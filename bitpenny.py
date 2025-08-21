import os
import time
import random
import sqlite3
import csv
import io
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from flask import Flask, jsonify, render_template, request, session, redirect, url_for, Response
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash

# --- Load .env early ---
from dotenv import load_dotenv
load_dotenv()

# Third-party
import stripe
try:
    import feedparser  # pip install feedparser
except Exception:
    feedparser = None
try:
    import pyotp  # pip install pyotp
except Exception:
    pyotp = None

from authlib.integrations.flask_client import OAuth

# ===================== App & Config =====================
app = Flask(__name__)

SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "http://localhost:5000").rstrip("/")
SECURE_COOKIES = SITE_BASE_URL.startswith("https://")

app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY", "dev-secret"),
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=SECURE_COOKIES,
)

DB_PATH = os.environ.get("DATABASE_URL", "sqlite:///bitpenny.db").replace("sqlite:///", "")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "admin-token")

# Stripe
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

print("GOOGLE_CLIENT_ID prefix:", (os.environ.get("GOOGLE_CLIENT_ID") or "")[:24])
print("SITE_BASE_URL:", SITE_BASE_URL)

# ===================== OAuth (Google OIDC) =====================
oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", "").strip(),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# ===================== DB Helpers =====================
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            totp_secret TEXT,
            twofa_enabled INTEGER NOT NULL DEFAULT 0
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            address TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,           -- BITP delta (+/-)
            kind TEXT NOT NULL,                -- CARD_DEPOSIT / DEPOSIT / WITHDRAW / TRADE_PNL / FEE
            meta TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,                -- INFO / DEPOSIT / TRADE / SYSTEM
            message TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    # Backfill missing columns for older DBs (SQLite ADD COLUMN is safe if not exists)
    try:
        cur.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cur.execute("ALTER TABLE users ADD COLUMN twofa_enabled INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    con.commit()
    con.close()

init_db()

def new_address() -> str:
    return "BITP-" + uuid4().hex[:20].upper()

def current_user_id():
    return session.get("uid")

def utcnow_iso():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

def add_notification(user_id: int, kind: str, message: str):
    con = db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO notifications(user_id, kind, message, created_at) VALUES(?,?,?,?)",
        (user_id, kind, message, utcnow_iso()),
    )
    con.commit()
    con.close()

def credit_user_bitp(user_id: int, amount_bitp: int, reason: str = "CARD_DEPOSIT", meta: str = "{\"via\":\"stripe\"}"):
    con = db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO ledger(user_id, amount, kind, meta, created_at) VALUES(?,?,?,?,?)",
        (user_id, amount_bitp, reason, meta, utcnow_iso()),
    )
    con.commit()
    con.close()

# ===================== Auth (Email/Password) =====================
@app.post("/auth/signup")
def signup():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    pw = data.get("password") or ""
    if not email or not pw:
        return jsonify({"error": "missing_email_or_password"}), 400

    con = db()
    cur = con.cursor()
    try:
        cur.execute(
            "INSERT INTO users(email, password_hash, created_at) VALUES(?,?,?)",
            (email, generate_password_hash(pw), utcnow_iso()),
        )
        uid = cur.lastrowid
        cur.execute(
            "INSERT INTO wallets(user_id, address, created_at) VALUES(?,?,?)",
            (uid, new_address(), utcnow_iso()),
        )
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
        return jsonify({"error": "email_exists"}), 409
    finally:
        con.close()

    session["uid"], session["email"] = uid, email
    add_notification(uid, "INFO", "Welcome to BITPenny 🎉 Your wallet is ready.")
    return jsonify({"ok": True})

@app.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    pw = data.get("password") or ""

    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, password_hash, twofa_enabled FROM users WHERE email=?", (email,))
    row = cur.fetchone()
    con.close()
    if not row or not check_password_hash(row["password_hash"], pw):
        return jsonify({"error": "invalid_credentials"}), 401

    # If 2FA enabled, require a code in a second step (simple flow)
    if int(row["twofa_enabled"] or 0) and not data.get("otp"):
        return jsonify({"error": "otp_required"}), 401

    if int(row["twofa_enabled"] or 0) and data.get("otp"):
        con = db()
        cur = con.cursor()
        cur.execute("SELECT totp_secret FROM users WHERE id=?", (row["id"],))
        srow = cur.fetchone()
        con.close()
        if not (pyotp and srow and srow["totp_secret"] and pyotp.TOTP(srow["totp_secret"]).verify(str(data.get("otp")))):
            return jsonify({"error": "invalid_otp"}), 401

    session["uid"], session["email"] = row["id"], email
    add_notification(row["id"], "INFO", "Signed in successfully.")
    return jsonify({"ok": True})

