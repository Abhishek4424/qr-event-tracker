"""
QR Event Tracker — Flask Backend
Generates UTM-tagged QR codes for marketing events and tracks scan analytics.
"""

import os
import io
import json
import hashlib
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
from functools import wraps

from flask import (
    Flask, request, jsonify, send_file, render_template,
    g, redirect, abort
)
import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer, SquareModuleDrawer
from PIL import Image
from user_agents import parse as parse_ua

# ─── App Setup ───────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")

# Database path - for Render, use /var/data for persistence
if os.environ.get("RENDER"):
    # On Render, use persistent disk
    os.makedirs("/var/data", exist_ok=True)
    DATABASE = "/var/data/qr_tracker.db"
else:
    # Local development
    DATABASE = os.environ.get("DATABASE_PATH", "qr_tracker.db")

# Base URL for QR codes - set this to your production URL when deployed
# e.g., "https://your-app.onrender.com" or "https://qr-tracker.yourdomain.com"
BASE_URL = os.environ.get("BASE_URL", None)


# ─── Database ────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Initialize database schema."""
    db = sqlite3.connect(DATABASE)
    db.executescript("""
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
            screen_width INTEGER,
            screen_height INTEGER,
            city TEXT,
            region TEXT,
            country TEXT,
            latitude REAL,
            longitude REAL,
            fingerprint TEXT,
            is_unique INTEGER DEFAULT 1,
            FOREIGN KEY (qr_id) REFERENCES qr_codes(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_scans_qr_id ON scans(qr_id);
        CREATE INDEX IF NOT EXISTS idx_scans_scanned_at ON scans(scanned_at);
        CREATE INDEX IF NOT EXISTS idx_scans_fingerprint ON scans(fingerprint);
        CREATE INDEX IF NOT EXISTS idx_qr_codes_event_id ON qr_codes(event_id);
        CREATE INDEX IF NOT EXISTS idx_qr_codes_short_code ON qr_codes(short_code);
    """)
    db.commit()
    db.close()


# ─── Helpers ─────────────────────────────────────────────────
def get_base_url():
    """Get the base URL for QR codes - uses BASE_URL env var or request.host_url."""
    if BASE_URL:
        return BASE_URL.rstrip("/")
    # In development, use the request's host URL
    if request:
        return request.host_url.rstrip("/")
    return "http://localhost:5000"

