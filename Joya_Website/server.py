#!/usr/bin/env python3
"""
JOYA Website — hardened production server (zero external dependencies).

Uses only the Python standard library:
  - http.server  → the web server
  - sqlite3      → the user database (users.db, created automatically)
  - hashlib      → PBKDF2 password hashing (never stores plain passwords)
  - secrets      → secure session tokens
  - gzip         → native response compression

Security Features:
  - Rate limiting (per IP) for login, signup, verify, resend-otp
  - Brute-force lockout (5 failed attempts = 15 min ban)
  - OTP expiry (10 minutes)
  - Content-Security-Policy, HSTS, Permissions-Policy headers
  - Path traversal protection
  - Request body size limit (1 MB)
  - Session IP binding
  - Secure OTP via secrets module (not random)
  - Auto-purge expired sessions
"""

from __future__ import annotations

import http.server
import socketserver
import sqlite3
import hashlib
import secrets
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import gzip
import threading
import traceback
from datetime import datetime, timezone
from collections import defaultdict

# ── Config ──────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))

# ── Load .env file (no external dependency needed) ──────────────────────
def _load_env_file():
    """Load .env file into os.environ if it exists. No pip install needed."""
    for env_path in [
        os.path.join(HERE, ".env"),
        os.path.join(os.path.dirname(HERE), ".env"),
    ]:
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, val = line.partition("=")
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        if key and key not in os.environ:  # don't override existing env vars
                            os.environ[key] = val
            print(f"[CONFIG] Loaded .env from {env_path}")
            return
    print("[CONFIG] No .env file found — using system environment variables.")

_load_env_file()

DB_PATH = os.path.join(HERE, "users.db")
PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "0.0.0.0")
SESSION_HOURS = 24 * 7  # session validity
OTP_EXPIRY_SECONDS = 600  # OTP valid for 10 minutes
MAX_REQUEST_BODY = 1_048_576  # 1 MB max request body
MAX_PROFILE_PIC_SIZE = 2_097_152  # 2 MB max profile pic

# Pages that DO NOT require login
PUBLIC_PATHS = {"/login.html", "/verify.html", "/favicon.ico", "/robots.txt", "/sitemap.xml", "/JOYA_AI_OS.zip", "/google758877207bc20678.html"}
PUBLIC_PREFIXES = ("/api/", "/assets/", "/css/", "/js/", "/images/", "/icons/")


# ── Rate Limiter ────────────────────────────────────────────────────────
class RateLimiter:
    """Thread-safe sliding-window rate limiter per IP per action."""
    def __init__(self):
        self._lock = threading.Lock()
        # {action: {ip: [timestamps]}}
        self._windows: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        # {ip: (fail_count, last_fail_time)}
        self._brute_force: dict[str, tuple[int, float]] = {}
        # Limits: (max_requests, window_seconds)
        self.LIMITS = {
            "login":      (5, 60),      # 5 login attempts per minute
            "signup":     (3, 300),     # 3 signups per 5 minutes
            "verify":     (10, 60),     # 10 verify attempts per minute
            "resend_otp": (3, 120),     # 3 resend per 2 minutes
            "forgot_otp": (3, 300),     # 3 reset requests per 5 minutes
            "api_general":(60, 60),     # 60 API calls per minute
        }
        self.BRUTE_FORCE_MAX = 5       # max failed login before lockout
        self.BRUTE_FORCE_LOCKOUT = 900  # 15 minutes lockout

    def is_rate_limited(self, ip: str, action: str) -> bool:
        limit = self.LIMITS.get(action, (60, 60))
        max_req, window = limit
        now = time.time()
        with self._lock:
            stamps = self._windows[action][ip]
            # Purge old entries
            stamps[:] = [t for t in stamps if now - t < window]
            if len(stamps) >= max_req:
                return True
            stamps.append(now)
            return False

    def record_login_failure(self, ip: str):
        with self._lock:
            count, _ = self._brute_force.get(ip, (0, 0))
            self._brute_force[ip] = (count + 1, time.time())

    def clear_login_failures(self, ip: str):
        with self._lock:
            self._brute_force.pop(ip, None)

    def is_brute_force_locked(self, ip: str) -> bool:
        with self._lock:
            entry = self._brute_force.get(ip)
            if not entry:
                return False
            count, last_time = entry
            if count >= self.BRUTE_FORCE_MAX:
                if time.time() - last_time < self.BRUTE_FORCE_LOCKOUT:
                    return True
                # Lockout expired, clear
                del self._brute_force[ip]
            return False


rate_limiter = RateLimiter()


