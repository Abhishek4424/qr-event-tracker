"""
QR Event Tracker — Flask Backend with PostgreSQL support
For production deployment with persistent data storage
"""

import os
import io
import json
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
from contextlib import contextmanager

from flask import (
    Flask, request, jsonify, send_file, render_template, redirect, abort
)
import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer
from PIL import Image
from user_agents import parse as parse_ua

# ─── App Setup ───────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")

# Database URL - PostgreSQL for production, SQLite for local
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    # Fix for SQLAlchemy
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Base URL for QR codes
BASE_URL = os.environ.get("BASE_URL", None)


# ─── Database ────────────────────────────────────────────────
@contextmanager
def get_db():
    """Get database connection."""
    if DATABASE_URL:
        # PostgreSQL for production
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    else:
        # SQLite for local development
        import sqlite3
        conn = sqlite3.connect("qr_tracker.db")
        conn.row_factory = sqlite3.Row
    
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize database schema."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        if DATABASE_URL:
            # PostgreSQL schema
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    login_url TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active INTEGER DEFAULT 1
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS qr_codes (
                    id SERIAL PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    utm_source TEXT NOT NULL DEFAULT 'qrcode',
                    utm_medium TEXT NOT NULL DEFAULT 'event_print',
                    utm_campaign TEXT NOT NULL,
                    utm_content TEXT NOT NULL,
                    utm_term TEXT DEFAULT '',
                    tagged_url TEXT NOT NULL,
                    short_code TEXT UNIQUE,
                    qr_color TEXT DEFAULT '#0F2B3C',
                    error_correction TEXT DEFAULT 'H',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active INTEGER DEFAULT 1
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scans (
                    id SERIAL PRIMARY KEY,
                    qr_id INTEGER NOT NULL REFERENCES qr_codes(id) ON DELETE CASCADE,
                    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ip_hash TEXT,
                    user_agent TEXT,
                    device_type TEXT,
                    device_brand TEXT,
                    device_model TEXT,
                    os_name TEXT,
                    os_version TEXT,
                    browser_name TEXT,
                    browser_version TEXT,
                    is_mobile INTEGER DEFAULT 1,
                    is_tablet INTEGER DEFAULT 0,
                    is_bot INTEGER DEFAULT 0,
                    accept_language TEXT,
                    referer TEXT,
                    city TEXT,
                    region TEXT,
                    country TEXT,
                    fingerprint TEXT,
                    is_unique INTEGER DEFAULT 1
                )
            """)
            
            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_scans_qr_id ON scans(qr_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_scans_scanned_at ON scans(scanned_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_qr_codes_short_code ON qr_codes(short_code)")
        else:
            # SQLite schema (same as original)
            cursor.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    login_url TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS qr_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    utm_source TEXT NOT NULL DEFAULT 'qrcode',
                    utm_medium TEXT NOT NULL DEFAULT 'event_print',
                    utm_campaign TEXT NOT NULL,
                    utm_content TEXT NOT NULL,
                    utm_term TEXT DEFAULT '',
                    tagged_url TEXT NOT NULL,
                    short_code TEXT UNIQUE,
                    qr_color TEXT DEFAULT '#0F2B3C',
                    error_correction TEXT DEFAULT 'H',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active INTEGER DEFAULT 1,
                    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    qr_id INTEGER NOT NULL,
                    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ip_hash TEXT,
                    user_agent TEXT,
                    device_type TEXT,
                    device_brand TEXT,
                    device_model TEXT,
                    os_name TEXT,
                    os_version TEXT,
                    browser_name TEXT,
                    browser_version TEXT,
                    is_mobile INTEGER DEFAULT 1,
                    is_tablet INTEGER DEFAULT 0,
                    is_bot INTEGER DEFAULT 0,
                    accept_language TEXT,
                    referer TEXT,
                    city TEXT,
                    region TEXT,
                    country TEXT,
                    fingerprint TEXT,
                    is_unique INTEGER DEFAULT 1,
                    FOREIGN KEY (qr_id) REFERENCES qr_codes(id) ON DELETE CASCADE
                );
            """)


# ─── Helpers ─────────────────────────────────────────────────
def get_base_url():
    """Get the base URL for QR codes."""
    if BASE_URL:
        return BASE_URL.rstrip("/")
    if request:
        return request.host_url.rstrip("/")
    return "http://localhost:5000"


