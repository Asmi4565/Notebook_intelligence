"""
Learning Assistant module for Notebook Intelligence System (NIS).
Implements Grounded Q&A with page citations (FR-10, FR-11) and
Quiz generation with instant scoring and explanations (FR-12, FR-13).
"""

from dataclasses import dataclass, field
import json
import re
from typing import List, Optional, Dict, Any
from document_processor import PageRecord
from ocr_engine import get_gemini_client, get_gemini_model, discover_active_gemini_model


@dataclass
class GroundedAnswer:
    question: str
    answer: str
    cited_pages: List[int]
    confidence: str = "High"


@dataclass
class QuizQuestion:
    id: int
    question: str
    question_type: str  # "mcq" or "true_false"
    options: List[str]
    correct_option_idx: int
    explanation: str
    source_page: int


@dataclass
class Quiz:
    title: str
    questions: List[QuizQuestion]


def answer_question_grounded(
    query: str,
    pages: List[PageRecord],
    api_key: Optional[str] = None,
    model_name: Optional[str] = None
) -> GroundedAnswer:
    """
    Answers user queries grounded strictly in the processed notebook pages (FR-10, FR-11).
    Cites specific page numbers in the response.
    """
    processed_pages = [p for p in pages if p.ocr_status == "success" and p.ocr_text]
    if not processed_pages:
        return GroundedAnswer(
            question=query,
            answer="No processed notebook content available. Please upload notes and run OCR first.",
            cited_pages=[]
        )

    # Build context string
    context_blocks = []
    for p in processed_pages:
        context_blocks.append(f"--- PAGE {p.page_number} ({p.source_filename}) ---\n{p.ocr_text}\n")
    context_str = "\n".join(context_blocks)

    client = get_gemini_client(api_key)
    if client is None:
        # Fallback grounded logic (keyword matching across pages)
        q_lower = query.lower()
        matched_pages = []
        for p in processed_pages:
            words = [w for w in re.findall(r"\w+", q_lower) if len(w) > 3]
            if any(w in p.ocr_text.lower() for w in words):
                matched_pages.append(p.page_number)

        if not matched_pages:
            matched_pages = [processed_pages[0].page_number]

        cited_str = ", ".join(f"Page {n}" for n in matched_pages)
        simulated_answer = (
            f"Based on your notes ({cited_str}):\n\n"
            f"Regarding **'{query}'**, your notes contain information on page {matched_pages[0]}."
        )
        return GroundedAnswer(
            question=query,
            answer=simulated_answer,
            cited_pages=matched_pages
        )

    prompt = f"""You are an educational AI assistant helping a student understand their handwritten notes.
Answer the user's question accurately, grounded strictly in the provided notebook content.
Whenever making a claim or explaining a concept, explicitly cite the relevant source page like [Page 1], [Page 2], etc.
If the information is not contained in the notes, say so clearly.

--- NOTEBOOK CONTENT ---
{context_str}

--- USER QUESTION ---
{query}
"""

    active_model = model_name or get_gemini_model()
    try:
        response = client.models.generate_content(
            model=active_model,
            contents=[prompt]
        )
        answer_text = response.text or "Unable to generate an answer."
        # Extract cited pages from answer
        citations = [int(n) for n in re.findall(r"\[Page\s+(\d+)\]", answer_text, re.IGNORECASE)]
        cited_pages = sorted(list(set(citations)))
        return GroundedAnswer(
            question=query,
            answer=answer_text,
            cited_pages=cited_pages
        )
    except Exception as ex:
        err_str = str(ex)
        if any(term in err_str.lower() for term in ["404", "not_found", "not found", "no longer available"]):
            fallback_m = discover_active_gemini_model(client, failed_model=active_model)
            try:
                retry_resp = client.models.generate_content(model=fallback_m, contents=[prompt])
                ans_text = retry_resp.text or "Unable to generate an answer."
                citations = [int(n) for n in re.findall(r"\[Page\s+(\d+)\]", ans_text, re.IGNORECASE)]
                return GroundedAnswer(question=query, answer=ans_text, cited_pages=sorted(list(set(citations))))
            except Exception:
                pass

        return GroundedAnswer(
            question=query,
            answer=f"Error generating answer: {str(ex)}",
            cited_pages=[]
        )


