"""Tests for QR Event Tracker API."""

import os
import json
import pytest

import tempfile

from app import app, init_db


@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.environ["DATABASE_PATH"] = db_path
    # Re-import DATABASE so app picks it up
    app.config["TESTING"] = True
    import app as app_module
    app_module.DATABASE = db_path
    init_db()
    with app.test_client() as client:
        yield client
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def event_id(client):
    """Create a test event and return its ID."""
    res = client.post("/api/events", json={
        "name": "Test Event",
        "login_url": "https://app.example.com/login",
        "description": "Test description"
    })
    return res.get_json()["id"]


@pytest.fixture
def qr_id(client, event_id):
    """Create a test QR code and return its ID."""
    res = client.post("/api/qr", json={
        "event_id": event_id,
        "label": "Main Banner",
        "utm_campaign": "test_campaign",
        "utm_content": "main_banner",
        "qr_color": "#0F2B3C",
    })
    return res.get_json()["id"]


# ─── Event Tests ───
class TestEvents:
    def test_create_event(self, client):
        res = client.post("/api/events", json={
            "name": "Logistics Summit",
            "login_url": "https://app.roado.com/login"
        })
        assert res.status_code == 201
        data = res.get_json()
        assert data["id"] > 0

    def test_create_event_missing_fields(self, client):
        res = client.post("/api/events", json={"name": "No URL"})
        assert res.status_code == 400

    def test_list_events(self, client, event_id):
        res = client.get("/api/events")
        assert res.status_code == 200
        events = res.get_json()
        assert len(events) >= 1
        assert events[0]["name"] == "Test Event"

    def test_delete_event(self, client, event_id):
        res = client.delete(f"/api/events/{event_id}")
        assert res.status_code == 200
        # Should be soft-deleted
        events = client.get("/api/events").get_json()
        assert all(e["id"] != event_id for e in events)


# ─── QR Code Tests ───
class TestQRCodes:
    def test_create_qr(self, client, event_id):
        res = client.post("/api/qr", json={
            "event_id": event_id,
            "label": "Booth Banner",
            "utm_campaign": "expo_2026",
            "utm_content": "booth_banner",
        })
        assert res.status_code == 201
        data = res.get_json()
        assert "short_code" in data
        assert len(data["short_code"]) == 8

    def test_create_qr_missing_fields(self, client, event_id):
        res = client.post("/api/qr", json={
            "event_id": event_id,
            "label": "Test",
        })
        assert res.status_code == 400

    def test_list_qr_by_event(self, client, event_id, qr_id):
        res = client.get(f"/api/qr?event_id={event_id}")
        assert res.status_code == 200
        qrs = res.get_json()
        assert len(qrs) >= 1

    def test_download_jpeg(self, client, qr_id):
        res = client.get(f"/api/qr/{qr_id}/download/jpeg")
        assert res.status_code == 200
        assert "image/jpeg" in res.content_type

    def test_download_png(self, client, qr_id):
        res = client.get(f"/api/qr/{qr_id}/download/png")
        assert res.status_code == 200
        assert "image/png" in res.content_type

    def test_download_svg(self, client, qr_id):
        res = client.get(f"/api/qr/{qr_id}/download/svg")
        assert res.status_code == 200
        assert "image/svg+xml" in res.content_type

    def test_preview(self, client, qr_id):
        res = client.get(f"/api/qr/{qr_id}/preview")
        assert res.status_code == 200
        assert "image/png" in res.content_type

    def test_delete_qr(self, client, qr_id):
        res = client.delete(f"/api/qr/{qr_id}")
        assert res.status_code == 200


# ─── Scan & Redirect Tests ───
class TestScans:
    def test_scan_redirect(self, client, event_id):
        # Create QR
        qr_res = client.post("/api/qr", json={
            "event_id": event_id,
            "label": "Test QR",
            "utm_campaign": "test",
            "utm_content": "test_scan",
        })
        short_code = qr_res.get_json()["short_code"]

        # Scan it
        res = client.get(f"/s/{short_code}", follow_redirects=False)
        assert res.status_code == 302
        assert "utm_source=qrcode" in res.headers["Location"]

    def test_scan_records_data(self, client, event_id):
        qr_res = client.post("/api/qr", json={
            "event_id": event_id,
            "label": "Scan Test",
            "utm_campaign": "scan_test",
            "utm_content": "scan_placement",
        })
        qr_id = qr_res.get_json()["id"]
        short_code = qr_res.get_json()["short_code"]

        # Scan
        client.get(f"/s/{short_code}", headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/122.0"
        })

        # Check analytics
        res = client.get(f"/api/analytics/live?event_id={event_id}")
        scans = res.get_json()
        assert len(scans) >= 1
        assert scans[0]["os_name"] == "Android"

    def test_repeat_scan_detection(self, client, event_id):
        qr_res = client.post("/api/qr", json={
            "event_id": event_id,
            "label": "Repeat Test",
            "utm_campaign": "repeat_test",
            "utm_content": "repeat_placement",
        })
        short_code = qr_res.get_json()["short_code"]

        # Scan twice with same UA
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) Safari/17.0"
        client.get(f"/s/{short_code}", headers={"User-Agent": ua})
        client.get(f"/s/{short_code}", headers={"User-Agent": ua})

        overview = client.get(f"/api/analytics/overview?event_id={event_id}").get_json()
        assert overview["total_scans"] >= 2
        assert overview["repeat_scans"] >= 1

    def test_invalid_short_code(self, client):
        res = client.get("/s/nonexistent")
        assert res.status_code == 404


# ─── Analytics Tests ───
class TestAnalytics:
    def test_overview_empty(self, client, event_id):
        res = client.get(f"/api/analytics/overview?event_id={event_id}")
        assert res.status_code == 200
        data = res.get_json()
        assert data["total_scans"] == 0

    def test_timeline(self, client, event_id):
        res = client.get(f"/api/analytics/timeline?event_id={event_id}&granularity=hourly")
        assert res.status_code == 200

    def test_placements(self, client, event_id, qr_id):
        res = client.get(f"/api/analytics/placements?event_id={event_id}")
        assert res.status_code == 200
        data = res.get_json()
        assert len(data) >= 1

    def test_personas(self, client, event_id):
        res = client.get(f"/api/analytics/personas?event_id={event_id}")
        assert res.status_code == 200
        data = res.get_json()
        assert "os" in data
        assert "browsers" in data
        assert "cities" in data

    def test_export_csv(self, client, event_id):
        res = client.get(f"/api/analytics/export?event_id={event_id}&format=csv")
        assert res.status_code == 200
        assert "text/csv" in res.content_type

    def test_export_json(self, client, event_id):
        res = client.get(f"/api/analytics/export?event_id={event_id}&format=json")
        assert res.status_code == 200


# ─── Index Page ───
class TestPages:
    def test_index(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert b"QR Event Tracker" in res.data