def generate_short_code(qr_id):
    """Generate a short code for URL redirect."""
    raw = f"qr-{qr_id}-{datetime.now().isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def build_tagged_url(base_url, params):
    """Build a URL with UTM parameters appended."""
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
    # Remove empty
    utm_params = {k: v for k, v in utm_params.items() if v}
    existing.update(utm_params)
    new_query = urlencode(existing, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def generate_qr_image(data, color="#0F2B3C", error_correction="H", size=10, border=4, style="rounded"):
    """Generate a QR code image and return as bytes."""
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

    # Convert to PIL Image if needed
    if not isinstance(img, Image.Image):
        img = img.get_image()

    return img


def parse_scan_data(req):
    """Extract device/browser/location info from a request."""
    ua_string = req.headers.get("User-Agent", "")
    ua = parse_ua(ua_string)

    # Hash IP for privacy
    ip = req.headers.get("X-Forwarded-For", req.remote_addr)
    if ip:
        ip = ip.split(",")[0].strip()
    ip_hash = hashlib.sha256((ip or "unknown").encode()).hexdigest()[:16]

    # Fingerprint: combination of IP hash + UA for uniqueness detection
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


# ─── Page Routes ─────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/s/<short_code>")
def scan_redirect(short_code):
    """Handle QR code scan — log data and redirect to tagged URL."""
    db = get_db()
    qr = db.execute(
        "SELECT * FROM qr_codes WHERE short_code = ? AND is_active = 1",
        (short_code,)
    ).fetchone()

    if not qr:
        abort(404)

    # Parse scan data
    scan = parse_scan_data(request)

    # Check uniqueness
    existing = db.execute(
        "SELECT id FROM scans WHERE qr_id = ? AND fingerprint = ?",
        (qr["id"], scan["fingerprint"])
    ).fetchone()
    is_unique = 0 if existing else 1

    # Get geo data from query params (set by client-side JS on landing)
    # Or from headers if available
    city = request.args.get("city", "")
    region = request.args.get("region", "")
    country = request.args.get("country", "")

    db.execute("""
        INSERT INTO scans (
            qr_id, ip_hash, user_agent, device_type, device_brand, device_model,
            os_name, os_version, browser_name, browser_version,
            is_mobile, is_tablet, is_bot, accept_language, referer,
            fingerprint, is_unique, city, region, country
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        qr["id"], scan["ip_hash"], scan["user_agent"], scan["device_type"],
        scan["device_brand"], scan["device_model"], scan["os_name"], scan["os_version"],
        scan["browser_name"], scan["browser_version"], scan["is_mobile"], scan["is_tablet"],
        scan["is_bot"], scan["accept_language"], scan["referer"],
        scan["fingerprint"], is_unique, city, region, country,
    ))
    db.commit()

    return redirect(qr["tagged_url"])


# ─── API: Events ─────────────────────────────────────────────
@app.route("/api/events", methods=["GET"])
def list_events():
    db = get_db()
    events = db.execute(
        "SELECT * FROM events WHERE is_active = 1 ORDER BY created_at DESC"
    ).fetchall()
    return jsonify([dict(e) for e in events])


@app.route("/api/events", methods=["POST"])
def create_event():
    data = request.json
    if not data.get("name") or not data.get("login_url"):
        return jsonify({"error": "name and login_url are required"}), 400

    db = get_db()
    cursor = db.execute(
        "INSERT INTO events (name, description, login_url) VALUES (?, ?, ?)",
        (data["name"], data.get("description", ""), data["login_url"])
    )
    db.commit()
    return jsonify({"id": cursor.lastrowid, "message": "Event created"}), 201


@app.route("/api/events/<int:event_id>", methods=["DELETE"])
def delete_event(event_id):
    db = get_db()
    db.execute("UPDATE events SET is_active = 0 WHERE id = ?", (event_id,))
    db.commit()
    return jsonify({"message": "Event deleted"})


# ─── API: QR Codes ───────────────────────────────────────────
@app.route("/api/qr", methods=["GET"])
def list_qr_codes():
    db = get_db()
    event_id = request.args.get("event_id")
    if event_id:
        qrs = db.execute(
            "SELECT * FROM qr_codes WHERE event_id = ? AND is_active = 1 ORDER BY created_at DESC",
            (event_id,)
        ).fetchall()
    else:
        qrs = db.execute(
            "SELECT * FROM qr_codes WHERE is_active = 1 ORDER BY created_at DESC"
        ).fetchall()
    return jsonify([dict(q) for q in qrs])


@app.route("/api/qr", methods=["POST"])
def create_qr():
    data = request.json
    required = ["event_id", "label", "utm_campaign", "utm_content"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"{field} is required"}), 400

    db = get_db()

    # Get event's login URL
    event = db.execute("SELECT * FROM events WHERE id = ?", (data["event_id"],)).fetchone()
    if not event:
        return jsonify({"error": "Event not found"}), 404

    tagged_url = build_tagged_url(event["login_url"], data)

    cursor = db.execute("""
        INSERT INTO qr_codes (
            event_id, label, utm_source, utm_medium, utm_campaign,
            utm_content, utm_term, tagged_url, qr_color, error_correction
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["event_id"], data["label"],
        data.get("utm_source", "qrcode"),
        data.get("utm_medium", "event_print"),
        data["utm_campaign"],
        data["utm_content"],
        data.get("utm_term", ""),
        tagged_url,
        data.get("qr_color", "#0F2B3C"),
        data.get("error_correction", "H"),
    ))
    db.commit()

    qr_id = cursor.lastrowid
    short_code = generate_short_code(qr_id)
    db.execute("UPDATE qr_codes SET short_code = ? WHERE id = ?", (short_code, qr_id))
    db.commit()

    return jsonify({
        "id": qr_id,
        "short_code": short_code,
        "tagged_url": tagged_url,
        "message": "QR code created"
    }), 201


@app.route("/api/qr/<int:qr_id>", methods=["DELETE"])
def delete_qr(qr_id):
    db = get_db()
    db.execute("UPDATE qr_codes SET is_active = 0 WHERE id = ?", (qr_id,))
    db.commit()
    return jsonify({"message": "QR code deleted"})


@app.route("/api/qr/<int:qr_id>/download/<fmt>")
def download_qr(qr_id, fmt):
    """Download QR code image in specified format (jpeg, png, svg)."""
    db = get_db()
    qr = db.execute("SELECT * FROM qr_codes WHERE id = ?", (qr_id,)).fetchone()
    if not qr:
        return jsonify({"error": "QR code not found"}), 404

    # Build the scan URL (not the direct tagged URL)
    base_url = get_base_url()
    scan_url = f"{base_url}/s/{qr['short_code']}"

    size = int(request.args.get("size", 10))
    style = request.args.get("style", "rounded")

    img = generate_qr_image(
        scan_url,
        color=qr["qr_color"],
        error_correction=qr["error_correction"],
        size=size,
        style=style,
    )

    buf = io.BytesIO()

    if fmt == "svg":
        # For SVG, regenerate with SVG factory
        import qrcode.image.svg
        qr_obj = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=size,
            border=4,
        )
        qr_obj.add_data(scan_url)
        qr_obj.make(fit=True)
        svg_img = qr_obj.make_image(image_factory=qrcode.image.svg.SvgPathImage)
        svg_img.save(buf)
        buf.seek(0)
        return send_file(buf, mimetype="image/svg+xml",
                         download_name=f"qr_{qr['utm_content']}.svg", as_attachment=True)

    elif fmt == "png":
        img.save(buf, format="PNG", quality=100)
        buf.seek(0)
        return send_file(buf, mimetype="image/png",
                         download_name=f"qr_{qr['utm_content']}.png", as_attachment=True)

    else:  # jpeg
        # JPEG doesn't support transparency
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=95)
        buf.seek(0)
        return send_file(buf, mimetype="image/jpeg",
                         download_name=f"qr_{qr['utm_content']}.jpg", as_attachment=True)