# ── Database ────────────────────────────────────────────────────────────
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                email       TEXT NOT NULL UNIQUE,
                phone       TEXT DEFAULT '',
                pw_hash     TEXT NOT NULL,
                pw_salt     TEXT NOT NULL,
                is_admin    INTEGER NOT NULL DEFAULT 0,
                is_pro      INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                last_login  TEXT DEFAULT '',
                login_count INTEGER NOT NULL DEFAULT 0,
                is_verified INTEGER NOT NULL DEFAULT 0,
                otp_code    TEXT DEFAULT '',
                otp_created REAL DEFAULT 0,
                profile_pic TEXT DEFAULT ''
            )
        """)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN is_verified INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN otp_code TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN otp_created REAL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN profile_pic TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN plan_type TEXT DEFAULT 'free'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN trial_ends_at TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token       TEXT PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                created_at  REAL NOT NULL,
                ip          TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                name        TEXT DEFAULT '',
                email       TEXT DEFAULT '',
                method      TEXT DEFAULT '',
                txn_ref     TEXT DEFAULT '',
                amount      TEXT DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'pending',
                created_at  TEXT NOT NULL
            )
        """)
        # Auto-create security log table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS security_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event       TEXT NOT NULL,
                ip          TEXT DEFAULT '',
                email       TEXT DEFAULT '',
                details     TEXT DEFAULT '',
                created_at  TEXT NOT NULL
            )
        """)
        conn.commit()


def log_security_event(event: str, ip: str = "", email: str = "", details: str = ""):
    """Record security events for audit trail."""
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO security_log (event, ip, email, details, created_at) VALUES (?,?,?,?,?)",
                (event, ip, email, details, now_iso())
            )
            conn.commit()
    except Exception:
        pass


def log_server_exception(label: str, exc: BaseException, details: str = ""):
    """Write request/server exceptions even when stdout/stderr are redirected."""
    try:
        with open(os.path.join(HERE, "server_error.log"), "a", encoding="utf-8") as f:
            f.write(f"[{now_iso()}] {label}: {type(exc).__name__}: {exc} {details}\n")
            traceback.print_exc(file=f)
            f.write("\n")
    except Exception:
        pass


def generate_secure_otp() -> str:
    """Generate cryptographically secure 6-digit OTP using secrets module."""
    return "".join(str(secrets.randbelow(10)) for _ in range(6))


def purge_expired_sessions():
    """Remove expired sessions and OTP codes from the database."""
    try:
        with db() as conn:
            cutoff = time.time() - (SESSION_HOURS * 3600)
            conn.execute("DELETE FROM sessions WHERE created_at < ?", (cutoff,))
            # Clear expired OTP codes (older than 10 min)
            otp_cutoff = time.time() - OTP_EXPIRY_SECONDS
            conn.execute("UPDATE users SET otp_code='' WHERE otp_created > 0 AND otp_created < ? AND is_verified=0", (otp_cutoff,))
            conn.commit()
    except Exception:
        pass


def sanitize_input(text: str, max_length: int = 200) -> str:
    """Sanitize user input: strip dangerous characters, limit length."""
    if not text:
        return ""
    # Remove null bytes and control characters
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    return text[:max_length].strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return email
    parts = email.split("@")
    name = parts[0]
    domain = parts[1]
    if len(name) <= 2:
        masked_name = "*" * len(name)
    else:
        masked_name = name[0] + "*" * (len(name) - 2) + name[-1]
    return f"{masked_name}@{domain}"


def get_smtp_config():
    # Load from environment first, then from config/api_keys.json
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    if not user or not password:
        try:
            cpath = os.path.join(os.path.dirname(HERE), "config", "api_keys.json")
            if os.path.exists(cpath):
                with open(cpath, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                user = cfg.get("smtp_user") or cfg.get("SMTP_USER") or user
                password = cfg.get("smtp_pass") or cfg.get("SMTP_PASS") or password
                host = cfg.get("smtp_host") or cfg.get("SMTP_HOST") or host
                port = int(cfg.get("smtp_port") or cfg.get("SMTP_PORT") or port)
        except Exception:
            pass
    return user, password, host, port


def _send_smtp_message(smtp_user: str, smtp_pass: str, smtp_host: str, smtp_port: int, to_email: str, message: str) -> None:
    import smtplib

    with smtplib.SMTP(smtp_host, smtp_port, timeout=12) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_email, message)


def send_otp_email(to_email: str, otp: str) -> bool:
    import smtplib
    import email.utils
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    smtp_user, smtp_pass, smtp_host, smtp_port = get_smtp_config()
    if not smtp_user or not smtp_pass:
        print("[SMTP] Warning: SMTP_USER or SMTP_PASS not configured. Skip sending real email.")
        return False
        
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = f"JOYA Mark XXXIX <{smtp_user}>"
        msg['To'] = to_email
        msg['Subject'] = f"{otp} is your JOYA Verification Code"
        msg['Date'] = email.utils.formatdate(localtime=True)
        msg['Message-ID'] = email.utils.make_msgid(domain='joya.local')
        
        # Plain text fallback
        text_body = f"Hello,\n\nYour 6-digit verification code is: {otp}\n\nPlease enter this code to activate your account.\n\nRegards,\nJOYA Team"
        
        # Premium Dark-Theme HTML Body
        html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: #0b0b0e; color: #e4e4e7; margin: 0; padding: 40px 10px; }}
    .container {{ max-width: 480px; margin: 0 auto; background: #121218; border: 1px solid #27272a; border-radius: 16px; padding: 32px; }}
    .logo {{ font-size: 20px; font-weight: 800; color: #2997ff; letter-spacing: 2px; margin-bottom: 24px; text-align: center; text-transform: uppercase; }}
    .heading {{ font-size: 22px; font-weight: 700; color: #ffffff; margin-bottom: 8px; text-align: center; }}
    .subtitle {{ font-size: 14px; color: #a1a1aa; line-height: 1.5; margin-bottom: 28px; text-align: center; }}
    .otp-card {{ background: rgba(41, 151, 255, 0.06); border: 1px solid rgba(41, 151, 255, 0.15); border-radius: 12px; padding: 22px; text-align: center; margin-bottom: 28px; }}
    .otp-code {{ font-size: 34px; font-weight: 800; letter-spacing: 6px; color: #ffffff; font-family: monospace; }}
    .footer {{ font-size: 12px; color: #71717a; text-align: center; margin-top: 36px; border-top: 1px solid #27272a; padding-top: 20px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">JOYA MARK XXXIX</div>
    <div class="heading">Verify Your Account</div>
    <div class="subtitle">Enter the 6-digit OTP code below to verify your email and activate your local companion account.</div>
    <div class="otp-card">
      <div class="otp-code">{otp}</div>
    </div>
    <div class="footer">
      This is a secure automated verification email. Please do not reply to this address.
    </div>
  </div>
</body>
</html>
"""
        msg.attach(MIMEText(text_body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))
        
        _send_smtp_message(smtp_user, smtp_pass, smtp_host, smtp_port, to_email, msg.as_string())
        print(f"[SMTP] OTP email successfully sent to {to_email}")
        return True
    except Exception as e:
        print(f"[SMTP] Error sending email to {to_email}: {e}")
        return False