@app.post("/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})

# ===================== Auth (Google) =====================
@app.route("/auth/google")
def auth_google():
    redirect_uri = f"{SITE_BASE_URL}/auth/google/callback"
    session["oidc_nonce"] = uuid4().hex
    return google.authorize_redirect(redirect_uri, nonce=session["oidc_nonce"])

@app.route("/auth/google/callback")
def auth_google_callback():
    token = google.authorize_access_token()

    user_info = None
    try:
        nonce = session.pop("oidc_nonce", None)
        user_info = google.parse_id_token(token, nonce=nonce)
    except Exception:
        meta = google.load_server_metadata()
        userinfo_url = meta.get("userinfo_endpoint")
        if not userinfo_url:
            return redirect(url_for("index"))
        resp = google.get(userinfo_url)
        resp.raise_for_status()
        user_info = resp.json()

    email = (user_info.get("email") or "").lower()
    if not email:
        return redirect(url_for("index"))

    con = db()
    cur = con.cursor()
    cur.execute("SELECT id FROM users WHERE email=?", (email,))
    row = cur.fetchone()
    if row:
        uid = row["id"]
    else:
        cur.execute(
            "INSERT INTO users(email, password_hash, created_at) VALUES(?,?,?)",
            (email, generate_password_hash(uuid4().hex), utcnow_iso()),
        )
        uid = cur.lastrowid
        cur.execute(
            "INSERT INTO wallets(user_id, address, created_at) VALUES(?,?,?)",
            (uid, new_address(), utcnow_iso()),
        )
        con.commit()
        add_notification(uid, "INFO", "Welcome to BITPenny 🎉 Your wallet is ready.")
    con.close()

    session["uid"], session["email"] = uid, email
    add_notification(uid, "INFO", "Signed in via Google.")
    return redirect(url_for("wallet_page"))

# ===================== User / Wallet APIs =====================
@app.get("/me")
def me():
    if not current_user_id():
        return jsonify({"user": None})
    uid = current_user_id()
    con = db()
    cur = con.cursor()
    cur.execute("SELECT email, twofa_enabled FROM users WHERE id=?", (uid,))
    urow = cur.fetchone()
    cur.execute("SELECT address FROM wallets WHERE user_id=?", (uid,))
    wrow = cur.fetchone()
    con.close()
    return jsonify({"user": {"email": urow["email"], "address": wrow["address"], "twofa_enabled": int(urow["twofa_enabled"] or 0)}})

@app.get("/wallet/address")
def wallet_address():
    if not current_user_id():
        return jsonify({"error": "auth_required"}), 401
    uid = current_user_id()
    con = db()
    cur = con.cursor()
    cur.execute("SELECT address FROM wallets WHERE user_id=?", (uid,))
    row = cur.fetchone()
    con.close()
    return jsonify({"address": row["address"]})

@app.get("/balance")
def balance_self():
    if not current_user_id():
        return jsonify({"error": "auth_required"}), 401
    uid = current_user_id()
    con = db()
    cur = con.cursor()
    cur.execute("SELECT COALESCE(SUM(amount),0) AS bal FROM ledger WHERE user_id=?", (uid,))
    bal = cur.fetchone()["bal"] or 0
    con.close()
    return jsonify({"balance": int(bal)})

# ===================== Portfolio Timeseries (for growth chart) =====================
@app.get("/api/portfolio/timeseries")
def portfolio_ts():
    if not current_user_id():
        return jsonify({"error": "auth_required"}), 401
    uid = current_user_id()
    days = int(request.args.get("days", 30))
    start = datetime.utcnow() - timedelta(days=days)

    con = db()
    cur = con.cursor()
    cur.execute("SELECT amount, created_at FROM ledger WHERE user_id=? ORDER BY id ASC", (uid,))
    rows = cur.fetchall()
    con.close()

    points = []
    bal = 0
    by_day = {}
    for r in rows:
        amt = int(r["amount"])
        try:
            ts = datetime.fromisoformat(r["created_at"].replace("Z",""))
        except Exception:
            ts = datetime.utcnow()
        day = datetime(ts.year, ts.month, ts.day)
        by_day[day] = by_day.get(day, 0) + amt

    cursor = datetime(start.year, start.month, start.day)
    today = datetime.utcnow()
    while cursor <= today:
        bal += by_day.get(cursor, 0)
        points.append({
            "time": int(cursor.replace(tzinfo=timezone.utc).timestamp()),
            "bitp": bal,
            "usd": round(bal / 100.0, 2),  # 1 USD -> 100 BITP
        })
        cursor += timedelta(days=1)

    return jsonify({"series": points})

# ===================== Notifications APIs =====================
@app.get("/api/notifications")
def api_notifications():
    if not current_user_id():
        return jsonify({"error": "auth_required"}), 401
    uid = current_user_id()
    only_unread = (request.args.get("only_unread") == "1")
    con = db()
    cur = con.cursor()
    if only_unread:
        cur.execute(
            "SELECT id, kind, message, created_at FROM notifications WHERE user_id=? AND is_read=0 ORDER BY id DESC LIMIT 50",
            (uid,),
        )
    else:
        cur.execute(
            "SELECT id, kind, message, is_read, created_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100",
            (uid,),
        )
    items = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify({"items": items})

@app.post("/api/notifications/read")
def api_notifications_read():
    if not current_user_id():
        return jsonify({"error": "auth_required"}), 401
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []

    con = db()
    cur = con.cursor()
    if ids:
        qmarks = ",".join("?" * len(ids))
        cur.execute(f"UPDATE notifications SET is_read=1 WHERE user_id=? AND id IN ({qmarks})", [uid, *ids])
    else:
        cur.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (uid,))
    con.commit()
    con.close()
    return jsonify({"ok": True})