@app.route("/api/qr/<int:qr_id>/preview")
def preview_qr(qr_id):
    """Return QR code image inline for preview."""
    db = get_db()
    qr = db.execute("SELECT * FROM qr_codes WHERE id = ?", (qr_id,)).fetchone()
    if not qr:
        return jsonify({"error": "QR code not found"}), 404

    base_url = get_base_url()
    scan_url = f"{base_url}/s/{qr['short_code']}"

    img = generate_qr_image(
        scan_url,
        color=qr["qr_color"],
        error_correction=qr["error_correction"],
        size=8,
        style="rounded",
    )
    buf = io.BytesIO()
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# ─── API: Analytics ──────────────────────────────────────────
@app.route("/api/analytics/overview")
def analytics_overview():
    """High-level stats across all events or filtered by event_id."""
    db = get_db()
    event_id = request.args.get("event_id")

    base_query = """
        SELECT
            COUNT(*) as total_scans,
            SUM(CASE WHEN is_unique = 1 THEN 1 ELSE 0 END) as unique_scanners,
            SUM(CASE WHEN is_unique = 0 THEN 1 ELSE 0 END) as repeat_scans,
            SUM(CASE WHEN is_bot = 1 THEN 1 ELSE 0 END) as bot_scans
        FROM scans s
        JOIN qr_codes q ON s.qr_id = q.id
    """
    if event_id:
        base_query += " WHERE q.event_id = ?"
        row = db.execute(base_query, (event_id,)).fetchone()
    else:
        row = db.execute(base_query).fetchone()

    total = row["total_scans"] or 0
    unique = row["unique_scanners"] or 0
    repeat_rate = round(((total - unique) / total * 100), 1) if total > 0 else 0

    # Peak hour
    hour_query = """
        SELECT strftime('%H', scanned_at) as hour, COUNT(*) as cnt
        FROM scans s JOIN qr_codes q ON s.qr_id = q.id
    """
    if event_id:
        hour_query += " WHERE q.event_id = ?"
        hour_query += " GROUP BY hour ORDER BY cnt DESC LIMIT 1"
        peak = db.execute(hour_query, (event_id,)).fetchone()
    else:
        hour_query += " GROUP BY hour ORDER BY cnt DESC LIMIT 1"
        peak = db.execute(hour_query).fetchone()

    peak_hour = ""
    peak_scans = 0
    if peak:
        h = int(peak["hour"])
        peak_hour = f"{h % 12 or 12} {'PM' if h >= 12 else 'AM'}"
        peak_scans = peak["cnt"]

    # Top OS
    os_query = """
        SELECT os_name, COUNT(*) as cnt
        FROM scans s JOIN qr_codes q ON s.qr_id = q.id
    """
    if event_id:
        os_query += " WHERE q.event_id = ?"
        os_query += " GROUP BY os_name ORDER BY cnt DESC LIMIT 1"
        top_os = db.execute(os_query, (event_id,)).fetchone()
    else:
        os_query += " GROUP BY os_name ORDER BY cnt DESC LIMIT 1"
        top_os = db.execute(os_query).fetchone()

    # Top city
    city_query = """
        SELECT city, COUNT(*) as cnt
        FROM scans s JOIN qr_codes q ON s.qr_id = q.id
        WHERE city != '' AND city IS NOT NULL
    """
    if event_id:
        city_query += " AND q.event_id = ?"
        city_query += " GROUP BY city ORDER BY cnt DESC LIMIT 1"
        top_city = db.execute(city_query, (event_id,)).fetchone()
    else:
        city_query += " GROUP BY city ORDER BY cnt DESC LIMIT 1"
        top_city = db.execute(city_query).fetchone()

    return jsonify({
        "total_scans": total,
        "unique_scanners": unique,
        "repeat_scans": total - unique,
        "repeat_rate": repeat_rate,
        "bot_scans": row["bot_scans"] or 0,
        "peak_hour": peak_hour,
        "peak_hour_scans": peak_scans,
        "top_os": dict(top_os) if top_os else None,
        "top_city": dict(top_city) if top_city else None,
    })


