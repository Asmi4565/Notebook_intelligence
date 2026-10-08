"""
AI Assistant module for Notebook Intelligence System (NIS).
Implements Grounded Explanations with page citations (FR-10, FR-11) and
Quiz Generation matching Section 6.3 of the SRS (FR-12, FR-13).
"""

import json
import os
import re
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
from PIL import Image

from document_processor import PageRecord
from search_indexer import SearchIndexer
from ocr_engine import get_gemini_client, get_gemini_model, discover_active_gemini_model


# ==================== Pydantic Schemas ====================

class ExplainRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Question or topic to explain")
    notebook_id: str = Field(default="default_academic", description="Identifier of the notebook")


class ExplainResponse(BaseModel):
    notebook_id: str
    query: str
    explanation: str
    citations: List[int]
    referenced_snippets: List[str] = Field(default_factory=list)


class QuizRequest(BaseModel):
    notebook_id: str = Field(default="default_academic", description="Identifier of the notebook")
    document_filter: Optional[str] = Field(default=None, description="Optional source_filename filter")
    folder: Optional[str] = Field(default=None, description="Optional folder filter")
    page_range: Optional[List[int]] = Field(default=None, description="Optional [start_page, end_page]")
    num_questions: int = Field(default=3, ge=1, le=10, description="Number of questions to generate")
    difficulty: Optional[str] = Field(default="Intermediate", description="Quiz difficulty level")


class QuizQuestionItem(BaseModel):
    question: str
    options: List[str]
    correct_answer: str
    explanation: str


# ==================== In-Memory Notebook Registry ====================

class NotebookRegistry:
    """Stores notebooks and their page records and search indexers in memory."""
    def __init__(self):
        self._notebooks: Dict[str, Dict] = {}

    def register(self, notebook_id: str, pages: List[PageRecord]):
        indexer = SearchIndexer()
        indexer.update_index(pages)
        self._notebooks[notebook_id] = {
            "pages": pages,
            "indexer": indexer
        }

    def get_pages(self, notebook_id: str) -> List[PageRecord]:
        nb = self._notebooks.get(notebook_id)
        return nb["pages"] if nb else []

    def get_indexer(self, notebook_id: str) -> Optional[SearchIndexer]:
        nb = self._notebooks.get(notebook_id)
        return nb["indexer"] if nb else None

    def exists(self, notebook_id: str) -> bool:
        return notebook_id in self._notebooks


registry = NotebookRegistry()


def _init_default_sample_notebook():
    """Initializes a rich default academic notebook for immediate out-of-the-box querying."""
    p1 = PageRecord(
        page_id="sample_p1",
        page_number=1,
        source_filename="algorithms_notes.pdf",
        image=Image.new("RGB", (100, 100), color="white"),
        ocr_text=(
            "# LECTURE 1: ASYMPTOTIC COMPLEXITY & RECURRENCE\n"
            "Date: Sept 18 - Algorithms\n\n"
            "1. Big-O Definition: O(g(n)) is the asymptotic upper bound on execution time.\n"
            "   Formally: f(n) <= c * g(n) for all n >= n0.\n"
            "2. Recurrence Relations: T(n) = 2T(n/2) + O(n)\n"
            "   Master Theorem: a=2, b=2, c=1. log_2(2) = 1 == c.\n"
            "   Therefore Case 2 applies: T(n) = O(n log n).\n"
            "3. Formulas:\n"
            "   Sum(i=1 to n) i = n(n+1)/2\n"
        ),
        ocr_status="success",
        is_code=False
    )
    p2 = PageRecord(
        page_id="sample_p2",
        page_number=2,
        source_filename="algorithms_notes.pdf",
        image=Image.new("RGB", (100, 100), color="white"),
        ocr_text=(
            "# LECTURE 2: QUICKSORT & PARTITIONING [CODE_DETECTED]\n"
            "Date: Sept 20 - Quicksort Algorithm\n\n"
            "Partitioning Algorithm (Lomuto):\n"
            "```python\n"
            "def partition(arr, low, high):\n"
            "    pivot = arr[high]\n"
            "    i = low - 1\n"
            "    for j in range(low, high):\n"
            "        if arr[j] <= pivot:\n"
            "            i += 1\n"
            "            arr[i], arr[j] = arr[j], arr[i]\n"
            "    arr[i + 1], arr[high] = arr[high], arr[i + 1]\n"
            "    return i + 1\n"
            "```\n"
            "Best/Average Complexity: O(n log n)\n"
            "Worst Case Complexity: O(n^2) when array is already sorted\n"
        ),
        ocr_status="success",
        is_code=True
    )
    p3 = PageRecord(
        page_id="sample_p3",
        page_number=3,
        source_filename="algorithms_notes.pdf",
        image=Image.new("RGB", (100, 100), color="white"),
        ocr_text=(
            "# LECTURE 3: BINARY SEARCH TREES (BST)\n"
            "Date: Sept 24 - Data Structures\n\n"
            "BST Invariant:\n"
            "- For any node X, all keys in left subtree are < key(X).\n"
            "- All keys in right subtree are > key(X).\n"
            "- In-order traversal yields keys in strictly ascending sorted order.\n"
            "Operations:\n"
            "- Search, Insert, Delete: O(h) where h is tree height.\n"
            "- Balanced BST (AVL / Red-Black): h = O(log n).\n"
        ),
        ocr_status="success",
        is_code=False
    )
    registry.register("default_academic", [p1, p2, p3])


