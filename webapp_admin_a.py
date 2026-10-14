"""Admin mini-app API, part A: me, overview, users, orders, receipts, tickets, reports."""
from __future__ import annotations

import logging
from datetime import timedelta

from flask import g, request

import database as db
from webapp_common import make_helpers

log = logging.getLogger(__name__)
PAID = "('completed','active','paid','processing')"


def _rows(sql, params=()):
    cur = db.get_connection().cursor()
    cur.execute(sql, params)
    return db._fetchall(cur)


def _one(sql, params=()):
    r = _rows(sql, params)
    return r[0] if r else {}


def _int(v, d=0):
    try:
        return int(v)
    except Exception:
        return d


def register(app, api):
    H = make_helpers(api)
    P = H.admin_perm

    def now():
        return api.now_tehran_naive()

    # ------------------------------------------------------------------ me
    @app.route("/api/admin/me", methods=["GET"])
    @P(None)
    def adm_me():
        u = g.tg_user
        name = ((u.get("first_name") or "") + " " + (u.get("last_name") or "")).strip() or u.get("username") or str(u.get("id"))
        return H.ok(name=name, is_main=H.is_main_admin(), sections=H.allowed_sections(), id=str(u.get("id")))

    # ------------------------------------------------------------ overview
    @app.route("/api/admin/overview", methods=["GET"])
    @P("stats")
    def adm_overview():
        today = now().date()
        t = today.isoformat()
        d14 = (today - timedelta(days=13)).isoformat()
        d7 = (today - timedelta(days=6)).isoformat()
        d30 = (today - timedelta(days=29)).isoformat()
        rev = lambda since: _int(_one(f"SELECT COALESCE(SUM(price),0) s FROM orders WHERE status IN {PAID} AND substr(created_at,1,10)>=?", (since,)).get("s"))
        series_rev = {r["d"]: _int(r["s"]) for r in _rows(f"SELECT substr(created_at,1,10) d, COALESCE(SUM(price),0) s FROM orders WHERE status IN {PAID} AND substr(created_at,1,10)>=? GROUP BY d", (d14,))}
        series_users = {r["d"]: _int(r["c"]) for r in _rows("SELECT substr(joined,1,10) d, COUNT(*) c FROM users WHERE substr(joined,1,10)>=? GROUP BY d", (d14,))}
        days = [(today - timedelta(days=13 - i)).isoformat() for i in range(14)]
        pend_receipts = _int(_one("SELECT COUNT(*) c FROM pending_receipts WHERE status='pending'").get("c"))
        data = dict(
            users=_int(_one("SELECT COUNT(*) c FROM users").get("c")),
            new_users_today=_int(_one("SELECT COUNT(*) c FROM users WHERE substr(joined,1,10)=?", (t,)).get("c")),
            customers=_int(db.count_customers()),
            active_users_30d=_int(db.count_active_users(30)),
            services=_int(_one("SELECT COUNT(*) c FROM configs WHERE deleted=0").get("c")),
            orders_today=_int(_one("SELECT COUNT(*) c FROM orders WHERE substr(created_at,1,10)=?", (t,)).get("c")),
            pending_orders=_int(_one("SELECT COUNT(*) c FROM orders WHERE status='pending'").get("c")),
            pending_receipts=pend_receipts,
            open_tickets=_int(db.count_tickets("open")),
            revenue_today=rev(t), revenue_7d=rev(d7), revenue_30d=rev(d30),
            revenue_total=_int(_one(f"SELECT COALESCE(SUM(price),0) s FROM orders WHERE status IN {PAID}").get("s")),
            wallets_total=_int(_one("SELECT COALESCE(SUM(wallet),0) s FROM users").get("s")),
            agents=len(db.get_all_agents()),
            orders_enabled=bool(db.is_orders_enabled()),
            series=[{"d": d, "revenue": series_rev.get(d, 0), "users": series_users.get(d, 0)} for d in days],
        )
        return H.ok(**data)

    @app.route("/api/admin/orders-toggle", methods=["POST"])
    @P("orders_toggle")
    def adm_orders_toggle():
        en = bool((request.get_json(silent=True) or {}).get("enabled"))
        db.set_orders_enabled(en)
        H.audit("orders_toggle", "orders", "on" if en else "off")
        return H.ok(enabled=en)

    # --------------------------------------------------------------- users
    def _user_row(u):
        return dict(id=int(u["id"]), telegram_id=str(u["telegram_id"]), name=u.get("name") or "", wallet=_int(u.get("wallet")),
                    total_purchase=_int(u.get("total_purchase")), joined=u.get("joined"), is_blocked=bool(u.get("is_blocked")))

    @app.route("/api/admin/users", methods=["GET"])
    @P("users")
    def adm_users():
        page, per, off = H.page_args(20)
        q = (request.args.get("q") or "").strip()
        flt = request.args.get("filter") or "all"
        if q:
            rows = db.search_users_multi(q, limit=30)
            return H.ok(items=[_user_row(u) for u in rows], total=len(rows), page=1, per=30)
        where = {"all": "1=1", "customers": "total_purchase>0", "blocked": "is_blocked=1", "wallet": "wallet>0", "new": "1=1"}.get(flt, "1=1")
        order = "total_purchase DESC" if flt == "customers" else "wallet DESC" if flt == "wallet" else "id DESC"
        total = _int(_one(f"SELECT COUNT(*) c FROM users WHERE {where}").get("c"))
        rows = _rows(f"SELECT * FROM users WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (per, off))
        return H.ok(items=[_user_row(u) for u in rows], total=total, page=page, per=per)

    def _get_user(tid):
        u = db.get_user(str(tid))
        return u

    @app.route("/api/admin/users/<tid>", methods=["GET"])
    @P("users")
    def adm_user_detail(tid):
        u = _get_user(tid)
        if not u:
            return H.fail("not_found", "کاربر یافت نشد", 404)
        uid = int(u["id"])
        cfgs = db.get_configs(uid, include_deleted=False)
        services = [dict(id=c["id"], plan=c.get("plan"), expiry=c.get("expiry"), type=c.get("type"), disabled=bool(c.get("sub_link_disabled"))) for c in cfgs]
        txs = db.get_transactions(uid, limit=15)
        orders = _rows("SELECT id, plan_name, order_type, price, status, created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 15", (uid,))
        agent = db.get_agent(u["telegram_id"])
        ref = None
        if u.get("referrer_id"):
            r = db.get_user_by_id(int(u["referrer_id"]))
            ref = {"telegram_id": r["telegram_id"], "name": r.get("name")} if r else None
        return H.ok(user=dict(_user_row(u), locked_wallet=_int(u.get("locked_wallet")), referral_wallet=_int(u.get("referral_wallet")),
                              invited_count=_int(u.get("invited_count")), successful_invites=_int(u.get("successful_invites")), invite_code=u.get("invite_code")),
                    services=services, transactions=[dict(type=t.get("type"), amount=_int(t.get("amount")), description=t.get("description"), created_at=t.get("created_at")) for t in txs],
                    orders=orders, agent=({"percent": _int(agent.get("vip_discount_percent"))} if agent else None), referrer=ref)

    @app.route("/api/admin/users/<tid>/wallet", methods=["POST"])
    @P("users")
    def adm_user_wallet(tid):
        b = request.get_json(silent=True) or {}
        amount = _int(b.get("amount"))
        note = (b.get("note") or "").strip()[:200]
        u = _get_user(tid)
        if not u:
            return H.fail("not_found", "کاربر یافت نشد", 404)
        if amount == 0 or abs(amount) > 1_000_000_000:
            return H.fail("invalid_amount", "مبلغ نامعتبر است")
        if amount < 0 and _int(u.get("wallet")) + amount < 0:
            return H.fail("insufficient", "موجودی کاربر برای این کسر کافی نیست")
        db.add_to_wallet(int(u["id"]), amount, note or ("شارژ توسط ادمین" if amount > 0 else "کسر توسط ادمین"), tx_type="admin_adjust")
        H.audit("wallet_adjust", u["telegram_id"], f"{amount} {note}")
        try:
            if amount > 0 and db.user_pref_enabled(user_id=u["id"], key="wallet_alerts"):
                api.tg_notify_user(u["telegram_id"], "notif_wallet_charge", f"✅ کیف پول شما {amount:,} تومان شارژ شد.")
        except Exception:
            pass
        return H.ok(wallet=_int((db.get_user_by_id(int(u["id"])) or {}).get("wallet")))

    @app.route("/api/admin/users/<tid>/block", methods=["POST"])
    @P("users")
    def adm_user_block(tid):
        u = _get_user(tid)
        if not u:
            return H.fail("not_found", "کاربر یافت نشد", 404)
        if str(u["telegram_id"]) == str(api.ADMIN_ID):
            return H.fail("forbidden", "مدیر اصلی قابل مسدود شدن نیست", 403)
        nb = not bool(u.get("is_blocked"))
        db.set_user_blocked(u["telegram_id"], nb)
        H.audit("block" if nb else "unblock", u["telegram_id"])
        return H.ok(is_blocked=nb)

    @app.route("/api/admin/users/<tid>/message", methods=["POST"])
    @P("users")
    def adm_user_message(tid):
        text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
        if not text:
            return H.fail("empty", "متن پیام خالی است")
        u = _get_user(tid)
        if not u:
            return H.fail("not_found", "کاربر یافت نشد", 404)
        sent = bool(api.tg_send_message(u["telegram_id"], text[:3500]))
        H.audit("message_user", u["telegram_id"], text[:80])
        return H.ok(sent=sent)

    # -------------------------------------------------------------- orders
    @app.route("/api/admin/orders", methods=["GET"])
    @P("requests")
    def adm_orders():
        page, per, off = H.page_args(20)
        st = request.args.get("status") or ""
        where, params = ("WHERE o.status=?", [st]) if st else ("", [])
        total = _int(_one(f"SELECT COUNT(*) c FROM orders o {where}", tuple(params)).get("c"))
        rows = _rows(f"SELECT o.id,o.user_id,u.name user_name,u.telegram_id,o.plan_name,o.order_type,o.price,o.status,o.created_at FROM orders o LEFT JOIN users u ON u.id=o.user_id {where} ORDER BY o.id DESC LIMIT ? OFFSET ?", tuple(params + [per, off]))
        counts = {r["status"]: _int(r["c"]) for r in _rows("SELECT status, COUNT(*) c FROM orders GROUP BY status")}
        return H.ok(items=rows, total=total, page=page, per=per, counts=counts)

    # ------------------------------------------------------------ receipts
    @app.route("/api/admin/receipts", methods=["GET"])
    @P("receipts")
    def adm_receipts():
        rows = _rows("""SELECT r.id,r.kind,r.telegram_id,r.label,r.amount,r.plan_key,r.discount_code,r.created_at,u.name user_name
                        FROM pending_receipts r LEFT JOIN users u ON u.telegram_id=r.telegram_id WHERE r.status='pending' ORDER BY r.id ASC LIMIT 100""")
        for r in rows:
            r["can_approve"] = r["kind"] == "charge"
        return H.ok(items=rows, bot_username=(api.bot_info.get("bot_username") if hasattr(api, "bot_info") else None) or db.get_setting("bot_username", ""))

    @app.route("/api/admin/receipts/<int:rid>/approve", methods=["POST"])
    @P("receipts")
    def adm_receipt_approve(rid):
        r = db.get_pending_receipt_by_id(rid)
        if not r or r.get("status") != "pending":
            return H.fail("not_found", "رسید پیدا نشد یا قبلاً بررسی شده", 404)
        if r.get("kind") != "charge":
            return H.fail("use_bot", "این رسید مربوط به ساخت سرویس است؛ تأیید آن را از داخل ربات انجام دهید.")
        amount = _int(r.get("amount"))
        try:
            done = db.approve_charge_receipt_atomic(str(r["telegram_id"]), amount)
        except Exception as e:
            log.exception("approve failed")
            return H.fail("error", str(e), 500)
        if not done:
            return H.fail("not_found", "رسید قبلاً بررسی شده", 409)
        H.audit("approve_receipt", r["telegram_id"], f"charge {amount}")
        try:
            api.tg_notify_user(r["telegram_id"], "notif_wallet_charge", f"✅ رسید شما تأیید و {amount:,} تومان به کیف پول اضافه شد.")
        except Exception:
            pass
        return H.ok()

    @app.route("/api/admin/receipts/<int:rid>/reject", methods=["POST"])
    @P("receipts")
    def adm_receipt_reject(rid):
        reason = ((request.get_json(silent=True) or {}).get("reason") or "").strip()[:300]
        r = db.get_pending_receipt_by_id(rid)
        if not r or r.get("status") != "pending":
            return H.fail("not_found", "رسید پیدا نشد یا قبلاً بررسی شده", 404)
        with db.transaction() as cur:
            cur.execute("UPDATE pending_receipts SET status='rejected' WHERE id=? AND status='pending'", (rid,))
        H.audit("reject_receipt", r["telegram_id"], f"{r.get('kind')} {r.get('amount')} {reason}")
        try:
            api.tg_send_message(r["telegram_id"], "❌ رسید پرداخت شما تأیید نشد." + (f"\nدلیل: {reason}" if reason else "") + "\nدر صورت نیاز با پشتیبانی در تماس باشید.")
        except Exception:
            pass
        return H.ok()

    # ------------------------------------------------------------- tickets
    @app.route("/api/admin/tickets", methods=["GET"])
    @P("tickets")
    def adm_tickets():
        page, per, off = H.page_args(20)
        st = request.args.get("status") or "open"
        rows = db.list_tickets(st, limit=per, offset=off)
        items = [dict(id=t["id"], subject=t.get("subject"), status=t.get("status"), updated_at=t.get("updated_at"), user_name=t.get("user_name"), telegram_id=t.get("user_telegram_id")) for t in rows]
        return H.ok(items=items, total=db.count_tickets(st), counts={s: db.count_tickets(s) for s in ("open", "answered", "closed")})

    @app.route("/api/admin/tickets/<int:tid>", methods=["GET"])
    @P("tickets")
    def adm_ticket(tid):
        t = db.get_ticket(tid)
        if not t:
            return H.fail("not_found", "تیکت پیدا نشد", 404)
        u = db.get_user_by_id(int(t["user_id"])) or {}
        return H.ok(ticket=dict(id=t["id"], subject=t.get("subject"), status=t.get("status"), created_at=t.get("created_at"), user_name=u.get("name"), telegram_id=u.get("telegram_id")),
                    messages=[dict(sender=m["sender"], text=m.get("text"), created_at=m.get("created_at")) for m in db.get_ticket_messages(tid)])

    @app.route("/api/admin/tickets/<int:tid>/reply", methods=["POST"])
    @P("tickets")
    def adm_ticket_reply(tid):
        text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
        t = db.get_ticket(tid)
        if not t:
            return H.fail("not_found", "تیکت پیدا نشد", 404)
        if not text:
            return H.fail("empty", "متن پاسخ خالی است")
        db.add_ticket_message(tid, "admin", text[:3500])
        u = db.get_user_by_id(int(t["user_id"])) or {}
        sent = False
        try:
            sent = bool(api.tg_send_message(u.get("telegram_id"), f"💬 پاسخ پشتیبانی به تیکت #{tid}:\n\n{text[:3500]}"))
        except Exception:
            pass
        H.audit("ticket_reply", str(tid))
        return H.ok(sent=sent)

    @app.route("/api/admin/tickets/<int:tid>/close", methods=["POST"])
    @P("tickets")
    def adm_ticket_close(tid):
        if not db.get_ticket(tid):
            return H.fail("not_found", "تیکت پیدا نشد", 404)
        db.set_ticket_status(tid, "closed")
        H.audit("ticket_close", str(tid))
        return H.ok()

    # ------------------------------------------------------------- reports
    @app.route("/api/admin/reports", methods=["GET"])
    @P("stats")
    def adm_reports():
        days = min(90, max(7, _int(request.args.get("days"), 30)))
        since = (now().date() - timedelta(days=days - 1)).isoformat()
        by_day = {r["d"]: r for r in _rows(f"SELECT substr(created_at,1,10) d, COALESCE(SUM(price),0) s, COUNT(*) c FROM orders WHERE status IN {PAID} AND substr(created_at,1,10)>=? GROUP BY d", (since,))}
        series = []
        for i in range(days):
            d = (now().date() - timedelta(days=days - 1 - i)).isoformat()
            r = by_day.get(d)
            series.append({"d": d, "revenue": _int(r["s"]) if r else 0, "orders": _int(r["c"]) if r else 0})
        top_plans = _rows(f"SELECT plan_name name, COUNT(*) c, COALESCE(SUM(price),0) s FROM orders WHERE status IN {PAID} AND substr(created_at,1,10)>=? GROUP BY plan_name ORDER BY s DESC LIMIT 8", (since,))
        top_buyers = _rows(f"SELECT u.name name, u.telegram_id telegram_id, COUNT(*) c, COALESCE(SUM(o.price),0) s FROM orders o JOIN users u ON u.id=o.user_id WHERE o.status IN {PAID} AND substr(o.created_at,1,10)>=? GROUP BY o.user_id ORDER BY s DESC LIMIT 8", (since,))
        by_type = _rows(f"SELECT order_type t, COUNT(*) c, COALESCE(SUM(price),0) s FROM orders WHERE status IN {PAID} AND substr(created_at,1,10)>=? GROUP BY order_type", (since,))
        new_users = _int(_one("SELECT COUNT(*) c FROM users WHERE substr(joined,1,10)>=?", (since,)).get("c"))
        return H.ok(days=days, series=series, top_plans=top_plans, top_buyers=top_buyers, by_type=by_type, new_users=new_users,
                    total_revenue=sum(s["revenue"] for s in series), total_orders=sum(s["orders"] for s in series))