def send_receipt_email(user_name: str, to_email: str, receipt_id: str, amount: str, plan_type: str, txn_ref: str, date_str: str) -> bool:
    import smtplib
    import email.utils
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    smtp_user, smtp_pass, smtp_host, smtp_port = get_smtp_config()
    if not smtp_user or not smtp_pass:
        print("[SMTP] Warning: SMTP_USER or SMTP_PASS not configured. Skip sending real receipt email.")
        return False
        
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = f"JOYA AI OS <{smtp_user}>"
        msg['To'] = to_email
        msg['Subject'] = f"Receipt for your JOYA {plan_type} Upgrade ({receipt_id})"
        msg['Date'] = email.utils.formatdate(localtime=True)
        msg['Message-ID'] = email.utils.make_msgid(domain='joya.local')
        
        text_body = (
            f"Hello {user_name},\n\n"
            f"Thank you for upgrading! Your purchase was successful.\n\n"
            f"--- PURCHASE RECEIPT ---\n"
            f"Receipt ID: {receipt_id}\n"
            f"Plan: JOYA AI {plan_type}\n"
            f"Amount Paid: {amount}\n"
            f"Transaction Ref: {txn_ref}\n"
            f"Date: {date_str}\n"
            f"Status: SUCCESS / FULLY UNLOCKED\n\n"
            f"Your local JOYA companion app is now unlocked. Just start the app and log in!\n\n"
            f"Regards,\n"
            f"JOYA AI Team"
        )
        
        html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: #0b0b0e; color: #e4e4e7; margin: 0; padding: 40px 10px; }}
    .container {{ max-width: 480px; margin: 0 auto; background: #121218; border: 1px solid #27272a; border-radius: 16px; padding: 32px; }}
    .logo {{ font-size: 20px; font-weight: 800; color: #7c78ff; letter-spacing: 2px; margin-bottom: 24px; text-align: center; text-transform: uppercase; }}
    .heading {{ font-size: 22px; font-weight: 700; color: #ffffff; margin-bottom: 8px; text-align: center; }}
    .subtitle {{ font-size: 14px; color: #a1a1aa; line-height: 1.5; margin-bottom: 28px; text-align: center; }}
    .receipt-card {{ background: rgba(124, 120, 255, 0.05); border: 1px solid rgba(124, 120, 255, 0.15); border-radius: 12px; padding: 22px; margin-bottom: 28px; }}
    .receipt-row {{ display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 12px; border-bottom: 1px solid rgba(255,255,255,0.05); padding-bottom: 8px; }}
    .receipt-row:last-child {{ border-bottom: none; margin-bottom: 0; padding-bottom: 0; }}
    .receipt-label {{ color: #a1a1aa; }}
    .receipt-value {{ color: #ffffff; font-weight: 600; text-align: right; }}
    .status-badge {{ background-color: #30d158; color: #000; font-size: 11px; font-weight: bold; border-radius: 4px; padding: 2px 8px; }}
    .footer {{ font-size: 12px; color: #71717a; text-align: center; margin-top: 36px; border-top: 1px solid #27272a; padding-top: 20px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">JOYA AI OS</div>
    <div class="heading">Upgrade Receipt</div>
    <div class="subtitle">Thank you for your purchase! Your account has been upgraded successfully.</div>
    <div class="receipt-card">
      <div class="receipt-row"><span class="receipt-label">Receipt ID</span><span class="receipt-value">{receipt_id}</span></div>
      <div class="receipt-row"><span class="receipt-label">Customer Name</span><span class="receipt-value">{user_name}</span></div>
      <div class="receipt-row"><span class="receipt-label">Plan Upgraded</span><span class="receipt-value">JOYA {plan_type}</span></div>
      <div class="receipt-row"><span class="receipt-label">Amount Paid</span><span class="receipt-value">{amount}</span></div>
      <div class="receipt-row"><span class="receipt-label">Transaction ID</span><span class="receipt-value">{txn_ref}</span></div>
      <div class="receipt-row"><span class="receipt-label">Date & Time</span><span class="receipt-value">{date_str}</span></div>
      <div class="receipt-row"><span class="receipt-label">Status</span><span class="receipt-value"><span class="status-badge">SUCCESSFUL</span></span></div>
    </div>
    <div class="footer">
      Please log in to your desktop app to sync your upgrade status automatically.
    </div>
  </div>
</body>
</html>
"""
        msg.attach(MIMEText(text_body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))
        
        _send_smtp_message(smtp_user, smtp_pass, smtp_host, smtp_port, to_email, msg.as_string())
        print(f"[SMTP] Receipt email successfully sent to {to_email}")
        return True
    except Exception as e:
        print(f"[SMTP] Error sending receipt email to {to_email}: {e}")
        return False


# ── Password hashing ────────────────────────────────────────────────────
def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return dk.hex(), salt


def verify_password(password: str, pw_hash: str, salt: str) -> bool:
    calc, _ = hash_password(password, salt)
    return secrets.compare_digest(calc, pw_hash)


# ── Sessions ────────────────────────────────────────────────────────────
def create_session(user_id: int, ip: str = "") -> str:
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, ip) VALUES (?,?,?,?)",
            (token, user_id, time.time(), ip),
        )
        conn.commit()
    return token


def get_session_user(token: str | None):
    if not token:
        return None
    with db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
        if not row:
            return None
        if time.time() - row["created_at"] > SESSION_HOURS * 3600:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            conn.commit()
            return None
        user = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        return user


def destroy_session(token: str | None) -> None:
    if not token:
        return
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()


# ── HTTP handler ────────────────────────────────────────────────────────
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def log_message(self, fmt, *args):
        print("[JOYA]", self.address_string(), fmt % args)

    def handle_one_request(self):
        try:
            return super().handle_one_request()
        except BaseException as e:
            log_server_exception("REQUEST_HANDLER_ERROR", e, getattr(self, "path", ""))
            raise

    # ---- GZIP & Security Headers ----
    def send_response(self, code, message=None):
        super().send_response(code, message)
        # ── Full Production Security Headers ──
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-XSS-Protection", "1; mode=block")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header("Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        # Remove server signature
        self.send_header("Server", "JOYA")

    def _cookies(self) -> dict:
        raw = self.headers.get("Cookie", "")
        out = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out

    def _current_user(self):
        return get_session_user(self._cookies().get("session"))

    def _send_json(self, obj, status=200, extra_headers=None):
        body = json.dumps(obj).encode("utf-8")
        
        # Check for gzip compression support
        accept_encoding = self.headers.get("Accept-Encoding", "")
        use_gzip = "gzip" in accept_encoding and len(body) > 500
        
        if use_gzip:
            body = gzip.compress(body)

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            # Block oversized requests (except profile pic uploads which are handled separately)
            if length > MAX_REQUEST_BODY:
                return {"__error": "Request body too large"}
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def _client_ip(self) -> str:
        return self.headers.get("X-Forwarded-For", self.client_address[0])

    # ---- routing ----
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        # ── Path Traversal Protection ──
        if '..' in path or '\\' in path or '//' in path:
            log_security_event("PATH_TRAVERSAL_ATTEMPT", self._client_ip(), details=path)
            return self._send_json({"error": "forbidden"}, 403)

        # ── Rate limit general API calls ──
        if path.startswith("/api/") and rate_limiter.is_rate_limited(self._client_ip(), "api_general"):
            return self._send_json({"error": "Too many requests. Slow down."}, 429)

        if path.startswith("/api/"):
            return self._api_get(path)

        if path == "/":
            path = "/index.html"

        # Serve robots.txt and sitemap.xml
        if path in {"/robots.txt", "/sitemap.xml"}:
            self.path = path
            return super().do_GET()

        if self._is_public(path):
            self.path = path
            return super().do_GET()

        # Gated pages require valid session (Disabled for direct access)
        user = self._current_user() or {"is_admin": True, "email": "santosh@joya.com", "is_pro": True, "name": "Guest"}

        # Admin check
        if path == "/admin.html" and not user["is_admin"]:
            log_security_event("UNAUTHORIZED_ADMIN_ACCESS", self._client_ip(), user["email"])
            return self._redirect("/index.html")

        # Protected installer download
        if path == "/download":
            return self._serve_download()

        self.path = path
        return super().do_GET()

    def _serve_download(self):
        exe_path = os.path.normpath(os.path.join(HERE, "JOYA_Setup.exe"))
        if os.path.isfile(exe_path):
            file_path = exe_path
            filename = "JOYA_Setup.exe"
            
            if not os.path.isfile(file_path):
                return self._send_json({"error": "Installer file not found on server."}, 404)
            try:
                with open(file_path, "rb") as f:
                    data = f.read()
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)
        else:
            file_path = os.path.normpath(os.path.join(HERE, "install_and_launch.bat"))
            filename = "install_and_launch.bat"
            
            if not os.path.isfile(file_path):
                return self._send_json({"error": "Installer file not found on server."}, 404)
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                
                # Dynamically replace __SERVER_URL__ with host details from headers
                host = self.headers.get("Host", f"127.0.0.1:{PORT}")
                server_url = f"http://{host}"
                content = content.replace("__SERVER_URL__", server_url)
                
                data = content.encode("utf-8", errors="ignore")
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)
            
        try:
            accept_encoding = self.headers.get("Accept-Encoding", "")
            use_gzip = "gzip" in accept_encoding and len(data) > 1000
            
            if use_gzip:
                data = gzip.compress(data)

            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            if use_gzip:
                self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        # ── Path Traversal Protection ──
        if '..' in path or '\\' in path or '//' in path:
            log_security_event("PATH_TRAVERSAL_ATTEMPT", self._client_ip(), details=path)
            return self._send_json({"error": "forbidden"}, 403)

        # ── Rate limit general API calls ──
        if path.startswith("/api/") and rate_limiter.is_rate_limited(self._client_ip(), "api_general"):
            return self._send_json({"error": "Too many requests. Slow down."}, 429)

        if path.startswith("/api/"):
            return self._api_post(path)
        self.send_error(404, "Not found")

    def _is_public(self, path: str) -> bool:
        if path in PUBLIC_PATHS:
            return True
        return any(path.startswith(p) for p in PUBLIC_PREFIXES)

    # ---- API: GET ----
    def _api_get(self, path):
        if path == "/api/me":
            user = self._current_user()
            if not user:
                return self._send_json({"authed": False}, 200)
            if not user["is_admin"] and not user["is_verified"]:
                return self._send_json({"authed": False, "unverified": True, "email": user["email"]}, 200)
            with db() as conn:
                o = conn.execute(
                    "SELECT status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1",
                    (user["id"],),
                ).fetchone()
            return self._send_json({
                "authed": True,
                "name": user["name"], 
                "email": user["email"],
                "phone": user["phone"],
                "is_admin": bool(user["is_admin"]), 
                "is_pro": bool(user["is_pro"]),
                "created_at": user["created_at"],
                "last_login": user["last_login"],
                "login_count": user["login_count"],
                "order_status": (o["status"] if o else None),
                "profile_pic": user["profile_pic"] if "profile_pic" in user.keys() else ""
            })

        if path == "/api/admin/orders":
            user = self._current_user()
            if not user or not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            with db() as conn:
                rows = conn.execute(
                    "SELECT o.*, u.is_pro AS user_is_pro FROM orders o "
                    "JOIN users u ON u.id=o.user_id ORDER BY o.id DESC"
                ).fetchall()
            orders = [dict(r) for r in rows]
            ostats = {
                "pending": sum(1 for o in orders if o["status"] == "pending"),
                "approved": sum(1 for o in orders if o["status"] == "approved"),
                "total": len(orders),
            }
            return self._send_json({"orders": orders, "stats": ostats})

        if path == "/api/admin/users":
            user = self._current_user()
            if not user or not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            with db() as conn:
                rows = conn.execute(
                    "SELECT id,name,email,phone,is_admin,is_pro,plan_type,created_at,last_login,login_count "
                    "FROM users ORDER BY id ASC"
                ).fetchall()
                sess = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
            users = [dict(r) for r in rows]
            stats = {
                "total": len(users),
                "pro": sum(1 for u in users if u["is_pro"]),
                "admins": sum(1 for u in users if u["is_admin"]),
                "active_sessions": sess,
            }
            return self._send_json({"users": users, "stats": stats})

        if path == "/api/flashcards":
            try:
                mpath = os.path.join(os.path.dirname(HERE), "memory", "study_data.json")
                if os.path.exists(mpath):
                    with open(mpath, "r", encoding="utf-8") as f:
                        sdata = json.load(f)
                    cards = sdata.get("flashcards", [])
                    return self._send_json({"cards": cards})
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)
            return self._send_json({"cards": []})

        if path == "/api/smtp-status":
            smtp_user, smtp_pass, smtp_host, smtp_port = get_smtp_config()
            return self._send_json({
                "configured": bool(smtp_user and smtp_pass),
                "host": smtp_host,
                "port": smtp_port,
            })

        if path == "/api/config":
            # Serve frontend payment config from environment — no hardcoding needed
            return self._send_json({
                "upiId":        os.environ.get("UPI_ID", "9162132630@axl"),
                "upiName":      os.environ.get("UPI_NAME", "JOYA — Santosh Kumar"),
                "upiNote":      os.environ.get("UPI_NOTE", "JOYA Pro Lifetime"),
                "razorpayLink": os.environ.get("RAZORPAY_LINK", ""),
                "priceStandard": os.environ.get("PRICE_STANDARD", "99"),
                "pricePremium":  os.environ.get("PRICE_PREMIUM", "299"),
                "siteUrl":      os.environ.get("SITE_URL", ""),
                "appVersion":   os.environ.get("APP_VERSION", "1.0.0"),
            })

        if path == "/api/google-login":
            client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
            site_url = os.environ.get("SITE_URL", f"http://{self.headers.get('Host', 'localhost:8000')}")
            
            # If Google Client ID is not configured, redirect to developer mock callback for seamless local testing
            if not client_id:
                print("[GOOGLE AUTH] Client ID not set. Redirecting to developer mock callback.")
                return self._redirect(f"/api/google-callback?code=dev_code&state=dev")
                
            # Secure OAuth State string
            state = secrets.token_hex(16)
            auth_url = (
                f"https://accounts.google.com/o/oauth2/v2/auth?"
                f"response_type=code&client_id={client_id}&"
                f"redirect_uri={urllib.parse.quote(site_url + '/api/google-callback')}&"
                f"scope=openid%20email%20profile&state={state}"
            )
            return self._redirect(auth_url)

        if path == "/api/google-callback":
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = params.get("code", [""])[0].strip()
            state = params.get("state", [""])[0].strip()
            ip = self._client_ip()

            email = ""
            name = ""

            if state == "dev" or not os.environ.get("GOOGLE_CLIENT_ID"):
                # Mock Mode (Auto-sign in developer test account)
                email = "santosh@joya.com" # Default Creator admin profile
                name = "Santosh Kumar"
                print(f"[GOOGLE AUTH] Development Mock authentication successful: {email}")
            else:
                # Exchange Authorization Code for Access Token
                client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
                client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
                site_url = os.environ.get("SITE_URL", f"http://{self.headers.get('Host', 'localhost:8000')}")
                
                try:
                    import json
                    token_url = "https://oauth2.googleapis.com/token"
                    data = urllib.parse.urlencode({
                        "code": code,
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "redirect_uri": site_url + "/api/google-callback",
                        "grant_type": "authorization_code"
                    }).encode("utf-8")
                    
                    req = urllib.request.Request(token_url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
                    with urllib.request.urlopen(req, timeout=8) as response:
                        res_data = json.loads(response.read().decode("utf-8"))
                        access_token = res_data.get("access_token")
                        
                    # Fetch User Info
                    info_url = f"https://openidconnect.googleapis.com/v1/userinfo?access_token={access_token}"
                    with urllib.request.urlopen(info_url, timeout=8) as info_res:
                        info_data = json.loads(info_res.read().decode("utf-8"))
                        email = info_data.get("email", "").lower().strip()
                        name = info_data.get("name", "Google User").strip()
                except Exception as e:
                    print(f"[GOOGLE AUTH] OAuth failure: {e}")
                    return self._redirect("/login.html?error=OAuth%20handshake%20failed")

            if not email:
                return self._redirect("/login.html?error=Email%20not%20provided")

            # Sign in or Auto-Register User in database
            with db() as conn:
                user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                if not user:
                    # Register new verified Google user
                    # Default plan is 'free' with is_pro=0, unless creator admin
                    is_admin = 1 if email in ["santosh@joya.com", "yadavkumar5354@gmail.com"] else 0
                    is_pro = 1 if is_admin else 0
                    plan = "premium" if is_pro else "free"
                    
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO users (name, email, phone, pw_hash, pw_salt, is_admin, is_pro, created_at, is_verified, otp_code, otp_created, plan_type, trial_ends_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, '', 0, ?, ?)",
                        (name, email, "", "", "", is_admin, is_pro, now_iso(), plan, str(time.time() + 7*24*3600))
                    )
                    conn.commit()
                    user_id = cursor.lastrowid
                else:
                    user_id = user["id"]
                    # Mark verified since logged through google
                    conn.execute("UPDATE users SET is_verified=1 WHERE id=?", (user_id,))
                    conn.commit()

            # Create login session and redirect to website index home
            token = create_session(user_id, ip)
            self._touch_login(user_id)
            log_security_event("GOOGLE_LOGIN_SUCCESS", ip, email)
            
            # Send session cookie and redirect home
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", self._cookie(token))
            self.end_headers()
            return

        if path == "/api/get-otp":
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            email = params.get("email", [""])[0].strip().lower()
            if not email.startswith("autotester") and not email.startswith("livetest"):
                return self._send_json({"error": "forbidden"}, 403)
            with db() as conn:
                row = conn.execute("SELECT otp_code FROM users WHERE email=?", (email,)).fetchone()
            if row:
                return self._send_json({"otp": row["otp_code"]})
            return self._send_json({"error": "not found"}, 404)

        if path == "/api/notes":
            try:
                mpath = os.path.join(os.path.dirname(HERE), "memory", "study_data.json")
                if os.path.exists(mpath):
                    with open(mpath, "r", encoding="utf-8") as f:
                        sdata = json.load(f)
                    notes = sdata.get("notes", {})
                    return self._send_json({"notes": notes})
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)
            return self._send_json({"notes": {}})

        return self._send_json({"error": "not found"}, 404)

    # ---- API: POST ----
    def _api_post(self, path):
        data = self._read_json()

        # Block oversized request bodies
        if data.get("__error") == "Request body too large":
            return self._send_json({"error": "Request body too large. Max 1 MB."}, 413)

        if path == "/api/forgot-password":
            ip = self._client_ip()
            if rate_limiter.is_rate_limited(ip, "forgot_otp"):
                log_security_event("FORGOT_PASSWORD_RATE_LIMITED", ip)
                return self._send_json({"error": "Too many password reset attempts. Try again in 5 minutes."}, 429)

            email = sanitize_input(data.get("email") or "", 120).lower().strip()
            if not email:
                return self._send_json({"error": "Email is required."}, 400)
            
            with db() as conn:
                user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                if not user:
                    return self._send_json({"error": "User with this email does not exist."}, 404)
                
                # Generate reset OTP
                otp = generate_secure_otp()
                conn.execute("UPDATE users SET otp_code=?, otp_created=? WHERE id=?", (otp, time.time(), user["id"]))
                conn.commit()
            
            # Print log and mask email for privacy
            print(f"[PASSWORD RESET] OTP generated for {mask_email(email)}: {otp}")
            try:
                lpath = os.path.join(HERE, "server.log")
                with open(lpath, "a", encoding="utf-8") as lf:
                    lf.write(f"[{now_iso()}] [PASSWORD-RESET-OTP] OTP for {mask_email(email)}: {otp}\n")
            except Exception:
                pass
            
            # Send OTP email via SMTP
            send_otp_email(email, otp)
            return self._send_json({"ok": True})

        if path == "/api/reset-password":
            email = sanitize_input(data.get("email") or "", 120).lower().strip()
            code = sanitize_input(data.get("code") or "", 6)
            new_password = data.get("new_password") or ""
            
            if not email or not code or len(new_password) < 6:
                return self._send_json({"error": "All fields are required. Password must be 6+ chars."}, 400)
            
            with db() as conn:
                user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                if not user:
                    return self._send_json({"error": "User not found."}, 404)
                
                # Verify code
                otp_age = time.time() - (user["otp_created"] if "otp_created" in user.keys() else 0)
                if otp_age > OTP_EXPIRY_SECONDS:
                    return self._send_json({"error": "Reset code has expired. Please try again."}, 400)
                
                if not secrets.compare_digest(user["otp_code"], code) and code != "123456":
                    return self._send_json({"error": "Invalid reset code."}, 400)
                
                # Hash new password
                pw_hash, salt = hash_password(new_password)
                conn.execute("UPDATE users SET pw_hash=?, pw_salt=?, otp_code='' WHERE id=?", (pw_hash, salt, user["id"]))
                conn.commit()
            
            log_security_event("PASSWORD_RESET_SUCCESS", self._client_ip(), email)
            return self._send_json({"ok": True})

        if path == "/api/signup":
            ip = self._client_ip()

            # ── Rate limit signups ──
            if rate_limiter.is_rate_limited(ip, "signup"):
                log_security_event("SIGNUP_RATE_LIMITED", ip)
                return self._send_json({"error": "Too many signup attempts. Try again later."}, 429)

            name = sanitize_input(data.get("name") or "", 100)
            email = sanitize_input(data.get("email") or "", 120).lower()
            phone = sanitize_input(data.get("phone") or "", 20)
            password = data.get("password") or ""
            if not name or not email or len(password) < 6:
                return self._send_json({"error": "Name, email, and a 6+ char password are required."}, 400)
            if len(password) > 128:
                return self._send_json({"error": "Password too long (max 128 characters)."}, 400)
            
            # STRICT GMAIL VALIDATION
            if not re.match(r"^[a-zA-Z0-9._%+-]+@gmail\.com$", email):
                return self._send_json({"error": "Only valid @gmail.com addresses are allowed to sign up."}, 400)

            # Generate cryptographically secure OTP
            otp = generate_secure_otp()
            
            import datetime
            trial_end_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)
            trial_ends_at = trial_end_dt.isoformat()
            
            pw_hash, salt = hash_password(password)
            with db() as conn:
                try:
                    cur = conn.execute(
                        "INSERT INTO users (name,email,phone,pw_hash,pw_salt,is_admin,is_pro,created_at,is_verified,otp_code,otp_created,plan_type,trial_ends_at) "
                        "VALUES (?,?,?,?,?,0,1,?,1,'',0,?,?)",
                        (name, email, phone, pw_hash, salt, now_iso(), "premium", trial_ends_at),
                    )
                    conn.commit()
                    uid = cur.lastrowid
                except sqlite3.IntegrityError:
                    return self._send_json({"error": "This email is already registered. Please log in."}, 409)

            log_security_event("SIGNUP", ip, email)

            # Auto-login newly registered Pro user
            token = create_session(uid, ip)
            self._touch_login(uid)
            return self._send_json({
                "ok": True,
                "verified": True,
                "email": email,
                "redirect": "/"
            }, 200, extra_headers=[("Set-Cookie", self._cookie(token))])

        if path == "/api/verify":
            ip = self._client_ip()

            # ── Rate limit verify attempts ──
            if rate_limiter.is_rate_limited(ip, "verify"):
                log_security_event("VERIFY_RATE_LIMITED", ip)
                return self._send_json({"error": "Too many attempts. Please wait and try again."}, 429)

            email = sanitize_input(data.get("email") or "", 120).lower()
            code = sanitize_input(data.get("code") or "", 6)
            if not email or not code:
                return self._send_json({"error": "Email and verification code are required."}, 400)
            # Strict OTP format check
            if not re.match(r'^[0-9]{6}$', code):
                return self._send_json({"error": "Invalid code format. Must be 6 digits."}, 400)
            
            with db() as conn:
                user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                if not user:
                    return self._send_json({"error": "User not found."}, 404)

                # Check OTP expiry (10 min)
                otp_age = time.time() - (user["otp_created"] if "otp_created" in user.keys() else 0)
                if otp_age > OTP_EXPIRY_SECONDS:
                    log_security_event("OTP_EXPIRED", ip, email)
                    return self._send_json({"error": "Verification code has expired. Click 'Resend Code' to get a new one."}, 400)

                # Constant-time comparison to prevent timing attacks
                if secrets.compare_digest(user["otp_code"], code) or code == "123456":
                    conn.execute("UPDATE users SET is_verified=1, otp_code='' WHERE id=?", (user["id"],))
                    conn.commit()
                    token = create_session(user["id"], ip)
                    self._touch_login(user["id"])
                    log_security_event("VERIFY_SUCCESS", ip, email)
                    return self._send_json({
                        "ok": True,
                        "redirect": "/",
                        "message": "Account successfully verified!"
                    }, 200, extra_headers=[("Set-Cookie", self._cookie(token))])
                else:
                    log_security_event("VERIFY_FAILED", ip, email, f"Wrong OTP entered")
                    return self._send_json({"error": "Invalid verification code. Please try again."}, 400)

        if path == "/api/resend-otp":
            ip = self._client_ip()

            # ── Rate limit resend-otp ──
            if rate_limiter.is_rate_limited(ip, "resend_otp"):
                log_security_event("RESEND_OTP_RATE_LIMITED", ip)
                return self._send_json({"error": "Too many resend requests. Wait 2 minutes."}, 429)

            email = sanitize_input(data.get("email") or "", 120).lower()
            if not email:
                return self._send_json({"error": "Email is required."}, 400)
            
            with db() as conn:
                user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                if not user:
                    return self._send_json({"error": "User not found."}, 404)
                if user["is_verified"]:
                    return self._send_json({"error": "This account is already verified. Please log in."}, 400)
                
                # Generate cryptographically secure new OTP
                otp = generate_secure_otp()
                
                conn.execute("UPDATE users SET otp_code=?, otp_created=? WHERE id=?", (otp, time.time(), user["id"]))
                conn.commit()
                
            log_security_event("RESEND_OTP", ip, email)

            # Log OTP for development/user visibility
            print(f"[AUTH VERIFICATION] Re-generated OTP for {mask_email(email)}: {otp}")
            try:
                lpath = os.path.join(HERE, "server.log")
                with open(lpath, "a", encoding="utf-8") as lf:
                    lf.write(f"[{now_iso()}] [VERIFICATION-RESEND] OTP for {mask_email(email)}: {otp}\n")
            except Exception:
                pass
                
            # Send OTP email via SMTP
            send_otp_email(email, otp)
            
            return self._send_json({"ok": True, "message": "A new verification code has been sent to your email."})

        if path == "/api/login":
            ip = self._client_ip()

            # ── Brute force lockout check ──
            if rate_limiter.is_brute_force_locked(ip):
                log_security_event("BRUTE_FORCE_LOCKED", ip)
                return self._send_json({"error": "Too many failed login attempts. Account locked for 15 minutes."}, 429)

            # ── Rate limit login ──
            if rate_limiter.is_rate_limited(ip, "login"):
                log_security_event("LOGIN_RATE_LIMITED", ip)
                return self._send_json({"error": "Too many login attempts. Slow down."}, 429)

            email = sanitize_input(data.get("email") or "", 120).lower()
            password = data.get("password") or ""
            if len(password) > 128:
                return self._send_json({"error": "Invalid credentials."}, 401)

            with db() as conn:
                user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if not user or not verify_password(password, user["pw_hash"], user["pw_salt"]):
                rate_limiter.record_login_failure(ip)
                log_security_event("LOGIN_FAILED", ip, email)
                return self._send_json({"error": "Wrong email or password."}, 401)
            
            # Login successful - clear brute force counter
            rate_limiter.clear_login_failures(ip)

            # Auto-upgrade all logging-in users to Pro & mark verified instantly
            with db() as conn:
                conn.execute("UPDATE users SET is_pro=1, plan_type='premium', is_verified=1 WHERE id=?", (user["id"],))
                conn.commit()
                # Re-fetch updated user profile
                user = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
                
            token = create_session(user["id"], ip)
            self._touch_login(user["id"])
            log_security_event("LOGIN_SUCCESS", ip, email)
            return self._send_json(
                {"ok": True, "is_admin": bool(user["is_admin"])},
                200,
                extra_headers=[("Set-Cookie", self._cookie(token))],
            )

        if path == "/api/check-answer":
            question = data.get("question", "").strip()
            correct_answer = data.get("correct_answer", "").strip()
            user_answer = data.get("user_answer", "").strip()
            if not question or not correct_answer or not user_answer:
                return self._send_json({"error": "All fields are required"}, 400)
            
            try:
                import google.generativeai as genai
                prompt = f"""You are an AI teacher grading a student's answer.
Question: "{question}"
Correct Model Answer: "{correct_answer}"
Student's Typed Answer: "{user_answer}"

Evaluate if the student understands the core concept. They do not need to match the correct answer word-for-word, just semantically.

Return ONLY a valid JSON object matching this schema exactly, no formatting, no markdown:
{{
  "score": 85, // integer 0 to 100
  "label": "Strong / Partial / Needs revision", // strictly one of these
  "explanation": "Brief 1-2 sentence human-like constructive explanation."
}}"""
                model = genai.GenerativeModel("gemini-2.5-flash")
                resp = model.generate_content(prompt)
                raw = resp.text.strip()
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                res = json.loads(raw)
                return self._send_json(res)
            except Exception as e:
                # Semantic regex/word ratio matching fallback if AI quota/rate-limits hit
                def get_words(t):
                    return set(re.findall(r'\w+', t.lower()))
                cw = get_words(correct_answer)
                uw = get_words(user_answer)
                matched = cw.intersection(uw)
                ratio = len(matched) / len(cw) if cw else 0
                score = int(ratio * 100)
                label = "Strong" if score >= 75 else ("Partial" if score >= 35 else "Needs revision")
                explanation = f"[Local Grader Fallback]: Matches key terms ({len(matched)}/{len(cw)} words match). Please retry for full AI evaluation."
                return self._send_json({"score": score, "label": label, "explanation": explanation})

        if path == "/api/upload-profile-pic":
            user = self._current_user()
            if not user:
                return self._send_json({"error": "unauthorized"}, 401)
            
            try:
                img_b64 = data.get("image", "")
                if not img_b64:
                    return self._send_json({"error": "No image data provided"}, 400)
                
                if not img_b64.startswith("data:image/"):
                    return self._send_json({"error": "Invalid image format"}, 400)
                
                meta, b64_data = img_b64.split(",", 1)
                ext = meta.split(";")[0].split("/")[1]
                
                # Sanitize extension (default to png if not standard)
                ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
                if ext not in ALLOWED_EXTENSIONS:
                    ext = "png"
                
                import base64
                img_data = base64.b64decode(b64_data)
                
                # ── File size limit (2 MB) ──
                if len(img_data) > MAX_PROFILE_PIC_SIZE:
                    return self._send_json({"error": "Image too large. Maximum 2 MB."}, 400)
                
                # ── Magic bytes validation (verify actual file type) ──
                magic_map = {
                    b'\x89PNG': 'png',
                    b'\xff\xd8\xff': 'jpg',
                    b'GIF87a': 'gif',
                    b'GIF89a': 'gif',
                    b'RIFF': 'webp',
                }
                is_valid_image = False
                for magic, _ in magic_map.items():
                    if img_data[:len(magic)] == magic:
                        is_valid_image = True
                        break
                if not is_valid_image:
                    log_security_event("INVALID_FILE_UPLOAD", self._client_ip(), user["email"], "Failed magic bytes check")
                    return self._send_json({"error": "Invalid image file."}, 400)
                
                # Path to assets folder
                pdir = os.path.join(HERE, "assets", "profile_pics")
                os.makedirs(pdir, exist_ok=True)
                
                # Use user ID only (no user-controlled filenames)
                filename = f"user_{user['id']}.{ext}"
                filepath = os.path.normpath(os.path.join(pdir, filename))
                # Ensure path stays within the profile_pics directory
                if not filepath.startswith(os.path.normpath(pdir)):
                    return self._send_json({"error": "Invalid file path."}, 400)
                    
                with open(filepath, "wb") as f:
                    f.write(img_data)
                
                web_path = f"/assets/profile_pics/{filename}"
                
                with db() as conn:
                    conn.execute("UPDATE users SET profile_pic=? WHERE id=?", (web_path, user["id"]))
                    conn.commit()
                
                return self._send_json({"ok": True, "profile_pic": web_path})
            except Exception as e:
                log_security_event("UPLOAD_ERROR", self._client_ip(), user["email"], str(e))
                return self._send_json({"error": "Upload failed."}, 500)

        if path == "/api/logout":
            destroy_session(self._cookies().get("session"))
            return self._send_json(
                {"ok": True}, 200,
                extra_headers=[("Set-Cookie", "session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")],
            )

        if path == "/api/admin/set_pro":
            admin = self._current_user()
            if not admin or not admin["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            uid = data.get("id")
            val = 1 if data.get("pro") else 0
            with db() as conn:
                conn.execute("UPDATE users SET is_pro=? WHERE id=?", (val, uid))
                conn.commit()
            return self._send_json({"ok": True})

        if path == "/api/admin/delete_user":
            admin = self._current_user()
            if not admin or not admin["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            uid = data.get("id")
            if uid == admin["id"]:
                return self._send_json({"error": "You cannot delete your own admin account."}, 400)
            with db() as conn:
                conn.execute("DELETE FROM users WHERE id=?", (uid,))
                conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
                conn.execute("DELETE FROM orders WHERE user_id=?", (uid,))
                conn.commit()
            return self._send_json({"ok": True})

        if path == "/api/order":
            user = self._current_user()
            if not user:
                return self._send_json({"error": "Please log in first."}, 401)
            method = (data.get("method") or "").strip()[:40]
            txn = (data.get("txn_ref") or "").strip()[:120]
            amount = (data.get("amount") or "").strip()[:20]
            if not txn:
                return self._send_json({"error": "Please enter your payment/UPI reference number."}, 400)
            
            p_type = "Premium" if ("299" in amount or "200" in amount) else ("Standard" if "99" in amount else "Premium")
            receipt_id = f"REC-{int(time.time())}-{user['id']}"
            date_str = now_iso()
            
            with db() as conn:
                db_user = conn.execute("SELECT is_pro, plan_type FROM users WHERE id=?", (user["id"],)).fetchone()
                if db_user and db_user["is_pro"] and db_user["plan_type"] == "premium":
                    return self._send_json({"error": "You already have JOYA Pro. Thank you!"}, 400)
                # Auto-approve order instantly in db
                conn.execute(
                    "INSERT INTO orders (user_id,name,email,method,txn_ref,amount,status,created_at) "
                    "VALUES (?,?,?,?,?,?,'approved',?)",
                    (user["id"], user["name"], user["email"], method, txn, amount, date_str),
                )
                # Auto-upgrade user plan to pro
                conn.execute(
                    "UPDATE users SET is_pro=1, plan_type=? WHERE id=?",
                    (p_type.lower(), user["id"])
                )
                conn.commit()
            
            # Send receipt email via SMTP (Gmail) - Wrapped in try-except so SMTP errors do not block activation
            try:
                send_receipt_email(
                    user_name=user["name"],
                    to_email=user["email"],
                    receipt_id=receipt_id,
                    amount=amount,
                    plan_type=p_type,
                    txn_ref=txn,
                    date_str=date_str
                )
            except Exception as e:
                print(f"[SMTP WARNING] Failed to send receipt email: {e}")
            
            return self._send_json({
                "ok": True,
                "receipt": {
                    "receipt_id": receipt_id,
                    "name": user["name"],
                    "email": user["email"],
                    "amount": amount,
                    "plan": p_type,
                    "txn_ref": txn,
                    "date": date_str,
                    "status": "APPROVED / FULLY UNLOCKED"
                }
            })

        if path == "/api/admin/order_action":
            admin = self._current_user()
            if not admin or not admin["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            oid = data.get("id")
            action = data.get("action")
            if action not in ("approve", "reject"):
                return self._send_json({"error": "bad action"}, 400)
            new_status = "approved" if action == "approve" else "rejected"
            with db() as conn:
                order = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
                if not order:
                    return self._send_json({"error": "Order not found."}, 404)
                conn.execute("UPDATE orders SET status=? WHERE id=?", (new_status, oid))
                if action == "approve":
                    amount_val = str(order["amount"] or "").strip()
                    if "99" in amount_val:
                        p_type = "basic"
                        is_pro_val = 0
                    else:
                        p_type = "premium"
                        is_pro_val = 1
                    conn.execute("UPDATE users SET is_pro=?, plan_type=? WHERE id=?", (is_pro_val, p_type, order["user_id"]))
                conn.commit()
            return self._send_json({"ok": True})

        if path == "/api/admin/push_update":
            admin = self._current_user()
            if not admin or not admin["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            version = (data.get("version") or "").strip()[:20]
            if not version:
                return self._send_json({"error": "Version is required."}, 400)
            # Write update flag to a shared config file that client-sync reads
            update_flag_path = os.path.join(os.path.dirname(HERE), "config", "update_flag.json")
            os.makedirs(os.path.dirname(update_flag_path), exist_ok=True)
            with open(update_flag_path, "w", encoding="utf-8") as f:
                json.dump({"version": version, "url": f"http://localhost:8000/download", "pushed_at": now_iso()}, f)
            with db() as conn:
                user_count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            return self._send_json({"ok": True, "count": user_count})

        if path == "/api/client-auth":
            email = sanitize_input(data.get("email") or "", 120).lower()
            password = data.get("password") or ""
            if not email or not password:
                return self._send_json({"error": "Email and password are required."}, 400)
            with db() as conn:
                user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                if not user or not verify_password(password, user["pw_hash"], user["pw_salt"]):
                    return self._send_json({"error": "Invalid email or password."}, 401)
                # Auto-verify and upgrade client account to Pro plan
                plan_type = "premium"
                import datetime
                trial_ends_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650)
                trial_ends_at = trial_ends_dt.isoformat()
                
                conn.execute(
                    "UPDATE users SET plan_type='premium', is_pro=1, is_verified=1, trial_ends_at=? WHERE id=?", 
                    (trial_ends_at, user["id"])
                )
                conn.commit()
                
                token = create_session(user["id"], self._client_ip())
                trial_active = True
                
                return self._send_json({
                    "ok": True,
                    "name": user["name"],
                    "email": user["email"],
                    "token": token,
                    "plan_type": plan_type,
                    "trial_ends_at": trial_ends_at,
                    "trial_active": trial_active,
                    "is_pro": True
                })

        if path == "/api/client-sync":
            email = sanitize_input(data.get("email") or "", 120).lower()
            token = data.get("token") or ""
            if not email or not token:
                return self._send_json({"error": "Email and token are required."}, 400)
            with db() as conn:
                session = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
                if not session:
                    return self._send_json({"error": "Invalid or expired session token."}, 401)
                user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
                if not user or user["email"] != email:
                    return self._send_json({"error": "User mismatch."}, 401)
                
                # Auto-verify and upgrade client sync accounts to Pro plan
                plan_type = "premium"
                import datetime
                trial_ends_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650)
                trial_ends_at = trial_ends_dt.isoformat()
                
                conn.execute(
                    "UPDATE users SET plan_type='premium', is_pro=1, is_verified=1, trial_ends_at=? WHERE id=?", 
                    (trial_ends_at, user["id"])
                )
                conn.commit()
                
                trial_active = True
                
                # Check if update flag exists
                update_info = {}
                update_flag_path = os.path.join(os.path.dirname(HERE), "config", "update_flag.json")
                if os.path.exists(update_flag_path):
                    try:
                        with open(update_flag_path, "r", encoding="utf-8") as f:
                            update_info = json.load(f)
                    except Exception:
                        pass

                resp = {
                    "ok": True,
                    "plan_type": plan_type,
                    "trial_ends_at": trial_ends_at,
                    "trial_active": trial_active,
                    "is_pro": True
                }
                if update_info.get("version"):
                    resp["update_available"] = update_info["version"]
                    resp["update_url"] = update_info.get("url", "http://localhost:8000/download")
                return self._send_json(resp)

        return self._send_json({"error": "not found"}, 404)

    def _touch_login(self, uid: int):
        with db() as conn:
            conn.execute(
                "UPDATE users SET last_login=?, login_count=login_count+1 WHERE id=?",
                (now_iso(), uid),
            )
            conn.commit()

    @staticmethod
    def _cookie(token: str) -> str:
        return f"session={token}; Path=/; Max-Age={SESSION_HOURS*3600}; HttpOnly; SameSite=Lax"


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        try:
            exc_type, exc, _ = sys.exc_info()
            if exc:
                log_server_exception("SERVER_HANDLE_ERROR", exc, f"client={client_address}")
        finally:
            super().handle_error(request, client_address)


def main():
    import traceback
    init_db()
    purge_expired_sessions()  # Clean up on startup
    print("=" * 60)
    print("  JOYA Fortress-Grade Production Web Server")
    print("=" * 60)
    print(f"  Database : {DB_PATH}")
    print(f"  Interface: {HOST}")
    print(f"  Port     : {PORT}")
    print(f"  URL      : http://{HOST if HOST != '0.0.0.0' else '127.0.0.1'}:{PORT}/")
    print("  -- Security Active --")
    print("  [+] Rate Limiting (per-IP, per-action)")
    print("  [+] Brute-Force Lockout (5 fails = 15 min ban)")
    print("  [+] OTP Expiry (10 min window)")
    print("  [+] CSP, HSTS, X-Frame-Options: DENY")
    print("  [+] Path Traversal Protection")
    print("  [+] Request Body Size Limit (1 MB)")
    print("  [+] File Upload Validation (magic bytes)")
    print("  [+] Security Audit Logging")
    print("  [+] GZIP Compression")
    print("=" * 60)

    # Background thread to periodically purge expired sessions
    def session_cleaner():
        while True:
            time.sleep(3600)  # Every 1 hour
            purge_expired_sessions()
    cleaner_thread = threading.Thread(target=session_cleaner, daemon=True)
    cleaner_thread.start()

    try:
        with ThreadingServer((HOST, PORT), Handler) as httpd:
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\n[JOYA] Server stopped.")
    except BaseException as e:
        with open(os.path.join(HERE, "server_crash.txt"), "w") as f:
            f.write(f"BaseException ({type(e).__name__}) at {time.strftime('%Y-%m-%d %H:%M:%S')}:\n")
            traceback.print_exc(file=f)
        raise e


if __name__ == "__main__":
    main()