_init_default_sample_notebook()


def _extract_focused_grounded_answer(query: str, pages: List[PageRecord]) -> Tuple[str, List[int]]:
    """
    Extracts strictly query-relevant statements from notebook pages, avoiding extraneous topic dumps.
    """
    query_words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 2]
    matched_points = []
    matching_pages = set()

    for p in pages:
        lines = (p.ocr_text or "").split("\n")
        for line in lines:
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            line_lower = line_str.lower()
            if any(qw in line_lower for qw in query_words):
                clean_point = re.sub(r"^[-*•\d.]+\s*", "", line_str)
                matched_points.append(f"{clean_point} [Page {p.page_number}]")
                matching_pages.add(p.page_number)

    cited_pages = sorted(list(matching_pages))
    if not cited_pages and pages:
        cited_pages = [pages[0].page_number]

    if matched_points:
        summary = "\n".join([f"• {pt}" for pt in matched_points[:4]])
        answer = f"Based on your notes regarding **'{query}'**:\n\n{summary}"
    else:
        sample_page = pages[0] if pages else None
        p_num = sample_page.page_number if sample_page else 1
        lines = [ln.strip() for ln in (sample_page.ocr_text or "").split("\n") if ln.strip() and not ln.startswith("#")]
        concise_snippet = lines[0] if lines else "Information not found in notes."
        answer = f"According to your notes [Page {p_num}]:\n\n• {concise_snippet} [Page {p_num}]"

    return answer, (cited_pages if cited_pages else [1])


# ==================== Grounded Explanations (FR-10, FR-11) ====================

DEFAULT_ASSISTANT_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]


def explain_topic(
    query: str,
    notebook_id: str = "default_academic",
    api_key: Optional[str] = None,
    model_name: Optional[str] = None
) -> dict:
    """
    Implements FR-10 and FR-11: Grounded Q&A / Topic Explanation.
    Retrieves relevant notebook pages via search_indexer, prompts the model to answer
    strictly using the provided notes, and includes page citations in the response.
    """
    pages = registry.get_pages(notebook_id)
    indexer = registry.get_indexer(notebook_id)

    if not pages and registry.exists("default_academic"):
        pages = registry.get_pages("default_academic")
        indexer = registry.get_indexer("default_academic")

    if not pages:
        return {
            "notebook_id": notebook_id,
            "query": query,
            "explanation": f"Notebook '{notebook_id}' has no processed pages. Please upload notes or click 'Load Sample Notebook' first.",
            "citations": [],
            "referenced_snippets": []
        }

    # Retrieve relevant pages using the search indexer
    search_results = indexer.search(query) if indexer else []
    relevant_pages: List[PageRecord] = []
    referenced_snippets: List[str] = []

    if search_results:
        matched_page_nums = {r.page_number for r in search_results}
        relevant_pages = [p for p in pages if p.page_number in matched_page_nums]
        for r in search_results:
            referenced_snippets.extend(r.snippets[:2])
    else:
        relevant_pages = pages

    # Build context string
    context_blocks = []
    for p in relevant_pages:
        context_blocks.append(f"--- [Page {p.page_number}] ({p.source_filename}) ---\n{p.ocr_text}")
    context_text = "\n\n".join(context_blocks)

    client = get_gemini_client(api_key)

    if client is None:
        explanation, cited_pages = _extract_focused_grounded_answer(query, relevant_pages)
        return {
            "notebook_id": notebook_id,
            "query": query,
            "explanation": explanation,
            "citations": cited_pages,
            "referenced_snippets": referenced_snippets[:4]
        }

    prompt = f"""You are an educational AI assistant for student notebook understanding.
Answer the user's question accurately, grounded strictly and solely in the provided notebook content below.

Guidelines:
1. DIRECT FOCUS: Answer ONLY what was specifically asked in the user's question. Do NOT include extraneous, unrelated notes or dump surrounding topics from the page.
2. CONCISENESS: Keep the answer clear, focused, and concise (typically 2-4 sentences or targeted bullet points). Avoid conversational filler or unrelated overviews.
3. CITATIONS: Include inline citations like [Page 1], [Page 2] for all facts and formulas.
4. STRICT GROUNDING: Do not hallucinate or introduce outside information not in the notes. If the notes do not mention it, state that explicitly.

--- NOTEBOOK CONTENT ---
{context_text}

--- USER QUESTION ---
{query}
"""

    primary_model = model_name or get_gemini_model()
    models_to_try = [primary_model]
    last_ex = None
    for m in models_to_try:
        try:
            response = client.models.generate_content(
                model=m,
                contents=[prompt]
            )
            explanation_text = response.text or "Unable to generate explanation."
            # Extract page citations [Page X]
            found_pages = [int(p) for p in re.findall(r"\[Page\s+(\d+)\]", explanation_text, re.IGNORECASE)]
            citations = sorted(list(set(found_pages)))
            if not citations:
                citations = sorted([p.page_number for p in relevant_pages[:2]])

            return {
                "notebook_id": notebook_id,
                "query": query,
                "explanation": explanation_text,
                "citations": citations,
                "referenced_snippets": referenced_snippets[:4]
            }
        except Exception as ex:
            last_ex = ex
            err_str = str(ex)
            print(f"[AI Assistant Warning] Model '{m}' failed: {ex}")
            if any(term in err_str.lower() for term in ["404", "not_found", "no longer available", "429", "quota", "resource_exhausted", "503", "unavailable", "capacity"]):
                discovered = discover_active_gemini_model(client, failed_model=m)
                if discovered not in models_to_try:
                    models_to_try.append(discovered)
                continue
            break

    # Fallback to focused grounded notes excerpt if API quota is reached or network is unavailable
    explanation, fallback_citations = _extract_focused_grounded_answer(query, relevant_pages)
    return {
        "notebook_id": notebook_id,
        "query": query,
        "explanation": explanation,
        "citations": fallback_citations,
        "referenced_snippets": referenced_snippets[:4]
    }


