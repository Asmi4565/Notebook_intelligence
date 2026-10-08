"""
Automated unit, integration, and schema validation test suite for NIS Phase 1 modules,
including AI Assistant grounded explanations, quiz generation, and FastAPI endpoints.
"""

import os
import json
import uuid
# Ensure test_suite runs deterministic offline unit tests without network calls
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)

import io
from PIL import Image, ImageDraw
import pymupdf
from fastapi.testclient import TestClient

from document_processor import (
    validate_file,
    extract_pages_from_pdf,
    extract_pages_from_image,
    PageRecord
)
from ocr_engine import perform_ocr_on_page, batch_ocr_pages, get_gemini_client
from search_indexer import SearchIndexer
from code_sandbox import execute_python_code
from ai_assistant import (
    explain_topic,
    generate_quiz,
    registry,
    ExplainResponse,
    QuizQuestionItem
)
from app import api

# Ensure test_suite runs offline unit tests without external API rate-limit delays
os.environ["GEMINI_API_KEY"] = ""
os.environ["GOOGLE_API_KEY"] = ""

# FastAPI TestClient
client = TestClient(api)


def create_dummy_image(text="Sample Note"):
    img = Image.new("RGB", (600, 800), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((50, 50), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def create_dummy_pdf():
    doc = pymupdf.open()
    page1 = doc.new_page(width=595, height=842)
    page1.insert_text((50, 72), "Handwritten Lecture 1: Binary Search Tree properties.")
    page2 = doc.new_page(width=595, height=842)
    page2.insert_text((50, 72), "Lecture 2: Quicksort partitioning and Big-O complexity.")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


# ==================== Core Module Tests ====================

def test_file_validation():
    print("Testing file validation...")
    valid, msg = validate_file("test.pdf", b"123")
    assert valid, f"Expected valid, got: {msg}"

    valid, msg = validate_file("test.exe", b"123")
    assert not valid, "Expected rejection for .exe file"

    valid, msg = validate_file("empty.png", b"")
    assert not valid, "Expected rejection for empty file"
    print("[OK] File validation passed.")


def test_pdf_extraction():
    print("Testing PDF multi-page extraction...")
    pdf_data = create_dummy_pdf()
    pages = extract_pages_from_pdf(pdf_data, "lecture.pdf", dpi=100)
    assert len(pages) == 2, f"Expected 2 pages, got {len(pages)}"
    assert pages[0].page_number == 1
    assert pages[1].page_number == 2
    assert isinstance(pages[0].image, Image.Image)
    print("[OK] PDF page extraction passed.")


def test_ocr_processing():
    print("Testing OCR processing without key...")
    img_data = create_dummy_image()
    page = extract_pages_from_image(img_data, "notes.png", page_number=1)
    text, is_code, err = perform_ocr_on_page(page, api_key=None)
    assert err is not None, "Expected error when API key is missing"
    assert "API key missing" in err
    print("[OK] OCR processing error handling passed.")


def test_ocr_key_sanitization():
    print("Testing OCR key sanitization...")
    genai_client = get_gemini_client(api_key="   ")
    assert genai_client is None, "Expected None client for whitespace API key"

    img_data = create_dummy_image()
    page = extract_pages_from_image(img_data, "notes.png", page_number=1)
    text, is_code, err = perform_ocr_on_page(page, api_key="   ")
    assert err is not None
    assert "API key missing" in err
    print("[OK] OCR key sanitization passed.")


def test_batch_ocr_processing():
    print("Testing batch OCR processing...")
    img_data = create_dummy_image()
    p1 = extract_pages_from_image(img_data, "notes1.png", page_number=1)
    p2 = extract_pages_from_image(img_data, "notes2.png", page_number=2)

    progress_log = []
    def on_progress(cur, total, page):
        progress_log.append((cur, total, page.page_number))

    pages = batch_ocr_pages([p1, p2], api_key=None, progress_callback=on_progress)
    assert len(pages) == 2
    assert pages[0].ocr_status == "error"
    assert pages[1].ocr_status == "error"
    assert len(progress_log) == 2
    print("[OK] Batch OCR processing passed.")


def test_search_and_indexing():
    print("Testing Search Indexer...")
    indexer = SearchIndexer()
    p1 = PageRecord(
        page_id="p1",
        page_number=1,
        source_filename="algo_notes.pdf",
        image=Image.new("RGB", (100, 100)),
        ocr_text="Binary Search Trees provide O(log n) lookup on balanced trees. Binary trees have nodes.",
        ocr_status="success"
    )
    p2 = PageRecord(
        page_id="p2",
        page_number=2,
        source_filename="algo_notes.pdf",
        image=Image.new("RGB", (100, 100)),
        ocr_text="Quicksort uses divide and conquer. Average complexity is O(n log n).",
        ocr_status="success"
    )

    indexer.update_index([p1, p2])

    results = indexer.search("Binary")
    assert len(results) == 1
    assert results[0].page_number == 1
    assert results[0].match_count == 2
    assert "<mark" in results[0].snippets[0]

    results2 = indexer.search("O(n log n)")
    assert len(results2) == 1
    assert results2[0].page_number == 2

    print("[OK] Search and indexing passed.")


def test_diagram_ocr_normalizer_and_blocks():
    print("Testing Diagram OCR block normalization & structure...")
    from ocr_engine import normalize_ocr_response
    import json

    raw_json = '{"blocks": [{"type": "text", "content": "Sample"}, {"type": "diagram", "mermaid": "flowchart LR\\n  A --> B"}]}'
    normalized = normalize_ocr_response(raw_json)
    data = json.loads(normalized)
    assert "blocks" in data
    assert len(data["blocks"]) == 2
    assert data["blocks"][1]["type"] == "diagram"
    assert "flowchart LR" in data["blocks"][1]["mermaid"]
    print("[OK] Diagram OCR block normalization passed.")



def test_diagram_search_indexing():
    print("Testing diagram node label search indexing...")
    indexer = SearchIndexer()
    diagram_json = json.dumps({
        "blocks": [
            {"type": "text", "content": "OSI vs TCP/IP Layer Mapping Notes"},
            {"type": "diagram", "mermaid": "flowchart LR\n  A5[5. Session] --> B4[Application Layer]"}
        ]
    })
    p_diag = PageRecord(
        page_id="p_diag",
        page_number=1,
        source_filename="diagram_notes.pdf",
        image=Image.new("RGB", (100, 100)),
        ocr_text=diagram_json,
        ocr_status="success"
    )
    indexer.update_index([p_diag])

    # Search for node label "Session"
    results = indexer.search("Session")
    assert len(results) == 1, "Expected 1 search result for diagram node label 'Session'"
    assert results[0].page_number == 1
    assert "<mark" in results[0].snippets[0]

    # Search for layer relationship node "Application Layer"
    results_app = indexer.search("Application Layer")
    assert len(results_app) == 1
    print("[OK] Diagram node label search indexing passed.")


def test_mermaid_label_extraction():
    print("Testing Mermaid diagram node label extraction...")
    from search_indexer import extract_searchable_text
    
    # Test JSON diagram block extraction
    json_ocr = json.dumps({
        "blocks": [
            {"type": "diagram", "mermaid": "flowchart LR\n  A[Physical Layer] --> B[Data Link Layer]"}
        ]
    })
    text_from_json = extract_searchable_text(json_ocr)
    assert "Physical Layer" in text_from_json
    assert "Data Link Layer" in text_from_json

    # Test raw ```mermaid fenced code block extraction
    raw_mermaid = "Here is a diagram:\n```mermaid\nflowchart TD\n  X[Session] --> Y[Transport]\n```"
    text_from_raw = extract_searchable_text(raw_mermaid)
    assert "Session" in text_from_raw
    assert "Transport" in text_from_raw
    print("[OK] Mermaid label extraction test passed.")


def test_diagram_relationship_quiz():
    print("Testing quiz generator diagram relationship questions...")
    quiz = generate_quiz(notebook_id="default_academic", num_questions=3)
    assert len(quiz) == 3
    # Verify one question tests diagram relationships (e.g. Session layer mapping to Application Layer)
    has_diag_q = any("Session" in q["question"] or "OSI" in q["question"] for q in quiz)
    assert has_diag_q, "Expected quiz to contain diagram relationship question"
    print("[OK] Diagram relationship quiz generation passed.")


# ==================== Code Sandbox Security & Execution Tests ====================

def test_sandbox_normal_output():
    print("Testing Sandbox normal output: print('test')...")
    res = execute_python_code('print("test")')
    assert res.exit_code == 0, f"Expected exit code 0, got {res.exit_code}"
    assert res.stdout.strip() == "test", f"Expected 'test', got '{res.stdout.strip()}'"
    assert res.timed_out is False
    print("[OK] Sandbox normal output passed.")


def test_sandbox_timeout():
    print("Testing Sandbox timeout: while True: pass...")
    timeout_code = "while True:\n    pass"
    res = execute_python_code(timeout_code, timeout_seconds=1)
    assert res.timed_out is True, "Expected timed_out to be True"
    assert res.exit_code != 0, "Expected non-zero exit code on timeout"
    assert "timed out" in res.stderr.lower(), f"Expected timeout message in stderr, got: {res.stderr}"
    print("[OK] Sandbox timeout handling passed.")


def test_sandbox_blocked_network():
    print("Testing Sandbox blocked network access...")
    net_code = "import socket\ns = socket.socket()"
    res = execute_python_code(net_code)
    assert res.exit_code != 0, "Expected non-zero exit code for unauthorized socket access"
    assert "PermissionError" in res.stderr or "blocked" in res.stderr.lower(), (
        f"Expected PermissionError in stderr, got: {res.stderr}"
    )
    print("[OK] Sandbox blocked network access passed.")


def test_sandbox_blocked_filesystem_write():
    print("Testing Sandbox blocked filesystem write outside sandbox...")
    fs_code = "with open('../unauthorized_escape.txt', 'w') as f:\n    f.write('bad')"
    res = execute_python_code(fs_code)
    assert res.exit_code != 0, "Expected non-zero exit code for writing outside sandbox"
    assert "PermissionError" in res.stderr or "blocked" in res.stderr.lower(), (
        f"Expected PermissionError in stderr, got: {res.stderr}"
    )
    print("[OK] Sandbox blocked filesystem write passed.")



# ==================== AI Assistant Schema & Logic Tests ====================

def test_explain_topic_schema():
    print("Testing explain_topic schema validation (FR-10, FR-11)...")
    res = explain_topic(query="What is the recurrence relation for Quicksort?", notebook_id="default_academic")

    # Validate output schema
    assert isinstance(res, dict), "Expected dict response"
    assert "notebook_id" in res and res["notebook_id"] == "default_academic"
    assert "query" in res and len(res["query"]) > 0
    assert "explanation" in res and len(res["explanation"]) > 0
    assert "citations" in res and isinstance(res["citations"], list)
    assert len(res["citations"]) > 0, "Expected at least one cited page number"
    for cite in res["citations"]:
        assert isinstance(cite, int), f"Citation {cite} is not an integer"

    # Verify Pydantic model validates
    validated = ExplainResponse(**res)
    assert validated.notebook_id == "default_academic"
    print("[OK] explain_topic schema validation passed.")


def test_generate_quiz_schema():
    print("Testing generate_quiz schema validation (FR-12, FR-13, SRS Sec 6.3)...")
    quiz = generate_quiz(notebook_id="default_academic", num_questions=3)

    assert isinstance(quiz, list), "Expected list of quiz questions"
    assert len(quiz) == 3, f"Expected 3 questions, got {len(quiz)}"

    # Validate Section 6.3 schema for every item
    for item in quiz:
        assert "question" in item and isinstance(item["question"], str) and len(item["question"]) > 0
        assert "options" in item and isinstance(item["options"], list) and len(item["options"]) >= 2
        assert "correct_answer" in item and isinstance(item["correct_answer"], str)
        assert "explanation" in item and isinstance(item["explanation"], str)

        # Check that correct_answer is one of the options
        assert item["correct_answer"] in item["options"], (
            f"correct_answer '{item['correct_answer']}' not found in options {item['options']}"
        )

        # Validate with Pydantic model
        QuizQuestionItem(**item)

    # Test page_range filter
    filtered_quiz = generate_quiz(notebook_id="default_academic", page_range=(1, 2), num_questions=2)
    assert len(filtered_quiz) == 2
    print("[OK] generate_quiz schema validation passed.")


# ==================== FastAPI Endpoint Tests ====================

def test_api_explain_endpoint():
    print("Testing POST /explain endpoint...")
    payload = {
        "query": "Explain Master Theorem case 2",
        "notebook_id": "default_academic"
    }
    response = client.post("/explain", json=payload)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    data = response.json()
    assert data["notebook_id"] == "default_academic"
    assert "explanation" in data and len(data["explanation"]) > 0
    assert "citations" in data and len(data["citations"]) > 0

    # Validation error test (empty query)
    bad_resp = client.post("/explain", json={"query": "", "notebook_id": "default_academic"})
    assert bad_resp.status_code == 422, "Expected 422 unprocessable entity for empty query"
    print("[OK] POST /explain endpoint passed.")


def test_api_quiz_endpoint():
    print("Testing POST /quiz endpoint...")
    payload = {
        "notebook_id": "default_academic",
        "page_range": [1, 3],
        "num_questions": 3
    }
    response = client.post("/quiz", json=payload)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 3

    for q in data:
        assert "question" in q
        assert "options" in q and len(q["options"]) >= 2
        assert "correct_answer" in q
        assert "explanation" in q
        assert q["correct_answer"] in q["options"]

    print("[OK] POST /quiz endpoint passed.")


def test_api_run_code_endpoint():
    print("Testing POST /run-code endpoint...")
    # Normal execution via API
    payload = {
        "code": "print('Output from Sandbox API')",
        "language": "python",
        "timeout_seconds": 5
    }
    response = client.post("/run-code", json=payload)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert data["exit_code"] == 0
    assert "Output from Sandbox API" in data["stdout"]
    assert data["timed_out"] is False

    # Blocked network via API - server must NOT crash, returns exit_code != 0
    blocked_payload = {
        "code": "import socket\ns = socket.socket()",
        "language": "python",
        "timeout_seconds": 3
    }
    resp_blocked = client.post("/run-code", json=blocked_payload)
    assert resp_blocked.status_code == 200, "Server should handle sandbox errors gracefully with 200 status"
    blocked_data = resp_blocked.json()
    assert blocked_data["exit_code"] != 0
    assert "PermissionError" in blocked_data["stderr"] or "blocked" in blocked_data["stderr"].lower()

    print("[OK] POST /run-code endpoint passed.")


def test_api_upload_endpoint():
    print("Testing POST /api/upload endpoint for images...")
    img_bytes = create_dummy_image("Lecture Note: Image Upload Test")
    files = [
        ("files", ("test_note.png", img_bytes, "image/png"))
    ]
    resp = client.post("/api/upload", files=files)
    assert resp.status_code == 200, f"Expected 200, got: {resp.text}"
    data = resp.json()
    assert data["status"] == "success"
    assert data["uploaded_pages"] == 1
    assert data["total_pages"] >= 1

    # Verify that the new page is in GET /api/pages
    get_resp = client.get("/api/pages?notebook_id=current_session")
    assert get_resp.status_code == 200
    pages = get_resp.json()
    assert any(p["source_filename"] == "test_note.png" for p in pages)
    print("[OK] POST /api/upload endpoint passed.")


def test_auth_endpoints():
    print("Testing Authentication endpoints (signup, login, wrong password, me, logout)...")
    unauth_client = TestClient(api)

    # 1. Access protected /api/auth/me without session -> 401
    res_me_unauth = unauth_client.get("/api/auth/me")
    assert res_me_unauth.status_code == 401, f"Expected 401, got {res_me_unauth.status_code}"

    # 2. Signup new user
    random_email = f"testuser_{uuid.uuid4().hex[:8]}@example.com"
    signup_payload = {
        "name": "Test User",
        "email": random_email,
        "password": "SecurePassword123!"
    }
    res_signup = unauth_client.post("/api/auth/signup", json=signup_payload)
    assert res_signup.status_code == 200, f"Expected 200, got: {res_signup.text}"
    signup_data = res_signup.json()
    assert signup_data["status"] == "success"
    assert signup_data["user"]["email"] == random_email

    # 3. /api/auth/me with session cookie -> 200
    res_me_auth = unauth_client.get("/api/auth/me")
    assert res_me_auth.status_code == 200
    assert res_me_auth.json()["user"]["name"] == "Test User"

    # 4. Logout
    res_logout = unauth_client.post("/api/auth/logout")
    assert res_logout.status_code == 200

    # 5. /api/auth/me after logout -> 401
    res_me_after = unauth_client.get("/api/auth/me")
    assert res_me_after.status_code == 401

    # 6. Login with wrong password -> 401
    login_wrong = {
        "email": random_email,
        "password": "WrongPassword!"
    }
    res_wrong = unauth_client.post("/api/auth/login", json=login_wrong)
    assert res_wrong.status_code == 401

    # 7. Login with correct password -> 200
    login_correct = {
        "email": random_email,
        "password": "SecurePassword123!"
    }
    res_login = unauth_client.post("/api/auth/login", json=login_correct)
    assert res_login.status_code == 200
    assert res_login.json()["status"] == "success"
    print("[OK] Authentication endpoints test passed.")


def test_folder_lifecycle_and_assignment():
    print("Testing folder creation, page assignment, and deletion...")
    test_nb = f"test_folder_nb_{uuid.uuid4().hex[:6]}"
    
    # 1. Starting folders should be empty
    res_get = client.get(f"/api/folders?notebook_id={test_nb}")
    assert res_get.status_code == 200
    assert res_get.json()["folders"] == []

    # 2. Create new folder
    res_create = client.post(f"/api/folders?notebook_id={test_nb}", json={"name": "Computer Networks"})
    assert res_create.status_code == 200
    assert res_create.json()["status"] == "success"
    assert res_create.json()["folder"]["name"] == "Computer Networks"

    # 3. Check folder list includes new folder
    res_list = client.get(f"/api/folders?notebook_id={test_nb}")
    assert "Computer Networks" in res_list.json()["folders"]

    # 4. Upload a page with folder assignment
    img_data = create_dummy_image()
    files = [("files", ("net_notes.png", img_data, "image/png"))]
    res_upload = client.post(f"/api/upload?notebook_id={test_nb}", files=files, data={"folder": "Computer Networks"})
    assert res_upload.status_code == 200

    pages = client.get(f"/api/pages?notebook_id={test_nb}").json()
    assert len(pages) >= 1
    assert pages[0]["folder"] == "Computer Networks"

    # 5. Move page to another folder or unassign
    res_move = client.put(f"/api/pages/{pages[0]['page_number']}/folder?notebook_id={test_nb}", json={"folder": ""})
    assert res_move.status_code == 200
    pages_after = client.get(f"/api/pages?notebook_id={test_nb}").json()
    assert pages_after[0]["folder"] == ""

    # 6. Delete folder
    res_del = client.delete(f"/api/folders/Computer%20Networks?notebook_id={test_nb}")
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "success"

    res_final_folders = client.get(f"/api/folders?notebook_id={test_nb}")
    assert "Computer Networks" not in res_final_folders.json()["folders"]
    print("[OK] Folder lifecycle and assignment tests passed.")


def test_user_stats_endpoint():
    print("Testing user stats & activity metrics...")
    unauth_client = TestClient(api)
    res_unauth = unauth_client.get("/api/user/stats")
    assert res_unauth.status_code == 401

    # Sign up and check stats
    email = f"stats_{uuid.uuid4().hex[:6]}@example.com"
    signup_res = unauth_client.post("/api/auth/signup", json={"name": "Stats User", "email": email, "password": "Password123!"})
    assert signup_res.status_code == 200

    stats_res = unauth_client.get("/api/user/stats")
    assert stats_res.status_code == 200
    data = stats_res.json()
    assert data["status"] == "success"
    stats = data["stats"]
    assert "total_notebooks" in stats
    assert "total_pages" in stats
    assert "ocr_usage" in stats
    assert "ocr_limit" in stats
    assert "ai_usage" in stats
    assert "ai_limit" in stats
    assert stats["ocr_limit"] == 50
    assert stats["ai_limit"] == 50
    print("[OK] User stats endpoint passed.")


def test_collective_pdf_export():
    print("Testing collective PDF generation and export...")
    test_nb = f"test_pdf_export_{uuid.uuid4().hex[:6]}"

    # Empty folder export returns 404
    res_empty = client.get(f"/api/folders/CS101/export-pdf?notebook_id={test_nb}")
    assert res_empty.status_code == 404

    # Upload a dummy page
    img_bytes = io.BytesIO()
    img = Image.new("RGB", (300, 300), color="white")
    img.save(img_bytes, format="PNG")
    img_bytes.seek(0)

    res_upload = client.post(
        f"/api/upload?notebook_id={test_nb}&folder=Algorithms",
        files={"files": ("algos.png", img_bytes.getvalue(), "image/png")}
    )
    assert res_upload.status_code == 200

    # Export folder as collective PDF
    res_folder_pdf = client.get(f"/api/folders/Algorithms/export-pdf?notebook_id={test_nb}")
    assert res_folder_pdf.status_code == 200
    assert "application/pdf" in res_folder_pdf.headers["content-type"]
    assert res_folder_pdf.content.startswith(b"%PDF")

    # Export all notes as collective PDF
    res_all_pdf = client.get(f"/api/folders/all/export-pdf?notebook_id={test_nb}")
    assert res_all_pdf.status_code == 200
    assert "application/pdf" in res_all_pdf.headers["content-type"]
    assert res_all_pdf.content.startswith(b"%PDF")

    # Export notebook as collective PDF
    res_nb_pdf = client.get(f"/api/notebooks/{test_nb}/export-pdf")
    assert res_nb_pdf.status_code == 200
    assert "application/pdf" in res_nb_pdf.headers["content-type"]
    assert res_nb_pdf.content.startswith(b"%PDF")

    print("[OK] Collective PDF export passed.")


if __name__ == "__main__":
    test_file_validation()
    test_pdf_extraction()
    test_ocr_processing()
    test_ocr_key_sanitization()
    test_batch_ocr_processing()
    test_search_and_indexing()
    test_diagram_ocr_normalizer_and_blocks()
    test_diagram_search_indexing()
    test_mermaid_label_extraction()
    test_diagram_relationship_quiz()
    test_sandbox_normal_output()
    test_sandbox_timeout()
    test_sandbox_blocked_network()
    test_sandbox_blocked_filesystem_write()
    test_explain_topic_schema()
    test_generate_quiz_schema()
    test_api_explain_endpoint()
    test_api_quiz_endpoint()
    test_api_run_code_endpoint()
    test_api_upload_endpoint()
    test_auth_endpoints()
    test_folder_lifecycle_and_assignment()
    test_user_stats_endpoint()
    test_collective_pdf_export()
    print("\nALL TESTS & SCHEMA VALIDATIONS PASSED SUCCESSFULLY!")