def generate_quiz_from_notes(
    pages: List[PageRecord],
    num_questions: int = 3,
    api_key: Optional[str] = None,
    model_name: Optional[str] = None
) -> Quiz:
    """
    Generates an active-recall quiz with multiple-choice and true/false questions (FR-12).
    """
    processed_pages = [p for p in pages if p.ocr_status == "success" and p.ocr_text]
    if not processed_pages:
        return Quiz(title="Empty Quiz", questions=[])

    client = get_gemini_client(api_key)
    if client is None:
        sample_questions = [
            QuizQuestion(
                id=1,
                question="What is the main topic of your notes?",
                question_type="mcq",
                options=["Computer Networks", "Database Systems", "Operating Systems", "Compiler Design"],
                correct_answer="Computer Networks",
                explanation="Based on page 1 of your notes."
            )
        ]
        return Quiz(title="Interactive Revision Quiz", questions=sample_questions[:num_questions])

    context_str = "\n".join([f"--- PAGE {p.page_number} ---\n{p.ocr_text}" for p in processed_pages])
    prompt = f"""Generate {num_questions} multiple-choice questions testing the concepts in the notes below.
Return JSON array with items having 'id', 'question', 'options', 'correct_answer', 'explanation'.

--- NOTES ---
{context_str}
"""
    active_model = model_name or get_gemini_model()
    try:
        response = client.models.generate_content(
            model=active_model,
            contents=[prompt],
            config={"response_mime_type": "application/json"}
        )
        data = json.loads(response.text)
        questions = []
        for idx, item in enumerate(data[:num_questions], start=1):
            questions.append(QuizQuestion(
                id=idx,
                question=item.get("question", ""),
                question_type="mcq",
                options=item.get("options", []),
                correct_answer=item.get("correct_answer", ""),
                explanation=item.get("explanation", "")
            ))
        return Quiz(title="Interactive Revision Quiz", questions=questions)
    except Exception as ex:
        err_str = str(ex)
        if any(term in err_str.lower() for term in ["404", "not_found", "not found", "no longer available"]):
            fallback_m = discover_active_gemini_model(client, failed_model=active_model)
            try:
                retry_resp = client.models.generate_content(
                    model=fallback_m,
                    contents=[prompt],
                    config={"response_mime_type": "application/json"}
                )
                data = json.loads(retry_resp.text)
                questions = []
                for idx, item in enumerate(data[:num_questions], start=1):
                    questions.append(QuizQuestion(
                        id=idx,
                        question=item.get("question", ""),
                        question_type="mcq",
                        options=item.get("options", []),
                        correct_answer=item.get("correct_answer", ""),
                        explanation=item.get("explanation", "")
                    ))
                return Quiz(title="Interactive Revision Quiz", questions=questions)
            except Exception:
                pass

        return Quiz(title="Quiz Generation Error", questions=[])

    """
    Generates an active-recall quiz with multiple-choice and true/false questions (FR-12).
    """
    processed_pages = [p for p in pages if p.ocr_status == "success" and p.ocr_text]
    if not processed_pages:
        return Quiz(title="Empty Quiz", questions=[])

    client = get_gemini_client(api_key)

    if client is None:
        # Fallback realistic quiz questions based on sample note content
        sample_questions = [
            QuizQuestion(
                id=1,
                question="What does Big-O notation represent according to the notes?",
                question_type="mcq",
                options=[
                    "The exact execution time in milliseconds",
                    "The asymptotic upper bound on execution time O(g(n))",
                    "The lowest possible memory usage",
                    "The average number of processor cycles"
                ],
                correct_option_idx=1,
                explanation="Big-O asymptotic notation defines an upper bound on growth rate as n approaches infinity.",
                source_page=1
            ),
            QuizQuestion(
                id=2,
                question="According to the Master Theorem, the recurrence T(n) = 2T(n/2) + O(n) resolves to:",
                question_type="mcq",
                options=[
                    "O(n)",
                    "O(n^2)",
                    "O(n log n)",
                    "O(2^n)"
                ],
                correct_option_idx=2,
                explanation="Case 2 of the Master Theorem gives O(n^c * log n) when log_b(a) = c. Here log_2(2) = 1, so O(n log n).",
                source_page=1
            ),
            QuizQuestion(
                id=3,
                question="True or False: Divide and conquer algorithms divide problems into overlapping subproblems with memoization.",
                question_type="true_false",
                options=["True", "False"],
                correct_option_idx=1,
                explanation="False. Overlapping subproblems with memoization is characteristic of Dynamic Programming; Divide and Conquer solves disjoint subproblems.",
                source_page=1
            )
        ]
        return Quiz(title="Interactive Revision Quiz (Demo Mode)", questions=sample_questions[:num_questions])

    # Dynamic generation via Gemini
    context_str = "\n".join([f"Page {p.page_number}:\n{p.ocr_text}" for p in processed_pages])
    prompt = f"""You are an educational quiz generator. Generate {num_questions} multiple-choice or true/false questions
testing the key concepts written in these student notes.

Respond strictly with valid JSON conforming to this structure:
{{
  "title": "Topic Quiz",
  "questions": [
    {{
      "id": 1,
      "question": "Question text?",
      "question_type": "mcq",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct_option_idx": 1,
      "explanation": "Why Option B is correct.",
      "source_page": 1
    }}
  ]
}}

--- STUDENT NOTES ---
{context_str}
"""
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[prompt],
            config={"response_mime_type": "application/json"}
        )
        data = json.loads(response.text)
        questions = []
        for q in data.get("questions", []):
            questions.append(QuizQuestion(
                id=q.get("id", 1),
                question=q.get("question", ""),
                question_type=q.get("question_type", "mcq"),
                options=q.get("options", []),
                correct_option_idx=q.get("correct_option_idx", 0),
                explanation=q.get("explanation", ""),
                source_page=q.get("source_page", 1)
            ))
        return Quiz(title=data.get("title", "Notebook Review Quiz"), questions=questions)
    except Exception as ex:
        print(f"Quiz generation fallback triggered: {ex}")
        return generate_quiz_from_notes(pages, num_questions=num_questions, api_key=None)