# ===================== News API (RSS, cached) =====================
NEWS_SOURCES = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
]
_NEWS_CACHE = {"items": [], "ts": 0}
_NEWS_TTL = 10 * 60  # 10 min

def _fetch_news():
    items = []
    if not feedparser:
        return [{"source":"News","title":"Crypto market overview","url":"https://coindesk.com/","published":utcnow_iso()}]
    for src_name, url in NEWS_SOURCES:
        try:
            d = feedparser.parse(url)
            for e in d.entries[:10]:
                items.append({
                    "source": src_name,
                    "title": getattr(e, "title", "Untitled"),
                    "url": getattr(e, "link", "#"),
                    "published": getattr(e, "published", utcnow_iso()),
                })
        except Exception:
            continue
    items.sort(key=lambda x: x["published"], reverse=True)
    return items[:30]

@app.get("/api/news")
def api_news():
    now = time.time()
    if now - _NEWS_CACHE["ts"] > _NEWS_TTL or not _NEWS_CACHE["items"]:
        _NEWS_CACHE["items"] = _fetch_news()
        _NEWS_CACHE["ts"] = now
    return jsonify({"items": _NEWS_CACHE["items"]})

# ===================== 2FA (TOTP) minimal APIs =====================
@app.post("/api/2fa/setup")
def api_2fa_setup():
    if not current_user_id():
        return jsonify({"error": "auth_required"}), 401
    if not pyotp:
        return jsonify({"error": "server_missing_pyotp"}), 500

    uid = current_user_id()
    secret = pyotp.random_base32()
    issuer = "BITPenny"
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET totp_secret=? WHERE id=?", (secret, uid))
    con.commit()
    con.close()

    email = session.get("email") or f"user{uid}@example.com"
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)
    return jsonify({"secret": secret, "otpauth_uri": uri})