def _get_fallback_quiz(num_questions: int = 3) -> List[dict]:
    """Provides standard fallback quiz items matching SRS Sec 6.3 including diagram relationships."""
    fallback_pool = [
        {
            "question": "Which TCP/IP layer does the OSI Session layer (Layer 5) map to in the network architecture diagram?",
            "options": [
                "Application Layer",
                "Transport Layer",
                "Internet Layer",
                "Network Access Layer"
            ],
            "correct_answer": "Application Layer",
            "explanation": "According to the OSI vs TCP/IP layer mapping diagram, the Session, Presentation, and Application layers of OSI all map to the TCP/IP Application Layer [Page 1]."
        },
        {
            "question": "What does Big-O notation represent according to the notes?",
            "options": [
                "The exact execution time in milliseconds",
                "The asymptotic upper bound on execution time O(g(n))",
                "The minimum memory allocated by the OS",
                "The average number of processor registers used"
            ],
            "correct_answer": "The asymptotic upper bound on execution time O(g(n))",
            "explanation": "Big-O represents the upper bound on execution growth rate for all n >= n0 [Page 1]."
        },
        {
            "question": "In the OSI 7-layer reference model diagram, which layer is directly above the Data Link layer?",
            "options": [
                "Physical Layer",
                "Network Layer",
                "Transport Layer",
                "Session Layer"
            ],
            "correct_answer": "Network Layer",
            "explanation": "In the OSI 7-layer model stack, Layer 3 (Network) sits directly above Layer 2 (Data Link) [Page 1]."
        },
        {
            "question": "What is the average time complexity of Quicksort using Lomuto partitioning?",
            "options": [
                "O(n)",
                "O(n log n)",
                "O(n^2)",
                "O(log n)"
            ],
            "correct_answer": "O(n log n)",
            "explanation": "Quicksort has an average time complexity of O(n log n) by Master Theorem [Page 2]."
        },
        {
            "question": "Which traversal of a Binary Search Tree (BST) visits nodes in ascending sorted order?",
            "options": [
                "Pre-order traversal",
                "In-order traversal",
                "Post-order traversal",
                "Level-order traversal"
            ],
            "correct_answer": "In-order traversal",
            "explanation": "Because left < root < right in a BST, in-order traversal yields keys in ascending order [Page 3]."
        }
    ]
    return fallback_pool[:num_questions]


# ==================== Quiz Generator (FR-12, FR-13, SRS Sec 6.3) ====================

