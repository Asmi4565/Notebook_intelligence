"""
End-to-end persistence test for scans/uploads.
Tests the full cycle: upload → DB save → restart (fresh DB read) → verify data intact.

Run with:  python test_scan_persistence.py
"""

import os
import sys
import io
import json
import sqlite3
from pathlib import Path
from PIL import Image, ImageDraw

# ── Ensure project root is on path ──────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Use a fresh isolated test DB so we never touch production data
TEST_DB = ROOT / "data" / "test_persistence.db"
os.environ["NIS_DB_FILE"] = str(TEST_DB)

import db  # noqa: E402  (imported after env var is set)

# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def make_test_image() -> str:
    """Create a tiny in-memory scan and return it as a base64 data-url."""
    img = Image.new("RGB", (100, 100), color=(200, 220, 255))
    draw = ImageDraw.Draw(img)
    draw.text((10, 40), "Test Scan", fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    import base64
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


def clean_test_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
        print(f"  🧹 Removed old test DB: {TEST_DB}")


def check(label: str, condition: bool):
    icon = "✅" if condition else "❌"
    status = "PASS" if condition else "FAIL"
    print(f"  {icon} [{status}] {label}")
    return condition


# ────────────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────────────

NOTEBOOK_ID = "test_notebook_001"
PAGE_RECORD = {
    "page_id": f"{NOTEBOOK_ID}_p1",
    "page_number": 1,
    "source_filename": "test_scan.png",
    "image_data": None,   # filled below
    "ocr_text": '{"blocks":[{"type":"text","content":"Hello from OCR"}]}',
    "ocr_status": "success",
    "is_code": False,
    "ocr_error": None,
    "folder": "Lecture 1"
}


def test_save_and_reload():
    """Core test: save a page, simulate server restart (fresh DBConnection), reload it."""
    print("\n🔬 Test 1 — Save page → fresh load → data intact")

    PAGE_RECORD["image_data"] = make_test_image()

    # 1. Save
    db.save_pages(NOTEBOOK_ID, [PAGE_RECORD])
    print("  ➡️  db.save_pages() called")

    # 2. Simulate restart: db.get_db_pages() opens a brand-new SQLite connection
    rows = db.get_db_pages(NOTEBOOK_ID)

    ok = True
    ok &= check("At least 1 page returned",          len(rows) >= 1)
    if rows:
        r = rows[0]
        ok &= check("page_number == 1",               r["page_number"] == 1)
        ok &= check("source_filename == test_scan.png", r["source_filename"] == "test_scan.png")
        ok &= check("ocr_text stored",                bool(r["ocr_text"]))
        ok &= check("image_data stored",              bool(r.get("image_data")))
        ok &= check("folder == 'Lecture 1'",          r.get("folder") == "Lecture 1")
        ok &= check("ocr_status == success",          r["ocr_status"] == "success")
    return ok


def test_image_data_survives():
    """Verify the base64 image round-trips correctly (can be decoded back to PIL)."""
    print("\n🔬 Test 2 — Image base64 survives DB round-trip")

    rows = db.get_db_pages(NOTEBOOK_ID)
    if not rows or not rows[0].get("image_data"):
        check("image_data present in DB row", False)
        return False

    import base64
    raw = rows[0]["image_data"]
    if "," in raw:
        raw = raw.split(",")[1]
    try:
        img_bytes = base64.b64decode(raw)
        img = Image.open(io.BytesIO(img_bytes))
        check("Decoded image is valid PIL Image",  isinstance(img, Image.Image))
        check(f"Image size > 0 pixels ({img.size})", img.width > 0 and img.height > 0)
        return True
    except Exception as e:
        check(f"Image decode failed: {e}", False)
        return False


def test_per_user_isolation():
    """
    Verify user-scoped storage: pages saved under user_A should NOT
    appear when loading under user_B.
    """
    print("\n🔬 Test 3 — Per-user table isolation")

    page_a = {**PAGE_RECORD,
              "page_id": f"{NOTEBOOK_ID}_u_a_p1",
              "page_number": 10,
              "source_filename": "user_a_scan.png",
              "image_data": make_test_image()}

    page_b = {**PAGE_RECORD,
              "page_id": f"{NOTEBOOK_ID}_u_b_p1",
              "page_number": 11,
              "source_filename": "user_b_scan.png",
              "image_data": make_test_image()}

    user_a_id = "aaaaaaaaaaaa"
    user_b_id = "bbbbbbbbbbbb"

    db.save_pages(NOTEBOOK_ID, [page_a], user_id=user_a_id)
    db.save_pages(NOTEBOOK_ID, [page_b], user_id=user_b_id)

    rows_a = db.get_db_pages(NOTEBOOK_ID, user_id=user_a_id)
    rows_b = db.get_db_pages(NOTEBOOK_ID, user_id=user_b_id)

    filenames_a = {r["source_filename"] for r in rows_a}
    filenames_b = {r["source_filename"] for r in rows_b}

    ok = True
    ok &= check("User A sees their scan",              "user_a_scan.png" in filenames_a)
    ok &= check("User A does NOT see User B's scan",   "user_b_scan.png" not in filenames_a)
    ok &= check("User B sees their scan",              "user_b_scan.png" in filenames_b)
    ok &= check("User B does NOT see User A's scan",   "user_a_scan.png" not in filenames_b)
    return ok


def test_sqlite_file_persists():
    """Verify the SQLite file actually exists on disk after saving."""
    print("\n🔬 Test 4 — SQLite DB file exists on disk")
    exists = TEST_DB.exists()
    size   = TEST_DB.stat().st_size if exists else 0
    ok = True
    ok &= check(f"DB file exists at {TEST_DB}",       exists)
    ok &= check(f"DB file size > 0 ({size} bytes)",   size > 0)
    return ok


def test_upload_endpoint_user_id_logic():
    """
    Simulate the exact logic in app.py upload_files():
    - user_id=None  → generic 'pages' table
    - user_id set   → user-scoped table
    Verifies save_pages picks the right table.
    """
    print("\n🔬 Test 5 — app.py upload logic: anonymous vs authenticated")

    anon_page = {**PAGE_RECORD,
                 "page_id": f"{NOTEBOOK_ID}_anon_p1",
                 "page_number": 20,
                 "source_filename": "anon_scan.png",
                 "image_data": make_test_image()}

    auth_page = {**PAGE_RECORD,
                 "page_id": f"{NOTEBOOK_ID}_auth_p1",
                 "page_number": 21,
                 "source_filename": "auth_scan.png",
                 "image_data": make_test_image()}

    # Anonymous upload (no user_id, mirrors app.py line 893-894 when user=None)
    db.save_pages(NOTEBOOK_ID, [anon_page], user_id=None)

    # Authenticated upload
    user_c_id = "cccccccccccc"
    db.save_pages(NOTEBOOK_ID, [auth_page], user_id=user_c_id)

    anon_rows = db.get_db_pages(NOTEBOOK_ID, user_id=None)
    auth_rows = db.get_db_pages(NOTEBOOK_ID, user_id=user_c_id)

    anon_fnames = {r["source_filename"] for r in anon_rows}
    auth_fnames = {r["source_filename"] for r in auth_rows}

    ok = True
    ok &= check("Anonymous scan is in generic pages table",  "anon_scan.png" in anon_fnames)
    ok &= check("Auth scan is in user-scoped table",         "auth_scan.png" in auth_fnames)
    ok &= check("Auth scan NOT in anonymous table",          "auth_scan.png" not in anon_fnames)
    return ok


# ────────────────────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  🧪 NIS Scan Persistence Test Suite")
    print("=" * 60)

    # Start fresh
    clean_test_db()

    results = []
    results.append(("Save & reload", test_save_and_reload()))
    results.append(("Image round-trip", test_image_data_survives()))
    results.append(("Per-user isolation", test_per_user_isolation()))
    results.append(("SQLite file on disk", test_sqlite_file_persists()))
    results.append(("Upload endpoint logic", test_upload_endpoint_user_id_logic()))

    print("\n" + "=" * 60)
    print("  📊 Summary")
    print("=" * 60)
    passed = sum(1 for _, r in results if r)
    for name, ok in results:
        icon = "✅" if ok else "❌"
        print(f"  {icon}  {name}")
    print(f"\n  {passed}/{len(results)} tests passed")

    # Cleanup
    if TEST_DB.exists():
        TEST_DB.unlink()
        print(f"  🧹 Cleaned up test DB")

    sys.exit(0 if passed == len(results) else 1)
