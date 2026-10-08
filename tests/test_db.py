"""
Unit and Integration tests for SQLite Database Layer (db.py)
Covering:
- Create notebook
- Save pages
- Update transcript
- Delete page with renumbering
- Full-text search (FTS5)
- Cascade delete
"""

import sys
import os
import tempfile
from pathlib import Path
import pytest

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Use an isolated temporary database for testing
temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
temp_db.close()
os.environ["NIS_DB_FILE"] = temp_db.name

import db


@pytest.fixture(autouse=True)
def setup_fresh_db():
    db.init_db()
    with db.get_connection() as conn:
        conn.execute("DELETE FROM pages")
        conn.execute("DELETE FROM quiz_history")
        conn.execute("DELETE FROM notebooks WHERE id != 'current_session'")
        conn.execute("DELETE FROM users")
        conn.commit()
    yield


def test_user_creation_and_password_hashing():
    # Create user
    user = db.create_user("Alice Smith", "alice@example.com", "SecretPass123!")
    assert user["name"] == "Alice Smith"
    assert user["email"] == "alice@example.com"
    assert "id" in user

    # Password verification
    db_user = db.get_user_by_email("alice@example.com")
    assert db_user is not None
    assert db.verify_password("SecretPass123!", db_user["password_hash"]) is True
    assert db.verify_password("WrongPassword", db_user["password_hash"]) is False

    # Get by ID
    by_id = db.get_user_by_id(user["id"])
    assert by_id is not None
    assert by_id["email"] == "alice@example.com"


def test_user_notebook_isolation():
    user1 = db.create_user("User One", "user1@test.com", "pass123")
    user2 = db.create_user("User Two", "user2@test.com", "pass123")

    # Create notebooks for user1 and user2
    db.create_notebook("nb_user1", "User 1 Notes", user_id=user1["id"])
    db.create_notebook("nb_user2", "User 2 Notes", user_id=user2["id"])

    # List notebooks for user1
    nb1 = db.list_notebooks(user_id=user1["id"])
    ids1 = [n["id"] for n in nb1]
    assert "nb_user1" in ids1

    # List notebooks for user2
    nb2 = db.list_notebooks(user_id=user2["id"])
    ids2 = [n["id"] for n in nb2]
    assert "nb_user2" in ids2



def test_create_and_list_notebook():
    nb = db.create_notebook("test_nb_1", "Test Notebook 1")
    assert nb["id"] == "test_nb_1"
    assert nb["name"] == "Test Notebook 1"

    notebooks = db.list_notebooks()
    ids = [n["id"] for n in notebooks]
    assert "test_nb_1" in ids


def test_save_and_get_pages():
    nb_id = "test_nb_pages"
    db.create_notebook(nb_id, "Test Pages")
    pages = [
        {
            "page_id": f"{nb_id}_p1",
            "page_number": 1,
            "source_filename": "lecture1.pdf",
            "image_data": None,
            "ocr_text": "Neural networks process vector inputs using backpropagation.",
            "ocr_status": "success",
            "is_code": False
        },
        {
            "page_id": f"{nb_id}_p2",
            "page_number": 2,
            "source_filename": "lecture1.pdf",
            "image_data": None,
            "ocr_text": "def train(): print('Loss minimised')",
            "ocr_status": "success",
            "is_code": True
        }
    ]
    db.save_pages(nb_id, pages)

    fetched = db.get_db_pages(nb_id)
    assert len(fetched) == 2
    assert fetched[0]["page_number"] == 1
    assert fetched[0]["ocr_text"] == "Neural networks process vector inputs using backpropagation."
    assert fetched[1]["is_code"] == 1


def test_update_page_transcript():
    nb_id = "test_nb_update"
    db.create_notebook(nb_id, "Update Test")
    pages = [
        {
            "page_id": f"{nb_id}_p1",
            "page_number": 1,
            "source_filename": "note.png",
            "ocr_text": "Initial Text",
            "ocr_status": "success",
            "is_code": False
        }
    ]
    db.save_pages(nb_id, pages)

    success = db.update_page_transcript(nb_id, 1, "Updated Text with ```python\ncode\n```", is_code=True)
    assert success is True

    updated_pages = db.get_db_pages(nb_id)
    assert updated_pages[0]["ocr_text"] == "Updated Text with ```python\ncode\n```"
    assert updated_pages[0]["is_code"] == 1


def test_delete_page_with_renumbering():
    nb_id = "test_nb_delete"
    db.create_notebook(nb_id, "Delete Test")
    pages = [
        {"page_id": f"{nb_id}_p1", "page_number": 1, "source_filename": "doc.pdf", "ocr_text": "Page One", "ocr_status": "success"},
        {"page_id": f"{nb_id}_p2", "page_number": 2, "source_filename": "doc.pdf", "ocr_text": "Page Two", "ocr_status": "success"},
        {"page_id": f"{nb_id}_p3", "page_number": 3, "source_filename": "doc.pdf", "ocr_text": "Page Three", "ocr_status": "success"},
    ]
    db.save_pages(nb_id, pages)

    # Delete page 2
    remaining = db.delete_page(nb_id, page_number=2)
    assert len(remaining) == 2
    assert remaining[0]["page_number"] == 1
    assert remaining[0]["ocr_text"] == "Page One"
    assert remaining[1]["page_number"] == 2
    assert remaining[1]["ocr_text"] == "Page Three"


def test_full_text_search():
    nb_id = "test_nb_fts"
    db.create_notebook(nb_id, "FTS Test")
    pages = [
        {"page_id": f"{nb_id}_p1", "page_number": 1, "source_filename": "math.pdf", "ocr_text": "Eigenvalues and eigenvectors solve linear differential equations.", "ocr_status": "success"},
        {"page_id": f"{nb_id}_p2", "page_number": 2, "source_filename": "cs.pdf", "ocr_text": "Binary search trees enable O(log N) fast lookup times.", "ocr_status": "success"},
    ]
    db.save_pages(nb_id, pages)

    results = db.search_pages(nb_id, "Eigenvalues")
    assert len(results) >= 1
    assert results[0]["page_number"] == 1
    assert "<mark>Eigenvalues</mark>" in results[0]["snippet"]


def test_cascade_delete():
    nb_id = "test_nb_cascade"
    db.create_notebook(nb_id, "Cascade Delete Test")
    pages = [
        {"page_id": f"{nb_id}_p1", "page_number": 1, "source_filename": "data.pdf", "ocr_text": "Cascading content", "ocr_status": "success"}
    ]
    db.save_pages(nb_id, pages)
    db.save_quiz_history(nb_id, num_questions=1, questions=[{"q": "sample"}], user_score=1, total_score=1)

    assert len(db.get_db_pages(nb_id)) == 1
    assert len(db.get_quiz_history(nb_id)) == 1

    # Delete notebook
    deleted = db.delete_notebook(nb_id)
    assert deleted is True

    # Pages and quiz history must be empty due to CASCADE ON DELETE
    assert len(db.get_db_pages(nb_id)) == 0
    assert len(db.get_quiz_history(nb_id)) == 0
