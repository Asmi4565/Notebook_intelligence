"""
Unit and integration tests for folder-scoped quiz generation and synchronization.
"""
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app import api
from ai_assistant import QuizRequest, generate_quiz, registry
from ocr_engine import PageRecord

client = TestClient(api)

def test_quiz_with_folder_filtering():
    test_nb = "test_quiz_folder_nb"
    dummy_img = Image.new("RGB", (50, 50), color="white")
    pages = [
        PageRecord(page_id="p1", page_number=1, source_filename="math.pdf", image=dummy_img, ocr_text="Page 1 explains linear algebra matrices and eigenvalues.", folder="Mathematics"),
        PageRecord(page_id="p2", page_number=2, source_filename="ai.pdf", image=dummy_img, ocr_text="Page 2 covers neural networks backpropagation and gradient descent.", folder="Machine Learning"),
        PageRecord(page_id="p3", page_number=3, source_filename="misc.pdf", image=dummy_img, ocr_text="Page 3 unassigned note on random syllabus topics.", folder=None),
    ]
    registry.register(test_nb, pages)

    # 1. Filter by specific folder: "Mathematics"
    quiz_math = generate_quiz(notebook_id=test_nb, folder="Mathematics", num_questions=2)
    assert len(quiz_math) > 0
    assert any("Page 1" in q["explanation"] or "Page 1" in q["question"] for q in quiz_math)

    # 2. Filter by specific folder: "Machine Learning"
    quiz_ml = generate_quiz(notebook_id=test_nb, folder="Machine Learning", num_questions=2)
    assert len(quiz_ml) > 0
    assert any("Page 2" in q["explanation"] or "Page 2" in q["question"] for q in quiz_ml)

    # 3. Filter by unassigned
    quiz_unassigned = generate_quiz(notebook_id=test_nb, folder="__unassigned__", num_questions=2)
    assert len(quiz_unassigned) > 0
    assert any("Page 3" in q["explanation"] or "Page 3" in q["question"] for q in quiz_unassigned)

def test_api_quiz_endpoint_with_folder_param():
    test_nb = "test_api_quiz_folder_nb"
    dummy_img = Image.new("RGB", (50, 50), color="white")
    pages = [
        PageRecord(page_id="p1", page_number=1, source_filename="db.pdf", image=dummy_img, ocr_text="Database normalization BCNF and 3NF functional dependencies.", folder="Databases"),
    ]
    registry.register(test_nb, pages)

    res = client.post("/api/quiz", json={
        "notebook_id": test_nb,
        "folder": "Databases",
        "num_questions": 1,
        "difficulty": "Intermediate"
    })
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1