@app.route("/api/analytics/timeline")
def analytics_timeline():
    """Hourly or daily scan distribution."""
    db = get_db()
    event_id = request.args.get("event_id")
    granularity = request.args.get("granularity", "hourly")  # hourly or daily

    if granularity == "hourly":
        fmt = "%H"
        label = "hour"
    else:
        fmt = "%Y-%m-%d"
        label = "date"

    query = f"""
        SELECT strftime('{fmt}', scanned_at) as period,
               COUNT(*) as total,
               SUM(CASE WHEN is_unique = 1 THEN 1 ELSE 0 END) as unique_count
        FROM scans s JOIN qr_codes q ON s.qr_id = q.id
    """
    if event_id:
        query += " WHERE q.event_id = ?"
        query += f" GROUP BY period ORDER BY period"
        rows = db.execute(query, (event_id,)).fetchall()
    else:
        query += f" GROUP BY period ORDER BY period"
        rows = db.execute(query).fetchall()

    result = []
    for r in rows:
        period = r["period"]
        if granularity == "hourly" and period:
            h = int(period)
            display = f"{h % 12 or 12} {'PM' if h >= 12 else 'AM'}"
        else:
            display = period
        result.append({
            "period": period,
            "display": display,
            "total": r["total"],
            "unique": r["unique_count"],
        })

    return jsonify(result)


@app.route("/api/analytics/placements")
def analytics_placements():
    """Performance breakdown by QR code placement (utm_content)."""
    db = get_db()
    event_id = request.args.get("event_id")

    query = """
        SELECT
            q.id, q.label, q.utm_content, q.short_code,
            COUNT(s.id) as total_scans,
            SUM(CASE WHEN s.is_unique = 1 THEN 1 ELSE 0 END) as unique_scanners,
            strftime('%H', s.scanned_at) as peak_hour_raw
        FROM qr_codes q
        LEFT JOIN scans s ON s.qr_id = q.id
    """
    if event_id:
        query += " WHERE q.event_id = ? AND q.is_active = 1"
        query += " GROUP BY q.id ORDER BY total_scans DESC"
        rows = db.execute(query, (event_id,)).fetchall()
    else:
        query += " WHERE q.is_active = 1"
        query += " GROUP BY q.id ORDER BY total_scans DESC"
        rows = db.execute(query).fetchall()

    # Get peak hours per QR
    placements = []
    for r in rows:
        peak_q = db.execute("""
            SELECT strftime('%H', scanned_at) as hour, COUNT(*) as cnt
            FROM scans WHERE qr_id = ?
            GROUP BY hour ORDER BY cnt DESC LIMIT 1
        """, (r["id"],)).fetchone()

        peak_display = ""
        if peak_q and peak_q["hour"]:
            h = int(peak_q["hour"])
            peak_display = f"{h % 12 or 12} {'PM' if h >= 12 else 'AM'}"

        placements.append({
            "id": r["id"],
            "label": r["label"],
            "utm_content": r["utm_content"],
            "short_code": r["short_code"],
            "total_scans": r["total_scans"],
            "unique_scanners": r["unique_scanners"],
            "peak_hour": peak_display,
        })

    return jsonify(placements)


