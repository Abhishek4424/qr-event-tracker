# QR Event Tracker

Generate UTM-tagged QR codes for marketing events and track attendee scan analytics in real-time — device personas, geographic distribution, placement performance, and live scan feeds.

## What It Does

This tool is designed for **marketing events** where QR codes point to a **login/signup page**. Since you don't have downstream conversion visibility from the QR side, analytics focus on **scan behavior and user persona profiling**:

- **Generate QR codes** with UTM parameters per physical placement (booth banner, registration desk, standee, etc.)
- **Track scans** with device, OS, browser, city, and repeat detection
- **Analyze personas** — who is scanning (Android vs iOS, browser breakdown, geography)
- **Compare placements** — which physical QR location performs best
- **Live feed** — real-time scan log with auto-refresh

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐
│  QR Code     │────▶│  /s/:code    │────▶│  Login Page   │
│  (printed)   │     │  (tracker)   │     │  (your app)   │
└─────────────┘     └──────┬───────┘     └───────────────┘
                           │
                    Logs: device, OS,
                    browser, IP hash,
                    fingerprint, UA
                           │
                    ┌──────▼───────┐
                    │   SQLite DB  │
                    │   (scans)    │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  Analytics   │
                    │  Dashboard   │
                    └──────────────┘
```

### Data Points Captured Per Scan

| Field | Source | Purpose |
|-------|--------|---------|
| `scanned_at` | Server timestamp | Timing / hourly heatmap |
| `ip_hash` | SHA256 of IP | Privacy-safe uniqueness |
| `user_agent` | Request header | Raw UA string |
| `device_type` | Parsed UA | mobile / tablet / desktop |
| `device_brand` | Parsed UA | Samsung, Apple, Xiaomi, etc. |
| `device_model` | Parsed UA | Specific model |
| `os_name` | Parsed UA | Android, iOS, Windows |
| `os_version` | Parsed UA | e.g., 14, 17.3 |
| `browser_name` | Parsed UA | Chrome, Safari, Samsung Internet |
| `browser_version` | Parsed UA | Version string |
| `is_mobile` | Parsed UA | Boolean flag |
| `is_tablet` | Parsed UA | Boolean flag |
| `is_bot` | Parsed UA | Bot detection |
| `accept_language` | Request header | Language preference |
| `referer` | Request header | Referral source |
| `city` | Client-side geo | Scanner's city |
| `region` | Client-side geo | State/province |
| `country` | Client-side geo | Country |
| `fingerprint` | IP+UA hash | Repeat detection |
| `is_unique` | Computed | First-time vs repeat |
| `screen_width/height` | Client-side | Screen dimensions |

## Quick Start

### Local Development

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/qr-event-tracker.git
cd qr-event-tracker

# Setup
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Run
python app.py
# Open http://localhost:5000
```

### Docker

```bash
docker build -t qr-event-tracker .
docker run -p 5000:5000 -v qr-data:/data qr-event-tracker
```

### Run Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

## ⚠️ IMPORTANT: Data Persistence

**SQLite databases are NOT persistent on free hosting tiers!** Your data will be lost when:
- The server restarts or redeploys
- The container spins down after inactivity
- You push new code updates

### Solutions for Persistent Data:

#### Option 1: Use PostgreSQL (Recommended for Production)
Most platforms offer free PostgreSQL databases:
- **Render**: Free PostgreSQL included
- **Railway**: PostgreSQL add-on available
- **Supabase**: Free PostgreSQL database
- **Neon**: Serverless PostgreSQL

To use PostgreSQL:
1. Use `app_postgres.py` instead of `app.py`
2. Set `DATABASE_URL` environment variable
3. Install `psycopg2-binary` dependency

#### Option 2: Paid Hosting with Persistent Disk
- **Render**: $7/month for persistent disk
- **Railway**: $5/month includes persistent storage
- **Fly.io**: Includes persistent volumes

#### Option 3: External Database Services
- **Turso**: SQLite in the cloud (free tier)
- **PlanetScale**: MySQL-compatible (free tier)
- **MongoDB Atlas**: NoSQL option (free tier)

## Deployment Options

### Render (Free with PostgreSQL)

1. Push to GitHub
2. Create free PostgreSQL database on Render
3. Deploy web service with environment variables:
   - `DATABASE_URL` — from PostgreSQL instance
   - `SECRET_KEY` — generate a random string
   - `BASE_URL` — your Render app URL
   - `PORT` — `10000`

### Railway / Fly.io

1. Push to GitHub
2. Connect your repo
3. Add PostgreSQL database
4. Set environment variables as above

### GitHub Codespaces

1. Open the repo in Codespaces
2. `pip install -r requirements.txt && python app.py`
3. Forward port 5000

## Usage Guide

### 1. Create an Event
Click **+ New Event**, enter the event name and your login page URL.

### 2. Generate QR Codes
For each physical placement at your event:
- Set a descriptive **label** (e.g., "Main Stage Banner")
- Set **utm_content** to a unique placement ID (e.g., `main_stage_banner`)
- Set **utm_campaign** to the event identifier
- Choose a color and generate

### 3. Print & Deploy
Download QR codes as JPEG/PNG/SVG and use in your print materials. Each QR encodes a tracked redirect URL (`/s/:shortcode`) that logs the scan and redirects to your login page with UTM parameters.

### 4. Monitor
- **Analytics tab** — overview stats, hourly scan timeline, placement comparison
- **Personas tab** — device OS, browser, city breakdowns with persona summary
- **Live Feed** — real-time scan log, export to CSV/JSON

## API Reference

### Events
- `GET /api/events` — List events
- `POST /api/events` — Create event `{name, login_url, description?}`
- `DELETE /api/events/:id` — Soft delete

### QR Codes
- `GET /api/qr?event_id=N` — List QR codes
- `POST /api/qr` — Create QR `{event_id, label, utm_campaign, utm_content, ...}`
- `GET /api/qr/:id/download/:fmt` — Download (jpeg/png/svg)
- `GET /api/qr/:id/preview` — Preview image
- `DELETE /api/qr/:id` — Soft delete

### Analytics
- `GET /api/analytics/overview?event_id=N` — Stats summary
- `GET /api/analytics/timeline?event_id=N&granularity=hourly` — Time distribution
- `GET /api/analytics/placements?event_id=N` — Per-placement performance
- `GET /api/analytics/personas?event_id=N` — Device/browser/city breakdowns
- `GET /api/analytics/live?event_id=N&limit=50` — Recent scans
- `GET /api/analytics/export?event_id=N&format=csv` — Export data

### Scan Redirect
- `GET /s/:short_code` — Scan handler (logs + redirects)

## Tech Stack

- **Backend**: Python / Flask
- **Database**: SQLite (WAL mode)
- **QR Generation**: `qrcode` + Pillow (rounded module drawer)
- **UA Parsing**: `user-agents` library
- **Frontend**: Vanilla JS + CSS (no framework dependencies)
- **Deployment**: Docker / Gunicorn

## License

MIT
