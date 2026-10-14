"""Reseller (agent) mini-app API: /api/reseller/*."""
from __future__ import annotations

import re
from datetime import timedelta

from flask import g, request

import database as db
from webapp_common import make_helpers

PAID = "('completed','active','paid','processing','fulfilled')"


def _rows(sql, params=()):
    cur = db.get_connection().cursor()
    cur.execute(sql, params)
    return db._fetchall(cur)


def _i(v, d=0):
    try:
        return int(v)
    except Exception:
        return d


def register(app, api):
    H = make_helpers(api)
    R = H.reseller_required

    def me():
        return db.get_user(str(g.tg_user["id"])) or {}

    def referral_link(u):
        bu = api.bot_info.get("bot_username") or db.get_setting("bot_username", "") or ""
        return f"https://t.me/{bu}?start={u.get('invite_code')}" if bu and u.get("invite_code") else ""

    def own_customer(tid):
        c = db.get_user(str(tid))
        if not c or _i(c.get("referrer_id")) != _i(me().get("id")):
            return None
        return c

    @app.route("/api/reseller/overview", methods=["GET"])
    @R
    def rs_overview():
        u = me(); a = g.agent; uid = _i(u.get("id"))
        today = api.now_tehran_naive().date()
        since30 = (today - timedelta(days=29)).isoformat()
        since14 = (today - timedelta(days=13)).isoformat()
        pct = _i(a.get("vip_discount_percent"))
        tot = _rows(f"SELECT COUNT(*) c, COALESCE(SUM(price),0) s FROM orders WHERE user_id=? AND status IN {PAID}", (uid,))[0]
        m = _rows(f"SELECT COUNT(*) c, COALESCE(SUM(price),0) s FROM orders WHERE user_id=? AND status IN {PAID} AND substr(created_at,1,10)>=?", (uid, since30))[0]
        by_day = {r["d"]: _i(r["s"]) for r in _rows(f"SELECT substr(created_at,1,10) d, SUM(price) s FROM orders WHERE user_id=? AND status IN {PAID} AND substr(created_at,1,10)>=? GROUP BY d", (uid, since14))}
        series = [{"d": (today - timedelta(days=13 - i)).isoformat(), "value": by_day.get((today - timedelta(days=13 - i)).isoformat(), 0)} for i in range(14)]
        cust = _rows("SELECT COUNT(*) c, COALESCE(SUM(total_purchase),0) s FROM users WHERE referrer_id=?", (uid,))[0]
        services = _rows("SELECT COUNT(*) c FROM configs WHERE user_id=? AND deleted=0", (uid,))[0]["c"]
        saved = int(round(_i(tot["s"]) * pct / (100 - pct))) if 0 < pct < 100 else 0
        return H.ok(
            name=u.get("name"), percent=pct, prefix=a.get("custom_prefix") or "", wallet=_i(u.get("wallet")), locked_wallet=_i(u.get("locked_wallet")),
            orders_total=_i(tot["c"]), spent_total=_i(tot["s"]), orders_30d=_i(m["c"]), spent_30d=_i(m["s"]), estimated_saving=saved,
            customers=_i(cust["c"]), customers_purchases=_i(cust["s"]), services=_i(services), referral_link=referral_link(u), series=series,
            categories=[c["name"] for c in db.get_vip_categories() if _i(c["id"]) in set(db.get_agent_category_ids(g.tg_user["id"]))] or None,
        )

    @app.route("/api/reseller/customers", methods=["GET"])
    @R
    def rs_customers():
        page, per, off = H.page_args(20)
        uid = _i(me().get("id"))
        q = (request.args.get("q") or "").strip()
        where, params = "referrer_id=?", [uid]
        if q:
            where += " AND (name LIKE ? OR telegram_id LIKE ?)"
            params += [f"%{q}%", f"%{q}%"]
        total = _rows(f"SELECT COUNT(*) c FROM users WHERE {where}", tuple(params))[0]["c"]
        rows = _rows(f"""SELECT u.telegram_id, u.name, u.joined, u.total_purchase,
                         (SELECT COUNT(*) FROM configs c WHERE c.user_id=u.id AND c.deleted=0) services
                         FROM users u WHERE {where.replace('referrer_id', 'u.referrer_id').replace('name', 'u.name').replace('telegram_id', 'u.telegram_id')}
                         ORDER BY u.id DESC LIMIT ? OFFSET ?""", tuple(params + [per, off]))
        return H.ok(items=[dict(telegram_id=r["telegram_id"], name=r["name"], joined=r["joined"], total_purchase=_i(r["total_purchase"]), services=_i(r["services"])) for r in rows], total=_i(total), page=page, per=per)

    @app.route("/api/reseller/customers/<tid>", methods=["GET"])
    @R
    def rs_customer(tid):
        c = own_customer(tid)
        if not c:
            return H.fail("not_found", "مشتری پیدا نشد", 404)
        cid = _i(c["id"])
        services = [dict(id=x["id"], plan=x.get("plan"), expiry=x.get("expiry"), disabled=bool(x.get("sub_link_disabled"))) for x in db.get_configs(cid, include_deleted=False)]
        orders = _rows("SELECT id, plan_name, order_type, price, status, created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 15", (cid,))
        return H.ok(customer=dict(telegram_id=c["telegram_id"], name=c["name"], joined=c["joined"], total_purchase=_i(c.get("total_purchase")), is_blocked=bool(c.get("is_blocked"))), services=services, orders=orders)

    @app.route("/api/reseller/customers/<tid>/message", methods=["POST"])
    @R
    def rs_customer_msg(tid):
        c = own_customer(tid)
        text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
        if not c:
            return H.fail("not_found", "مشتری پیدا نشد", 404)
        if not text:
            return H.fail("empty", "متن پیام خالی است")
        sent = bool(api.tg_send_message(c["telegram_id"], text[:3500]))
        return H.ok(sent=sent)

    @app.route("/api/reseller/sales", methods=["GET"])
    @R
    def rs_sales():
        page, per, off = H.page_args(20)
        uid = _i(me().get("id"))
        total = _rows("SELECT COUNT(*) c FROM orders WHERE user_id=?", (uid,))[0]["c"]
        rows = _rows("SELECT id, plan_name, order_type, price, status, created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?", (uid, per, off))
        # purchases made by referred customers
        cust = _rows(f"""SELECT o.id, o.plan_name, o.price, o.status, o.created_at, u.name user_name FROM orders o JOIN users u ON u.id=o.user_id
                         WHERE u.referrer_id=? AND o.status IN {PAID} ORDER BY o.id DESC LIMIT 20""", (uid,))
        return H.ok(items=rows, total=_i(total), page=page, per=per, customer_orders=cust)

    @app.route("/api/reseller/plans", methods=["GET"])
    @R
    def rs_plans():
        tid = g.tg_user["id"]
        out = []
        for c in db.get_vip_categories():
            plans = []
            for p in db.get_vip_plans(c["id"]):
                pct = db.agent_percent_for_plan(tid, p["plan_key"], c["id"])
                price = _i(p.get("price"))
                plans.append(dict(key=p["plan_key"], name=p["name"], price=price, agent_price=int(round(price * (1 - pct / 100))), percent=pct,
                                  days=_i(p.get("days")), volume_gb=_i(p.get("volume_gb"))))
            if plans:
                out.append(dict(id=c["id"], name=c["name"], plans=plans))
        return H.ok(categories=out)

    @app.route("/api/reseller/prefix", methods=["POST"])
    @R
    def rs_prefix():
        raw = str((request.get_json(silent=True) or {}).get("prefix") or "")
        p = re.sub(r"[^A-Za-z0-9_]", "", raw)[:12]
        if raw and not p:
            return H.fail("invalid", "فقط حروف انگلیسی، عدد و _ مجاز است")
        db.set_agent_prefix(g.tg_user["id"], p)
        return H.ok(prefix=p)

    @app.route("/api/reseller/wallet", methods=["GET"])
    @R
    def rs_wallet():
        u = me()
        txs = db.get_transactions(_i(u.get("id")), limit=30)
        return H.ok(wallet=_i(u.get("wallet")), locked_wallet=_i(u.get("locked_wallet")),
                    transactions=[dict(type=t.get("type"), amount=_i(t.get("amount")), description=t.get("description"), created_at=t.get("created_at")) for t in txs])
