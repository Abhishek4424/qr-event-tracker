"""
Comprehensive test suite for QR Event Tracker Flask application.
Tests all endpoints, edge cases, error handling, and data validation.
"""

import os
import json
import sqlite3
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image
import qrcode

os.environ["TESTING"] = "1"
os.environ["SECRET_KEY"] = "test-secret-key"

import app
from app import init_db, get_db, generate_short_code, build_tagged_url, parse_scan_data


@pytest.fixture
def client():
    """Create test client with temporary database."""
    db_fd, db_path = tempfile.mkstemp()
    app.app.config["TESTING"] = True
    app.app.config["DATABASE"] = db_path
    app.DATABASE = db_path
    
    with app.app.test_client() as client:
        with app.app.app_context():
            init_db()
        yield client
    
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def populated_db(client):
    """Fixture with pre-populated test data."""
    with app.app.app_context():
        db = get_db()
        
        # Insert test event
        cursor = db.execute(
            "INSERT INTO events (name, description, login_url) VALUES (?, ?, ?)",
            ("Test Event", "Test Description", "https://example.com/login")
        )
        event_id = cursor.lastrowid
        
        # Insert inactive event
        cursor = db.execute(
            "INSERT INTO events (name, description, login_url, is_active) VALUES (?, ?, ?, ?)",
            ("Inactive Event", "Inactive", "https://example.com", 0)
        )
        inactive_event_id = cursor.lastrowid
        
        # Insert QR codes
        cursor = db.execute("""
            INSERT INTO qr_codes (
                event_id, label, utm_source, utm_medium, utm_campaign,
                utm_content, utm_term, tagged_url, short_code, qr_color, error_correction
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_id, "Test QR", "qrcode", "event_print", "test_campaign",
            "booth_1", "term1", "https://example.com/login?utm_source=qrcode",
            "abc12345", "#0F2B3C", "H"
        ))
        qr_id = cursor.lastrowid
        
        # Insert inactive QR code
        cursor = db.execute("""
            INSERT INTO qr_codes (
                event_id, label, utm_campaign, utm_content, tagged_url, 
                short_code, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            event_id, "Inactive QR", "campaign", "content", 
            "https://example.com", "xyz789", 0
        ))
        
        # Insert scan data with various scenarios
        scans_data = [
            # Normal scan
            ("abc12345", "fingerprint1", "Mozilla/5.0", "mobile", "Apple", "iPhone", 
             "iOS", "14.0", "Safari", "14.0", 1, 0, 0, "en-US", "", 1, "New York", "NY", "USA"),
            # Duplicate scan (same fingerprint)
            ("abc12345", "fingerprint1", "Mozilla/5.0", "mobile", "Apple", "iPhone", 
             "iOS", "14.0", "Safari", "14.0", 1, 0, 0, "en-US", "", 0, "New York", "NY", "USA"),
            # Bot scan
            ("abc12345", "fingerprint2", "Googlebot", "desktop", "Unknown", "Unknown", 
             "Unknown", "", "Unknown", "", 0, 0, 1, "", "", 1, "", "", ""),
            # Tablet scan
            ("abc12345", "fingerprint3", "Mozilla/5.0 (iPad)", "tablet", "Apple", "iPad", 
             "iOS", "15.0", "Safari", "15.0", 0, 1, 0, "fr-FR", "https://google.com", 1, "Paris", "IDF", "France"),
        ]
        
        for scan in scans_data:
            db.execute("""
                INSERT INTO scans (
                    qr_id, fingerprint, user_agent, device_type, device_brand, device_model,
                    os_name, os_version, browser_name, browser_version,
                    is_mobile, is_tablet, is_bot, accept_language, referer, is_unique,
                    city, region, country
                ) VALUES (
                    (SELECT id FROM qr_codes WHERE short_code = ?),
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, scan)
        
        db.commit()
    
    return {
        "event_id": event_id,
        "inactive_event_id": inactive_event_id,
        "qr_id": qr_id,
        "short_code": "abc12345"
    }


class TestDatabaseInit:
    """Test database initialization and teardown."""
    
    def test_init_db_creates_tables(self, client):
        """Verify all tables are created with correct schema."""
        with app.app.app_context():
            db = get_db()
            cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            
            expected_tables = {"events", "qr_codes", "scans"}
            assert expected_tables.issubset(tables)
    
    def test_init_db_creates_indexes(self, client):
        """Verify all indexes are created."""
        with app.app.app_context():
            db = get_db()
            cursor = db.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = {row[0] for row in cursor.fetchall()}
            
            expected_indexes = {
                "idx_scans_qr_id", 
                "idx_scans_scanned_at",
                "idx_scans_fingerprint",
                "idx_qr_codes_event_id",
                "idx_qr_codes_short_code"
            }
            assert expected_indexes.issubset(indexes)
    
    def test_foreign_keys_enabled(self, client):
        """Test foreign key constraints are enforced."""
        with app.app.app_context():
            db = get_db()
            
            # Try to insert QR code with invalid event_id
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO qr_codes (event_id, label, utm_campaign, utm_content, tagged_url) VALUES (?, ?, ?, ?, ?)",
                    (9999, "Test", "campaign", "content", "https://example.com")
                )
                db.commit()


class TestHelperFunctions:
    """Test utility/helper functions."""
    
    def test_generate_short_code(self):
        """Test short code generation."""
        code1 = generate_short_code(1)
        code2 = generate_short_code(1)
        code3 = generate_short_code(2)
        
        # Should be 8 characters
        assert len(code1) == 8
        assert len(code2) == 8
        assert len(code3) == 8
        
        # Different timestamps should yield different codes
        assert code1 != code2
        
        # Should be hexadecimal
        assert all(c in "0123456789abcdef" for c in code1)
    
    def test_build_tagged_url_basic(self):
        """Test UTM parameter addition to URLs."""
        base_url = "https://example.com/login"
        params = {
            "utm_source": "qrcode",
            "utm_medium": "print",
            "utm_campaign": "event2024",
            "utm_content": "booth_a"
        }
        
        result = build_tagged_url(base_url, params)
        assert "utm_source=qrcode" in result
        assert "utm_medium=print" in result
        assert "utm_campaign=event2024" in result
        assert "utm_content=booth_a" in result
    
    def test_build_tagged_url_existing_params(self):
        """Test UTM addition when URL already has parameters."""
        base_url = "https://example.com/login?existing=param"
        params = {
            "utm_source": "qrcode",
            "utm_campaign": "test"
        }
        
        result = build_tagged_url(base_url, params)
        assert "existing=param" in result
        assert "utm_source=qrcode" in result
        assert "utm_campaign=test" in result
    
    def test_build_tagged_url_empty_params(self):
        """Test handling of empty UTM parameters."""
        base_url = "https://example.com"
        params = {
            "utm_source": "",
            "utm_campaign": "test",
            "utm_content": ""
        }
        
        result = build_tagged_url(base_url, params)
        assert "utm_source" not in result  # Empty params should be excluded
        assert "utm_campaign=test" in result
        assert "utm_content" not in result
    
    def test_build_tagged_url_special_characters(self):
        """Test URL encoding of special characters."""
        base_url = "https://example.com"
        params = {
            "utm_campaign": "test campaign",
            "utm_content": "booth&stand#1"
        }
        
        result = build_tagged_url(base_url, params)
        assert "test+campaign" in result or "test%20campaign" in result
        assert "%26" in result  # & encoded
        assert "%23" in result  # # encoded
    
    def test_parse_scan_data_mobile(self):
        """Test parsing mobile user agent."""
        req = MagicMock()
        req.headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)",
            "X-Forwarded-For": "192.168.1.1"
        }
        req.remote_addr = "10.0.0.1"
        
        data = parse_scan_data(req)
        
        assert data["is_mobile"] == 1
        assert data["is_tablet"] == 0
        assert data["is_bot"] == 0
        assert len(data["fingerprint"]) == 24
        assert len(data["ip_hash"]) == 16
    
    def test_parse_scan_data_bot(self):
        """Test bot detection."""
        req = MagicMock()
        req.headers = {
            "User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)",
        }
        req.remote_addr = "66.249.64.1"
        
        data = parse_scan_data(req)
        
        assert data["is_bot"] == 1
        assert data["is_mobile"] == 0
    
    def test_parse_scan_data_missing_headers(self):
        """Test handling of missing headers."""
        req = MagicMock()
        req.headers = {}
        req.remote_addr = None
        
        data = parse_scan_data(req)
        
        assert data["user_agent"] == ""
        assert data["ip_hash"] is not None  # Should hash "unknown"
        assert data["fingerprint"] is not None


class TestEventEndpoints:
    """Test event management endpoints."""
    
    def test_list_events_empty(self, client):
        """Test listing events when database is empty."""
        response = client.get("/api/events")
        assert response.status_code == 200
        assert json.loads(response.data) == []
    
    def test_list_events_populated(self, client, populated_db):
        """Test listing active events only."""
        response = client.get("/api/events")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert len(data) == 1  # Only active event
        assert data[0]["name"] == "Test Event"
        assert data[0]["is_active"] == 1
    
    def test_create_event_valid(self, client):
        """Test creating event with valid data."""
        event_data = {
            "name": "New Event",
            "description": "Test Description",
            "login_url": "https://example.com/login"
        }
        
        response = client.post("/api/events", 
                              json=event_data,
                              content_type="application/json")
        assert response.status_code == 201
        
        data = json.loads(response.data)
        assert "id" in data
        assert data["message"] == "Event created"
    
    def test_create_event_missing_name(self, client):
        """Test validation: missing name."""
        event_data = {
            "description": "Test",
            "login_url": "https://example.com"
        }
        
        response = client.post("/api/events",
                              json=event_data,
                              content_type="application/json")
        assert response.status_code == 400
        assert "name and login_url are required" in json.loads(response.data)["error"]
    
    def test_create_event_missing_url(self, client):
        """Test validation: missing login_url."""
        event_data = {
            "name": "Event"
        }
        
        response = client.post("/api/events",
                              json=event_data,
                              content_type="application/json")
        assert response.status_code == 400
    
    def test_create_event_empty_strings(self, client):
        """Test validation: empty string values."""
        event_data = {
            "name": "",
            "login_url": ""
        }
        
        response = client.post("/api/events",
                              json=event_data,
                              content_type="application/json")
        assert response.status_code == 400
    
    def test_delete_event(self, client, populated_db):
        """Test soft delete of event."""
        response = client.delete(f"/api/events/{populated_db['event_id']}")
        assert response.status_code == 200
        
        # Verify it's soft deleted (is_active = 0)
        with app.app.app_context():
            db = get_db()
            event = db.execute(
                "SELECT is_active FROM events WHERE id = ?",
                (populated_db['event_id'],)
            ).fetchone()
            assert event["is_active"] == 0
    
    def test_delete_nonexistent_event(self, client):
        """Test deleting non-existent event."""
        response = client.delete("/api/events/9999")
        assert response.status_code == 200  # Still returns 200


class TestQRCodeEndpoints:
    """Test QR code management endpoints."""
    
    def test_list_qr_codes_all(self, client, populated_db):
        """Test listing all active QR codes."""
        response = client.get("/api/qr")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert len(data) == 1  # Only active QR codes
        assert data[0]["label"] == "Test QR"
    
    def test_list_qr_codes_by_event(self, client, populated_db):
        """Test filtering QR codes by event."""
        response = client.get(f"/api/qr?event_id={populated_db['event_id']}")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert len(data) == 1
        assert all(qr["event_id"] == populated_db['event_id'] for qr in data)
    
    def test_create_qr_valid(self, client, populated_db):
        """Test creating QR code with valid data."""
        qr_data = {
            "event_id": populated_db['event_id'],
            "label": "New QR",
            "utm_campaign": "test_campaign",
            "utm_content": "location_1",
            "utm_term": "optional_term",
            "qr_color": "#FF0000",
            "error_correction": "M"
        }
        
        response = client.post("/api/qr",
                              json=qr_data,
                              content_type="application/json")
        assert response.status_code == 201
        
        data = json.loads(response.data)
        assert "id" in data
        assert "short_code" in data
        assert len(data["short_code"]) == 8
        assert "tagged_url" in data
        assert "utm_campaign=test_campaign" in data["tagged_url"]
    
    def test_create_qr_missing_required(self, client, populated_db):
        """Test validation: missing required fields."""
        test_cases = [
            {},  # All missing
            {"event_id": 1},  # Missing label, campaign, content
            {"event_id": 1, "label": "Test"},  # Missing campaign, content
            {"event_id": 1, "label": "Test", "utm_campaign": "camp"},  # Missing content
        ]
        
        for qr_data in test_cases:
            response = client.post("/api/qr",
                                  json=qr_data,
                                  content_type="application/json")
            assert response.status_code == 400
            assert "is required" in json.loads(response.data)["error"]
    
    def test_create_qr_invalid_event(self, client):
        """Test creating QR for non-existent event."""
        qr_data = {
            "event_id": 9999,
            "label": "Test",
            "utm_campaign": "campaign",
            "utm_content": "content"
        }
        
        response = client.post("/api/qr",
                              json=qr_data,
                              content_type="application/json")
        assert response.status_code == 404
        assert "Event not found" in json.loads(response.data)["error"]
    
    def test_create_qr_default_values(self, client, populated_db):
        """Test default values for optional fields."""
        qr_data = {
            "event_id": populated_db['event_id'],
            "label": "Minimal QR",
            "utm_campaign": "campaign",
            "utm_content": "content"
        }
        
        response = client.post("/api/qr",
                              json=qr_data,
                              content_type="application/json")
        assert response.status_code == 201
        
        # Check defaults were applied
        with app.app.app_context():
            db = get_db()
            qr = db.execute(
                "SELECT * FROM qr_codes WHERE id = ?",
                (json.loads(response.data)["id"],)
            ).fetchone()
            assert qr["utm_source"] == "qrcode"
            assert qr["utm_medium"] == "event_print"
            assert qr["qr_color"] == "#0F2B3C"
            assert qr["error_correction"] == "H"
    
    def test_delete_qr(self, client, populated_db):
        """Test soft delete of QR code."""
        response = client.delete(f"/api/qr/{populated_db['qr_id']}")
        assert response.status_code == 200
        
        # Verify soft deleted
        with app.app.app_context():
            db = get_db()
            qr = db.execute(
                "SELECT is_active FROM qr_codes WHERE id = ?",
                (populated_db['qr_id'],)
            ).fetchone()
            assert qr["is_active"] == 0


class TestQRCodeGeneration:
    """Test QR code image generation endpoints."""
    
    def test_download_qr_png(self, client, populated_db):
        """Test PNG download."""
        response = client.get(f"/api/qr/{populated_db['qr_id']}/download/png")
        assert response.status_code == 200
        assert response.content_type == "image/png"
        
        # Verify it's a valid PNG
        img = Image.open(BytesIO(response.data))
        assert img.format == "PNG"
    
    def test_download_qr_jpeg(self, client, populated_db):
        """Test JPEG download."""
        response = client.get(f"/api/qr/{populated_db['qr_id']}/download/jpeg")
        assert response.status_code == 200
        assert response.content_type == "image/jpeg"
        
        # Verify it's a valid JPEG
        img = Image.open(BytesIO(response.data))
        assert img.format == "JPEG"
    
    def test_download_qr_svg(self, client, populated_db):
        """Test SVG download."""
        response = client.get(f"/api/qr/{populated_db['qr_id']}/download/svg")
        assert response.status_code == 200
        assert "image/svg+xml" in response.content_type
        assert b"<svg" in response.data
    
    def test_download_qr_custom_size(self, client, populated_db):
        """Test custom size parameter."""
        response = client.get(f"/api/qr/{populated_db['qr_id']}/download/png?size=20")
        assert response.status_code == 200
        
        img = Image.open(BytesIO(response.data))
        # Larger size should result in larger image
        assert img.size[0] > 100
    
    def test_download_qr_invalid_format(self, client, populated_db):
        """Test invalid format handling."""
        response = client.get(f"/api/qr/{populated_db['qr_id']}/download/invalid")
        # Should default to JPEG
        assert response.status_code == 200
        assert response.content_type == "image/jpeg"
    
    def test_download_nonexistent_qr(self, client):
        """Test downloading non-existent QR."""
        response = client.get("/api/qr/9999/download/png")
        assert response.status_code == 404
    
    def test_preview_qr(self, client, populated_db):
        """Test QR preview endpoint."""
        response = client.get(f"/api/qr/{populated_db['qr_id']}/preview")
        assert response.status_code == 200
        assert response.content_type == "image/png"
        
        img = Image.open(BytesIO(response.data))
        assert img.format == "PNG"


class TestScanTracking:
    """Test QR code scan tracking."""
    
    def test_scan_redirect_valid(self, client, populated_db):
        """Test successful scan and redirect."""
        response = client.get(f"/s/{populated_db['short_code']}", 
                             headers={"User-Agent": "Mozilla/5.0"},
                             follow_redirects=False)
        assert response.status_code == 302
        assert response.location.startswith("https://example.com/login")
        
        # Verify scan was recorded
        with app.app.app_context():
            db = get_db()
            scan = db.execute(
                "SELECT * FROM scans WHERE qr_id = ? ORDER BY id DESC LIMIT 1",
                (populated_db['qr_id'],)
            ).fetchone()
            assert scan is not None
    
    def test_scan_redirect_invalid_code(self, client):
        """Test scanning invalid short code."""
        response = client.get("/s/invalid_code")
        assert response.status_code == 404
    
    def test_scan_redirect_inactive_qr(self, client, populated_db):
        """Test scanning inactive QR code."""
        response = client.get("/s/xyz789")
        assert response.status_code == 404
    
    def test_scan_uniqueness_detection(self, client, populated_db):
        """Test duplicate scan detection."""
        headers = {"User-Agent": "TestAgent/1.0"}
        
        # First scan
        response1 = client.get(f"/s/{populated_db['short_code']}", 
                               headers=headers,
                               follow_redirects=False)
        assert response1.status_code == 302
        
        # Second scan with same fingerprint
        response2 = client.get(f"/s/{populated_db['short_code']}", 
                               headers=headers,
                               follow_redirects=False)
        assert response2.status_code == 302
        
        # Check uniqueness flags
        with app.app.app_context():
            db = get_db()
            scans = db.execute(
                "SELECT is_unique FROM scans WHERE qr_id = ? ORDER BY id DESC LIMIT 2",
                (populated_db['qr_id'],)
            ).fetchall()
            
            assert len(scans) >= 2
            # Second scan should be marked as not unique
            assert scans[0]["is_unique"] == 0
    
    def test_scan_with_geo_params(self, client, populated_db):
        """Test scan with geographic parameters."""
        response = client.get(
            f"/s/{populated_db['short_code']}?city=Boston&region=MA&country=USA",
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=False
        )
        assert response.status_code == 302
        
        # Verify geo data was recorded
        with app.app.app_context():
            db = get_db()
            scan = db.execute(
                "SELECT city, region, country FROM scans WHERE qr_id = ? ORDER BY id DESC LIMIT 1",
                (populated_db['qr_id'],)
            ).fetchone()
            assert scan["city"] == "Boston"
            assert scan["region"] == "MA"
            assert scan["country"] == "USA"


class TestAnalyticsEndpoints:
    """Test analytics and reporting endpoints."""
    
    def test_analytics_overview_all(self, client, populated_db):
        """Test overview analytics for all events."""
        response = client.get("/api/analytics/overview")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert data["total_scans"] == 4
        assert data["unique_scanners"] == 3
        assert data["repeat_scans"] == 1
        assert data["bot_scans"] == 1
        assert "peak_hour" in data
        assert "top_os" in data
        assert "top_city" in data
    
    def test_analytics_overview_filtered(self, client, populated_db):
        """Test overview filtered by event."""
        response = client.get(f"/api/analytics/overview?event_id={populated_db['event_id']}")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert data["total_scans"] == 4
    
    def test_analytics_overview_empty(self, client):
        """Test overview with no data."""
        response = client.get("/api/analytics/overview")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert data["total_scans"] == 0
        assert data["unique_scanners"] == 0
        assert data["repeat_rate"] == 0
    
    def test_analytics_timeline_hourly(self, client, populated_db):
        """Test hourly timeline analytics."""
        response = client.get("/api/analytics/timeline?granularity=hourly")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert isinstance(data, list)
        if data:
            assert "period" in data[0]
            assert "display" in data[0]
            assert "total" in data[0]
            assert "unique" in data[0]
    
    def test_analytics_timeline_daily(self, client, populated_db):
        """Test daily timeline analytics."""
        response = client.get("/api/analytics/timeline?granularity=daily")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert isinstance(data, list)
    
    def test_analytics_placements(self, client, populated_db):
        """Test placement performance analytics."""
        response = client.get("/api/analytics/placements")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) == 1  # Only one active QR code
        assert data[0]["utm_content"] == "booth_1"
        assert data[0]["total_scans"] == 4
        assert data[0]["unique_scanners"] == 3
    
    def test_analytics_personas(self, client, populated_db):
        """Test user persona analytics."""
        response = client.get("/api/analytics/personas")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert "device_types" in data
        assert "os" in data
        assert "browsers" in data
        assert "cities" in data
        assert "device_brands" in data
        assert "languages" in data
        
        # Check device type breakdown
        device_types = {d["name"]: d["count"] for d in data["device_types"]}
        assert "mobile" in device_types
        assert "tablet" in device_types
        assert "desktop" in device_types
    
    def test_analytics_live(self, client, populated_db):
        """Test live scan feed."""
        response = client.get("/api/analytics/live?limit=10")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) <= 10
        if data:
            assert "placement_label" in data[0]
            assert "utm_content" in data[0]
            assert "device_type" in data[0]
    
    def test_analytics_live_limit(self, client, populated_db):
        """Test live feed limit parameter."""
        response = client.get("/api/analytics/live?limit=2")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert len(data) <= 2
    
    def test_analytics_live_max_limit(self, client, populated_db):
        """Test live feed maximum limit enforcement."""
        response = client.get("/api/analytics/live?limit=500")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert len(data) <= 200  # Max is 200


class TestDataExport:
    """Test data export functionality."""
    
    def test_export_csv(self, client, populated_db):
        """Test CSV export."""
        response = client.get("/api/analytics/export?format=csv")
        assert response.status_code == 200
        assert "text/csv" in response.content_type
        assert b"scanned_at" in response.data  # Header
        assert b"device_type" in response.data
    
    def test_export_json(self, client, populated_db):
        """Test JSON export."""
        response = client.get("/api/analytics/export?format=json")
        assert response.status_code == 200
        assert response.content_type == "application/json"
        
        data = json.loads(response.data)
        assert isinstance(data, list)
        if data:
            assert "scanned_at" in data[0]
            assert "utm_campaign" in data[0]
    
    def test_export_filtered_by_event(self, client, populated_db):
        """Test filtered export."""
        response = client.get(f"/api/analytics/export?event_id={populated_db['event_id']}&format=json")
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert len(data) == 4  # All scans for this event
    
    def test_export_empty(self, client):
        """Test export with no data."""
        response = client.get("/api/analytics/export?format=csv")
        assert response.status_code == 200
        assert response.data == b""  # Empty CSV


class TestGeoEnrichment:
    """Test geographic data enrichment."""
    
    def test_update_scan_geo(self, client, populated_db):
        """Test updating scan with geo data."""
        # First create a scan
        with app.app.app_context():
            db = get_db()
            cursor = db.execute(
                "INSERT INTO scans (qr_id, fingerprint) VALUES (?, ?)",
                (populated_db['qr_id'], "test_fingerprint")
            )
            scan_id = cursor.lastrowid
            db.commit()
        
        geo_data = {
            "city": "Seattle",
            "region": "WA",
            "country": "USA",
            "latitude": 47.6062,
            "longitude": -122.3321,
            "screen_width": 1920,
            "screen_height": 1080
        }
        
        response = client.patch(f"/api/scans/{scan_id}/geo",
                               json=geo_data,
                               content_type="application/json")
        assert response.status_code == 200
        
        # Verify data was updated
        with app.app.app_context():
            db = get_db()
            scan = db.execute(
                "SELECT * FROM scans WHERE id = ?",
                (scan_id,)
            ).fetchone()
            assert scan["city"] == "Seattle"
            assert scan["region"] == "WA"
            assert scan["latitude"] == 47.6062
            assert scan["screen_width"] == 1920


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_malformed_json(self, client):
        """Test handling of malformed JSON."""
        response = client.post("/api/events",
                              data="not json",
                              content_type="application/json")
        assert response.status_code == 400
    
    def test_sql_injection_attempt(self, client):
        """Test SQL injection protection."""
        # Try SQL injection in event creation
        event_data = {
            "name": "'; DROP TABLE events; --",
            "login_url": "https://example.com"
        }
        response = client.post("/api/events",
                              json=event_data,
                              content_type="application/json")
        assert response.status_code == 201  # Should succeed without executing injection
        
        # Verify table still exists
        response = client.get("/api/events")
        assert response.status_code == 200
    
    def test_xss_prevention(self, client, populated_db):
        """Test XSS prevention in stored data."""
        qr_data = {
            "event_id": populated_db['event_id'],
            "label": "<script>alert('xss')</script>",
            "utm_campaign": "test",
            "utm_content": "test"
        }
        
        response = client.post("/api/qr",
                              json=qr_data,
                              content_type="application/json")
        assert response.status_code == 201
        
        # Data should be stored as-is (escaped by template engine on display)
        with app.app.app_context():
            db = get_db()
            qr = db.execute(
                "SELECT label FROM qr_codes WHERE id = ?",
                (json.loads(response.data)["id"],)
            ).fetchone()
            assert qr["label"] == "<script>alert('xss')</script>"
    
    def test_large_data_handling(self, client, populated_db):
        """Test handling of large data inputs."""
        # Very long user agent
        long_ua = "A" * 1000
        response = client.get(f"/s/{populated_db['short_code']}",
                             headers={"User-Agent": long_ua},
                             follow_redirects=False)
        assert response.status_code == 302
        
        # Check it was truncated to 500 chars
        with app.app.app_context():
            db = get_db()
            scan = db.execute(
                "SELECT user_agent FROM scans ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert len(scan["user_agent"]) == 500
    
    def test_concurrent_short_code_generation(self, client, populated_db):
        """Test handling of concurrent QR creation."""
        # Create multiple QRs sequentially to test uniqueness of short codes
        results = []
        for i in range(5):
            qr_data = {
                "event_id": populated_db['event_id'],
                "label": f"Concurrent QR {i}",
                "utm_campaign": "test",
                "utm_content": f"test_{i}"
            }
            response = client.post("/api/qr", json=qr_data)
            results.append(response)
        
        # All should succeed
        assert all(r.status_code == 201 for r in results)
        
        # All should have unique short codes
        short_codes = [json.loads(r.data)["short_code"] for r in results]
        assert len(short_codes) == len(set(short_codes))
    
    def test_unicode_handling(self, client, populated_db):
        """Test Unicode character handling."""
        event_data = {
            "name": "テストイベント 🎉",  # Japanese + emoji
            "description": "Тестовое описание",  # Russian
            "login_url": "https://example.com"
        }
        
        response = client.post("/api/events",
                              json=event_data,
                              content_type="application/json")
        assert response.status_code == 201
        
        # Verify data integrity
        response = client.get("/api/events")
        data = json.loads(response.data)
        created_event = [e for e in data if "テスト" in e["name"]][0]
        assert created_event["name"] == "テストイベント 🎉"
        assert created_event["description"] == "Тестовое описание"
    
    def test_special_url_characters(self, client, populated_db):
        """Test URLs with special characters."""
        event_data = {
            "name": "Test",
            "login_url": "https://example.com/path?param=value&other=test#anchor"
        }
        
        response = client.post("/api/events",
                              json=event_data,
                              content_type="application/json")
        assert response.status_code == 201
        
        # Create QR for this event
        event_id = json.loads(response.data)["id"]
        qr_data = {
            "event_id": event_id,
            "label": "Test",
            "utm_campaign": "test",
            "utm_content": "test"
        }
        
        response = client.post("/api/qr", json=qr_data)
        assert response.status_code == 201
        
        # Check URL was properly constructed
        data = json.loads(response.data)
        assert "param=value" in data["tagged_url"]
        assert "other=test" in data["tagged_url"]
        assert "#anchor" in data["tagged_url"]


class TestPerformance:
    """Test performance-related scenarios."""
    
    def test_bulk_scan_insertion(self, client, populated_db):
        """Test handling of many scans."""
        # Simulate 100 rapid scans
        for i in range(100):
            headers = {"User-Agent": f"TestAgent/{i}"}
            response = client.get(f"/s/{populated_db['short_code']}",
                                 headers=headers,
                                 follow_redirects=False)
            assert response.status_code == 302
        
        # Verify all were recorded
        with app.app.app_context():
            db = get_db()
            count = db.execute(
                "SELECT COUNT(*) as cnt FROM scans WHERE qr_id = ?",
                (populated_db['qr_id'],)
            ).fetchone()
            assert count["cnt"] >= 100  # May include pre-existing scans
    
    def test_analytics_with_large_dataset(self, client, populated_db):
        """Test analytics performance with many scans."""
        # Add many scans
        with app.app.app_context():
            db = get_db()
            for i in range(500):
                db.execute("""
                    INSERT INTO scans (
                        qr_id, fingerprint, user_agent, device_type, 
                        city, country, is_unique
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    populated_db['qr_id'], f"fp_{i}", "Mozilla/5.0",
                    "mobile" if i % 2 else "desktop",
                    f"City_{i % 10}", "USA", 1
                ))
            db.commit()
        
        # Test various analytics endpoints
        endpoints = [
            "/api/analytics/overview",
            "/api/analytics/timeline",
            "/api/analytics/placements",
            "/api/analytics/personas",
            "/api/analytics/live?limit=50"
        ]
        
        for endpoint in endpoints:
            response = client.get(endpoint)
            assert response.status_code == 200


class TestSecurityHeaders:
    """Test security-related response headers and behaviors."""
    
    def test_no_directory_traversal(self, client):
        """Test protection against directory traversal."""
        # Attempt to access files outside app directory
        dangerous_paths = [
            "/s/../../../etc/passwd",
            "/api/qr/../../sensitive",
            "/api/events/%2e%2e%2f%2e%2e%2fconfig"
        ]
        
        for path in dangerous_paths:
            response = client.get(path)
            assert response.status_code in [404, 400]
    
    def test_rate_limiting_simulation(self, client, populated_db):
        """Simulate rate limiting scenario (app should handle gracefully)."""
        # Make many rapid requests
        for _ in range(50):
            response = client.get("/api/analytics/overview")
            assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])