def generate_short_code(qr_id):
    """Generate a short code for URL redirect."""
    raw = f"qr-{qr_id}-{datetime.now().isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def build_tagged_url(base_url, params):
    """Build a URL with UTM parameters."""
    parsed = urlparse(base_url)
    existing = parse_qs(parsed.query)
    utm_params = {
        "utm_source": params.get("utm_source", "qrcode"),
        "utm_medium": params.get("utm_medium", "event_print"),
        "utm_campaign": params.get("utm_campaign", ""),
        "utm_content": params.get("utm_content", ""),
    }
    if params.get("utm_term"):
        utm_params["utm_term"] = params["utm_term"]
    utm_params = {k: v for k, v in utm_params.items() if v}
    existing.update(utm_params)
    new_query = urlencode(existing, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def generate_qr_image(data, color="#0F2B3C", error_correction="H", size=10, border=4, style="rounded"):
    """Generate a QR code image."""
    ec_map = {
        "H": qrcode.constants.ERROR_CORRECT_H,
        "Q": qrcode.constants.ERROR_CORRECT_Q,
        "M": qrcode.constants.ERROR_CORRECT_M,
        "L": qrcode.constants.ERROR_CORRECT_L,
    }
    qr = qrcode.QRCode(
        version=None,
        error_correction=ec_map.get(error_correction, qrcode.constants.ERROR_CORRECT_H),
        box_size=size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)

    if style == "rounded":
        img = qr.make_image(
            image_factory=StyledPilImage,
            module_drawer=RoundedModuleDrawer(),
            fill_color=color,
            back_color="white",
        )
    else:
        img = qr.make_image(fill_color=color, back_color="white")

    if not isinstance(img, Image.Image):
        img = img.get_image()

    return img


def parse_scan_data(req):
    """Extract device/browser info from request."""
    ua_string = req.headers.get("User-Agent", "")
    ua = parse_ua(ua_string)

    ip = req.headers.get("X-Forwarded-For", req.remote_addr)
    if ip:
        ip = ip.split(",")[0].strip()
    ip_hash = hashlib.sha256((ip or "unknown").encode()).hexdigest()[:16]

    fingerprint = hashlib.sha256(f"{ip_hash}:{ua_string}".encode()).hexdigest()[:24]

    return {
        "ip_hash": ip_hash,
        "user_agent": ua_string[:500],
        "device_type": "mobile" if ua.is_mobile else ("tablet" if ua.is_tablet else "desktop"),
        "device_brand": str(ua.device.brand or "Unknown"),
        "device_model": str(ua.device.model or "Unknown"),
        "os_name": str(ua.os.family or "Unknown"),
        "os_version": str(ua.os.version_string or ""),
        "browser_name": str(ua.browser.family or "Unknown"),
        "browser_version": str(ua.browser.version_string or ""),
        "is_mobile": 1 if ua.is_mobile else 0,
        "is_tablet": 1 if ua.is_tablet else 0,
        "is_bot": 1 if ua.is_bot else 0,
        "accept_language": req.headers.get("Accept-Language", "")[:200],
        "referer": req.headers.get("Referer", "")[:500],
        "fingerprint": fingerprint,
    }


# ─── Routes ───────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/s/<short_code>")
def scan_redirect(short_code):
    """Handle QR code scan."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM qr_codes WHERE short_code = %s AND is_active = 1",
            (short_code,)
        )
        qr = cursor.fetchone()

        if not qr:
            abort(404)

        scan = parse_scan_data(request)

        # Check uniqueness
        cursor.execute(
            "SELECT id FROM scans WHERE qr_id = %s AND fingerprint = %s",
            (qr["id"], scan["fingerprint"])
        )
        existing = cursor.fetchone()
        is_unique = 0 if existing else 1

        # Insert scan
        cursor.execute("""
            INSERT INTO scans (
                qr_id, ip_hash, user_agent, device_type, device_brand, device_model,
                os_name, os_version, browser_name, browser_version,
                is_mobile, is_tablet, is_bot, accept_language, referer,
                fingerprint, is_unique, city, region, country
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            qr["id"], scan["ip_hash"], scan["user_agent"], scan["device_type"],
            scan["device_brand"], scan["device_model"], scan["os_name"], scan["os_version"],
            scan["browser_name"], scan["browser_version"], scan["is_mobile"], scan["is_tablet"],
            scan["is_bot"], scan["accept_language"], scan["referer"],
            scan["fingerprint"], is_unique, 
            request.args.get("city", ""), 
            request.args.get("region", ""), 
            request.args.get("country", "")
        ))

    return redirect(qr["tagged_url"])


@app.route("/api/events", methods=["GET"])
def list_events():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM events WHERE is_active = 1 ORDER BY created_at DESC")
        events = cursor.fetchall()
        return jsonify([dict(e) for e in events])


@app.route("/api/events", methods=["POST"])
def create_event():
    data = request.json
    if not data.get("name") or not data.get("login_url"):
        return jsonify({"error": "name and login_url are required"}), 400

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO events (name, description, login_url) VALUES (%s, %s, %s) RETURNING id",
            (data["name"], data.get("description", ""), data["login_url"])
        )
        event_id = cursor.fetchone()["id"]
        
    return jsonify({"id": event_id, "message": "Event created"}), 201


# Add remaining routes following the same pattern...
# The rest of the endpoints would follow the same pattern, replacing:
# - db.execute() with cursor.execute()
# - ? placeholders with %s for PostgreSQL
# - Adding RETURNING id for inserts in PostgreSQL


# ─── Init & Run ──────────────────────────────────────────────
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    if os.environ.get("RENDER"):
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        app.run(host="0.0.0.0", port=port, debug=debug)