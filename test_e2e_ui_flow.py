"""
End-to-End full user journey simulation test verifying all 5 core UI views
and endpoints served by app.py.
"""

from fastapi.testclient import TestClient
from app import api

client = TestClient(api)


def test_e2e_complete_ui_flow():
    print("\n--- 0. Authenticating Session for E2E Flow ---")
    auth_resp = client.post("/api/auth/signup", json={
        "name": "E2E Tester",
        "email": "e2etester@example.com",
        "password": "Password123!"
    })
    if auth_resp.status_code != 200:
        client.post("/api/auth/login", json={
            "email": "e2etester@example.com",
            "password": "Password123!"
        })

    print("\n--- 1. Testing GET / (HTML Interface Delivery) ---")
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "Notebook Intelligence System" in html
    assert "Upload & Status" in html
    assert "Notebook Viewer" in html
    assert "Search View" in html
    assert "Ask / Quiz" in html
    assert "Code Lab" in html
    print("[PASS] HTML user interface delivers all 5 views.")

    print("\n--- 2. Testing View 1: Load Sample Notebook ---")
    resp = client.post("/api/load-sample")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["count"] == 3
    print("[PASS] Sample notebook loaded 3 pages.")

    print("\n--- 3. Testing View 2: Notebook Viewer & Text Editing Persistence ---")
    pages_resp = client.get("/api/pages")
    assert pages_resp.status_code == 200
    pages = pages_resp.json()
    assert len(pages) == 3
    assert pages[0]["page_number"] == 1
    assert pages[0]["image_url"].startswith("data:image/")
    assert pages[1]["is_code"] is True

    # Test editing page 1 text
    updated_text = pages[0]["ocr_text"] + "\n[TEST_EDIT] Verified persistence."
    put_resp = client.put("/api/pages/1", json={"ocr_text": updated_text})
    assert put_resp.status_code == 200

    # Verify persistence
    pages_check = client.get("/api/pages").json()
    assert "[TEST_EDIT]" in pages_check[0]["ocr_text"]
    print("[PASS] Split-screen page inspection and edit persistence verified.")

    print("\n--- 4. Testing View 3: Search with Highlights ---")
    search_resp = client.post("/api/search", json={"query": "Quicksort", "notebook_id": "current_session"})
    assert search_resp.status_code == 200
    search_data = search_resp.json()
    assert search_data["total_matches"] > 0
    assert len(search_data["results"]) > 0
    assert "<mark" in search_data["results"][0]["snippets"][0]
    print(f"[PASS] Search returned {search_data['total_matches']} matches with <mark> highlights.")

    print("\n--- 5. Testing View 4: Grounded Q&A Chat & Quiz Runner ---")
    # Grounded Q&A
    qa_resp = client.post("/explain", json={
        "query": "What is the recurrence relation for Quicksort?",
        "notebook_id": "current_session"
    })
    assert qa_resp.status_code == 200
    qa_data = qa_resp.json()
    assert len(qa_data["explanation"]) > 0
    assert len(qa_data["citations"]) > 0
    print(f"[PASS] Grounded Q&A generated explanation citing: {qa_data['citations']}")

    # Quiz Runner
    quiz_resp = client.post("/quiz", json={
        "notebook_id": "current_session",
        "num_questions": 3
    })
    assert quiz_resp.status_code == 200
    quiz_data = quiz_resp.json()
    assert len(quiz_data) == 3
    for q in quiz_data:
        assert len(q["options"]) >= 2
        assert q["correct_answer"] in q["options"]
    print(f"[PASS] Quiz runner generated {len(quiz_data)} questions matching SRS Sec 6.3.")

    print("\n--- 6. Testing View 5: Code Lab Execution & Output Terminal ---")
    code = (
        "def partition(arr, low, high):\n"
        "    pivot = arr[high]\n"
        "    i = low - 1\n"
        "    for j in range(low, high):\n"
        "        if arr[j] <= pivot:\n"
        "            i += 1\n"
        "            arr[i], arr[j] = arr[j], arr[i]\n"
        "    arr[i + 1], arr[high] = arr[high], arr[i + 1]\n"
        "    return i + 1\n"
        "nums = [64, 34, 25, 12, 22, 11, 90]\n"
        "p = partition(nums, 0, len(nums)-1)\n"
        "print(f'Pivot index: {p}')\n"
    )
    code_resp = client.post("/run-code", json={
        "code": code,
        "language": "python",
        "timeout_seconds": 5
    })
    assert code_resp.status_code == 200
    code_data = code_resp.json()
    assert code_data["exit_code"] == 0
    assert "Pivot index: 6" in code_data["stdout"]
    assert code_data["timed_out"] is False
    print(f"[PASS] Code Lab executed successfully in {code_data['duration_ms']} ms.")

    print("\n--- 7. Testing Code Sandbox Security Guardrails ---")
    # Timeout test
    timeout_resp = client.post("/run-code", json={
        "code": "while True: pass",
        "language": "python",
        "timeout_seconds": 1
    })
    assert timeout_resp.status_code == 200
    assert timeout_resp.json()["timed_out"] is True

    # Blocked network test
    net_resp = client.post("/run-code", json={
        "code": "import socket\ns = socket.socket()",
        "language": "python",
        "timeout_seconds": 3
    })
    assert net_resp.status_code == 200
    assert net_resp.json()["exit_code"] != 0
    assert "PermissionError" in net_resp.json()["stderr"] or "blocked" in net_resp.json()["stderr"].lower()
    print("[PASS] Code Sandbox enforced timeout and network block.")


if __name__ == "__main__":
    test_e2e_complete_ui_flow()
    print("\n[SUCCESS] ALL E2E USER JOURNEY TESTS COMPLETED SUCCESSFULLY!")