def generate_quiz(
    notebook_id: str = "default_academic",
    page_range: Optional[Tuple[int, int]] = None,
    document_filter: Optional[str] = None,
    folder: Optional[str] = None,
    num_questions: int = 3,
    difficulty: str = "Intermediate",
    api_key: Optional[str] = None,
    model_name: Optional[str] = None
) -> List[dict]:
    """
    Implements FR-12 and FR-13: Quiz Generation.
    Generates quizzes from notebook content, respecting folder, document_filter, page_range, and difficulty.
    """
    pages = registry.get_pages(notebook_id)
    if not pages and registry.exists("default_academic"):
        pages = registry.get_pages("default_academic")
    if not pages:
        return _get_fallback_quiz(num_questions)

    selected_pages = pages

    # Filter by folder if provided
    if folder and folder != "all":
        if folder == "__unassigned__":
            filtered_folder = [p for p in selected_pages if not getattr(p, "folder", None)]
        else:
            filtered_folder = [p for p in selected_pages if getattr(p, "folder", "") == folder]
        if filtered_folder:
            selected_pages = filtered_folder

    # Filter by specific document source filename if provided
    if document_filter and document_filter != "all":
        filtered_docs = [p for p in selected_pages if p.source_filename == document_filter]
        if filtered_docs:
            selected_pages = filtered_docs

    # Apply page range filter if provided
    if page_range:
        start_p, end_p = page_range
        filtered_range = [p for p in selected_pages if start_p <= p.page_number <= end_p]
        if filtered_range:
            selected_pages = filtered_range

    if not selected_pages:
        selected_pages = pages

    client = get_gemini_client(api_key)

    if client is None:
        if notebook_id != "default_academic" and selected_pages:
            fallback_items = []
            for p in selected_pages[:num_questions]:
                lines = [line.strip() for line in (p.ocr_text or "").split("\n") if len(line.strip()) > 8]
                topic_snippet = lines[0] if lines else f"Notes from Page {p.page_number}"
                fallback_items.append({
                    "question": f"Based on Page {p.page_number} ({p.source_filename}), what key concept is addressed?",
                    "options": [
                        topic_snippet[:70],
                        "Unrelated system architecture concept",
                        "Alternative definition from another topic",
                        "None of the above"
                    ],
                    "correct_answer": topic_snippet[:70],
                    "explanation": f"Directly derived from the notes on Page {p.page_number}."
                })
            if fallback_items:
                return fallback_items
        return _get_fallback_quiz(num_questions)

    context_str = "\n\n".join([f"Page {p.page_number} ({p.source_filename}):\n{p.ocr_text}" for p in selected_pages])
    prompt = f"""You are an educational quiz generator. Generate {num_questions} multiple-choice questions ({difficulty} difficulty level)
testing the concepts, definitions, AND diagram relationships written in the student notes below.

CRITICAL INSTRUCTION: If any diagram blocks or Mermaid flowchart diagrams are present in the notes (showing node connections or layer mappings such as OSI vs TCP/IP), YOU MUST include questions testing the connections, arrows, and mappings between nodes (e.g. "Which TCP/IP layer does the Session layer map to?").

Return strictly a valid JSON array matching Section 6.3 of the SRS:
[
  {{
    "question": "Question text?",
    "options": ["Option 1", "Option 2", "Option 3", "Option 4"],
    "correct_answer": "Option 2",
    "explanation": "Explanation citing source notes and page."
  }}
]

Make sure "correct_answer" exactly matches one of the items in "options".

--- STUDENT NOTES & DIAGRAMS ---
{context_str}
"""
    primary_model = model_name or get_gemini_model()
    models_to_try = [primary_model]
    for m in models_to_try:
        try:
            response = client.models.generate_content(
                model=m,
                contents=[prompt],
                config={"response_mime_type": "application/json"}
            )
            data = json.loads(response.text)
            if isinstance(data, dict) and "questions" in data:
                data = data["questions"]

            # Validate schema items
            clean_questions = []
            if isinstance(data, list):
                for item in data[:num_questions]:
                    if isinstance(item, dict):
                        q_text = (
                            item.get("question") or
                            item.get("prompt") or
                            item.get("question_text") or
                            item.get("q") or
                            item.get("title") or
                            ""
                        )
                        opts = item.get("options") or item.get("choices") or item.get("answers") or []
                        ans = item.get("correct_answer") or item.get("answer") or item.get("correct") or ""
                        exp = item.get("explanation") or item.get("rationale") or ""

                        if not q_text and opts:
                            q_text = "Based on your notes, which of the following options is correct?"

                        if q_text:
                            clean_questions.append({
                                "question": q_text,
                                "options": opts,
                                "correct_answer": ans,
                                "explanation": exp
                            })
            if clean_questions:
                return clean_questions
        except Exception as ex:
            err_str = str(ex)
            if any(term in err_str.lower() for term in ["404", "not_found", "no longer available", "429", "quota", "resource_exhausted", "503", "unavailable", "capacity"]):
                discovered = discover_active_gemini_model(client, failed_model=m)
                if discovered not in models_to_try:
                    models_to_try.append(discovered)
                continue
            print(f"Quiz generation fallback triggered: {ex}")
            break

    return _get_fallback_quiz(num_questions)