@app.post("/api/2fa/enable")
def api_2fa_enable():
    if not current_user_id():
        return jsonify({"error": "auth_required"}), 401
    if not pyotp:
        return jsonify({"error": "server_missing_pyotp"}), 500

    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    code = str(data.get("otp") or "")
    con = db()
    cur = con.cursor()
    cur.execute("SELECT totp_secret FROM users WHERE id=?", (uid,))
    row = cur.fetchone()
    if not (row and row["totp_secret"]):
        con.close()
        return jsonify({"error": "no_secret"}), 400
    ok = pyotp.TOTP(row["totp_secret"]).verify(code)
    if not ok:
        con.close()
        return jsonify({"error": "invalid_otp"}), 400
    cur.execute("UPDATE users SET twofa_enabled=1 WHERE id=?", (uid,))
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.post("/api/2fa/disable")
def api_2fa_disable():
    if not current_user_id():
        return jsonify({"error": "auth_required"}), 401
    uid = current_user_id()
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET twofa_enabled=0, totp_secret=NULL WHERE id=?", (uid,))
    con.commit()
    con.close()
    return jsonify({"ok": True})

# ===================== Admin (sim deposit) =====================
@app.post("/admin/credit_deposit")
def admin_credit_deposit():
    if request.headers.get("X-Admin-Token") != ADMIN_TOKEN:
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    address = data.get("address")
    amount = int(data.get("amount") or 0)
    if not address or amount <= 0:
        return jsonify({"error": "invalid_params"}), 400

    con = db()
    cur = con.cursor()
    cur.execute("SELECT user_id FROM wallets WHERE address=?", (address,))
    row = cur.fetchone()
    if not row:
        con.close()
        return jsonify({"error": "address_not_found"}), 404
    uid = row["user_id"]
    cur.execute(
        "INSERT INTO ledger(user_id, amount, kind, meta, created_at) VALUES(?,?,?,?,?)",
        (uid, amount, "DEPOSIT", "{\"source\":\"simulated\"}", utcnow_iso()),
    )
    con.commit()
    con.close()
    add_notification(uid, "DEPOSIT", f"Deposit credited: +{amount} BITP")
    return jsonify({"ok": True})

# ===================== History APIs (JSON + CSV) =====================
@app.get("/api/history")
def api_history():
    if not current_user_id():
        return jsonify({"error": "auth_required"}), 401
    uid = current_user_id()
    kind = request.args.get("kind")  # optional filter
    con = db()
    cur = con.cursor()
    if kind:
        cur.execute(
            "SELECT id, amount, kind, meta, created_at FROM ledger WHERE user_id=? AND kind=? ORDER BY id DESC LIMIT 1000",
            (uid, kind),
        )
    else:
        cur.execute(
            "SELECT id, amount, kind, meta, created_at FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 1000",
            (uid,),
        )
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify({"items": rows})

@app.get("/api/history/csv")
def api_history_csv():
    if not current_user_id():
        return jsonify({"error": "auth_required"}), 401
    uid = current_user_id()

    con = db()
    cur = con.cursor()
    cur.execute(
        "SELECT id, amount, kind, meta, created_at FROM ledger WHERE user_id=? ORDER BY id DESC",
        (uid,),
    )
    rows = cur.fetchall()
    con.close()

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "amount_bitp", "kind", "meta", "created_at"])
    for r in rows:
        w.writerow([r["id"], r["amount"], r["kind"], r["meta"], r["created_at"]])
    data = out.getvalue()
    out.close()
    return Response(
        data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=bitpenny_history.csv"},
    )

# ===================== Minimal Market Sim (for Trade UI) =====================
MARKET = {"last": 1.00, "t0": time.time()}

def _nudge_price():
    # random walk
    MARKET["last"] = max(0.01, round(MARKET["last"] * (1 + random.uniform(-0.003, 0.003)), 4))

@app.get("/api/market/ticker")
def api_ticker():
    _nudge_price()
    last = MARKET["last"]
    return jsonify({"symbol": "BITP/USD", "last": last, "change24h": round(random.uniform(-5, 5), 2)})

@app.get("/api/market/orderbook")
def api_orderbook():
    _nudge_price()
    p = MARKET["last"]
    bids = [[round(p - i*0.002, 4), random.randint(10, 200)] for i in range(1, 11)]
    asks = [[round(p + i*0.002, 4), random.randint(10, 200)] for i in range(1, 11)]
    return jsonify({"bids": bids, "asks": asks})