@app.route("/api/analytics/personas")
def analytics_personas():
    """User persona data — device, OS, browser, city breakdowns."""
    db = get_db()
    event_id = request.args.get("event_id")

    def breakdown(column, limit=10):
        query = f"""
            SELECT {column} as name, COUNT(*) as count
            FROM scans s JOIN qr_codes q ON s.qr_id = q.id
            WHERE {column} != '' AND {column} IS NOT NULL
        """
        if event_id:
            query += " AND q.event_id = ?"
            query += f" GROUP BY {column} ORDER BY count DESC LIMIT {limit}"
            return [dict(r) for r in db.execute(query, (event_id,)).fetchall()]
        else:
            query += f" GROUP BY {column} ORDER BY count DESC LIMIT {limit}"
            return [dict(r) for r in db.execute(query).fetchall()]

    # Device type split
    device_types = breakdown("device_type", 5)
    os_data = breakdown("os_name", 10)
    browser_data = breakdown("browser_name", 10)
    city_data = breakdown("city", 15)
    brand_data = breakdown("device_brand", 10)
    language_data = breakdown("accept_language", 10)

    return jsonify({
        "device_types": device_types,
        "os": os_data,
        "browsers": browser_data,
        "cities": city_data,
        "device_brands": brand_data,
        "languages": language_data,
    })


@app.route("/api/analytics/live")
def analytics_live():
    """Recent scans feed — last N scans."""
    db = get_db()
    event_id = request.args.get("event_id")
    limit = min(int(request.args.get("limit", 50)), 200)

    query = """
        SELECT s.*, q.label as placement_label, q.utm_content
        FROM scans s
        JOIN qr_codes q ON s.qr_id = q.id
    """
    if event_id:
        query += " WHERE q.event_id = ?"
        query += " ORDER BY s.scanned_at DESC LIMIT ?"
        rows = db.execute(query, (event_id, limit)).fetchall()
    else:
        query += " ORDER BY s.scanned_at DESC LIMIT ?"
        rows = db.execute(query, (limit,)).fetchall()

    return jsonify([dict(r) for r in rows])


@app.route("/api/analytics/export")
def analytics_export():
    """Export scan data as CSV or JSON."""
    db = get_db()
    event_id = request.args.get("event_id")
    fmt = request.args.get("format", "csv")

    query = """
        SELECT
            s.scanned_at, s.device_type, s.device_brand, s.os_name,
            s.os_version, s.browser_name, s.browser_version,
            s.is_mobile, s.is_tablet, s.is_bot, s.is_unique,
            s.city, s.region, s.country, s.accept_language,
            q.label as placement, q.utm_content, q.utm_campaign, q.utm_source, q.utm_medium
        FROM scans s
        JOIN qr_codes q ON s.qr_id = q.id
    """
    if event_id:
        query += " WHERE q.event_id = ?"
        query += " ORDER BY s.scanned_at DESC"
        rows = db.execute(query, (event_id,)).fetchall()
    else:
        query += " ORDER BY s.scanned_at DESC"
        rows = db.execute(query).fetchall()

    data = [dict(r) for r in rows]

    if fmt == "json":
        buf = io.BytesIO()
        buf.write(json.dumps(data, indent=2, default=str).encode())
        buf.seek(0)
        return send_file(buf, mimetype="application/json",
                         download_name="qr_scan_export.json", as_attachment=True)
    else:
        import csv
        buf = io.StringIO()
        if data:
            writer = csv.DictWriter(buf, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        buf.seek(0)
        return send_file(
            io.BytesIO(buf.getvalue().encode()),
            mimetype="text/csv",
            download_name="qr_scan_export.csv",
            as_attachment=True
        )


# ─── Geo Enrichment Endpoint (called from client-side) ──────
@app.route("/api/scans/<int:scan_id>/geo", methods=["PATCH"])
def update_scan_geo(scan_id):
    """Update scan with geo data from client-side geolocation API."""
    data = request.json
    db = get_db()
    db.execute("""
        UPDATE scans SET city = ?, region = ?, country = ?,
        latitude = ?, longitude = ?, screen_width = ?, screen_height = ?
        WHERE id = ?
    """, (
        data.get("city", ""), data.get("region", ""), data.get("country", ""),
        data.get("latitude"), data.get("longitude"),
        data.get("screen_width"), data.get("screen_height"),
        scan_id
    ))
    db.commit()
    return jsonify({"message": "updated"})


# ─── Init & Run ──────────────────────────────────────────────
with app.app_context():
    init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    # For Render deployment
    if os.environ.get("RENDER"):
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        app.run(host="0.0.0.0", port=port, debug=debug)