@app.get("/api/market/trades")
def api_trades():
    _nudge_price()
    now = int(time.time())
    trades = [{"time": now - i*5, "price": round(MARKET["last"]*(1+random.uniform(-0.001,0.001)),4), "size": random.randint(1,50)} for i in range(30)]
    return jsonify({"trades": trades})

@app.get("/api/market/candles")
def api_candles():
    # simple synthetic candles
    limit = int(request.args.get("limit", 180))
    now = int(time.time())
    candles = []
    base = MARKET["last"]
    for i in range(limit):
        t = now - (limit - i) * 60
        o = round(base*(1+random.uniform(-0.002,0.002)),4)
        h = round(o*(1+random.uniform(0,0.004)),4)
        l = round(o*(1-random.uniform(0,0.004)),4)
        c = round(random.choice([h,l, o*(1+random.uniform(-0.001,0.001))]),4)
        v = random.randint(5,200)
        candles.append([t, o, h, l, c, v])
        base = c
    MARKET["last"] = base
    return jsonify({"candles": candles})

# ===================== Pages =====================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/wallet")
def wallet_page():
    if not current_user_id():
        return redirect(url_for("index"))
    return render_template("wallet.html")

# --- Extra pages ---
@app.route("/trade")
def trade_page():
    if not current_user_id():
        return redirect(url_for("index"))
    return render_template("trade.html")

@app.route("/history")
def history_page():
    if not current_user_id():
        return redirect(url_for("index"))
    return render_template("history.html")

@app.route("/profile")
def profile_page():
    if not current_user_id():
        return redirect(url_for("index"))
    return render_template("profile.html")

# ===================== Stripe: Checkout + Webhook =====================
@app.post("/stripe/create-checkout-session")
def stripe_create_checkout_session():
    if not current_user_id():
        return jsonify({"error": "auth_required"}), 401

    data = request.get_json(silent=True) or {}
    amount_usd = float(data.get("amount_usd") or 0)
    if amount_usd <= 0:
        return jsonify({"error": "invalid_amount"}), 400

    try:
        amount_cents = int(round(amount_usd * 100))
        session_obj = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "quantity": 1,
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount_cents,
                    "product_data": {"name": "BITP Deposit"},
                },
            }],
            success_url=f"{SITE_BASE_URL}/deposit/success?sid={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{SITE_BASE_URL}/deposit/cancel",
            client_reference_id=str(current_user_id()),
            metadata={"user_id": str(current_user_id())},
        )
        return jsonify({"id": session_obj.id, "pk": PUBLISHABLE_KEY})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/stripe/webhook")
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=sig_header, secret=WEBHOOK_SECRET
        )
    except Exception as e:
        return (f"Webhook error: {e}", 400)

    if event["type"] == "checkout.session.completed":
        sess = event["data"]["object"]
        user_id = int(sess.get("client_reference_id") or sess.get("metadata", {}).get("user_id") or 0)
        amount_total_cents = int(sess.get("amount_total") or 0)
        if user_id and amount_total_cents > 0:
            bitp_per_usd = 100
            usd = amount_total_cents / 100.0
            credit_amount = int(round(usd * bitp_per_usd))
            credit_user_bitp(user_id, credit_amount, reason="CARD_DEPOSIT")
            add_notification(user_id, "DEPOSIT", f"Card deposit succeeded: +{credit_amount} BITP")

    return ("OK", 200)

@app.get("/deposit/success")
def deposit_success():
    return "<html><body style='background:#0d0d0d;color:#e0ffe0;font-family:Segoe UI,sans-serif'><div style='display:grid;place-items:center;min-height:100vh'><div><h2>✅ Payment initiated</h2><p>We’ll credit your BITP after the webhook step.</p><a href='/wallet' style='color:#00ff99'>Back to wallet</a></div></div></body></html>"

@app.get("/deposit/cancel")
def deposit_cancel():
    return "<html><body style='background:#0d0d0d;color:#e0ffe0;font-family:Segoe UI,sans-serif'><div style='display:grid;place-items:center;min-height:100vh'><div><h2>Payment canceled</h2><a href='/wallet' style='color:#00ff99'>Back to wallet</a></div></div></body></html>"

# ===================== Run =====================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
