"""
Search Indexer module for Notebook Intelligence System (NIS).
Implements page-wise indexing (FR-07), keyword/phrase search (FR-08),
and contextual highlighting (FR-09).
"""

from dataclasses import dataclass
import html
import re
from typing import List, Optional
from document_processor import PageRecord


import json


def extract_searchable_text(ocr_text: str) -> str:
    """
    Extracts text from both 'text' blocks and 'diagram' blocks (including Mermaid node labels
    and structural relationships) for indexing and searching.
    """
    if not ocr_text:
        return ""

    blocks = []
    try:
        data = json.loads(ocr_text)
        if isinstance(data, dict) and "blocks" in data and isinstance(data["blocks"], list):
            blocks = data["blocks"]
        elif isinstance(data, list):
            blocks = data
    except Exception:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", ocr_text)
        if match:
            try:
                data = json.loads(match[1])
                if isinstance(data, dict) and "blocks" in data:
                    blocks = data["blocks"]
            except Exception:
                pass

    text_parts = []
    if blocks:
        for b in blocks:
            b_type = b.get("type", "text")
            if b_type == "text" and "content" in b:
                content = b["content"]
                text_parts.append(content)
                for mermaid_code in re.findall(r"```mermaid\s*([\s\S]*?)\s*```", content, re.IGNORECASE):
                    node_labels = re.findall(r"\[(.*?)\]|\((.*?)\)|\{(.*?)\}", mermaid_code)
                    flattened_labels = [item for tuple_item in node_labels for item in tuple_item if item and not item.startswith("http")]
                    if flattened_labels:
                        text_parts.append("[Diagram Node Labels]: " + ", ".join(flattened_labels))
            elif b_type == "diagram" and "mermaid" in b:
                mermaid_code = b["mermaid"]
                text_parts.append(f"[Diagram]: {mermaid_code}")
                node_labels = re.findall(r"\[(.*?)\]|\((.*?)\)|\{(.*?)\}", mermaid_code)
                flattened_labels = [item for tuple_item in node_labels for item in tuple_item if item and not item.startswith("http")]
                if flattened_labels:
                    text_parts.append("[Diagram Node Labels]: " + ", ".join(flattened_labels))
    else:
        text_parts.append(ocr_text)
        for mermaid_code in re.findall(r"```mermaid\s*([\s\S]*?)\s*```", ocr_text, re.IGNORECASE):
            node_labels = re.findall(r"\[(.*?)\]|\((.*?)\)|\{(.*?)\}", mermaid_code)
            flattened_labels = [item for tuple_item in node_labels for item in tuple_item if item and not item.startswith("http")]
            if flattened_labels:
                text_parts.append("[Diagram Node Labels]: " + ", ".join(flattened_labels))

    return "\n\n".join(text_parts)


@dataclass
class SearchResult:
    page_number: int
    page_id: str
    source_filename: str
    match_count: int
    snippets: List[str]
    highlighted_full_text: str


class SearchIndexer:
    def __init__(self):
        self.pages: List[PageRecord] = []

    def update_index(self, pages: List[PageRecord]):
        """Updates the internal page index."""
        self.pages = pages

    def search(self, query: str, context_window: int = 120) -> List[SearchResult]:
        """
        Searches across all pages for the query string or words.
        Returns a list of SearchResults sorted by match count descending.
        """
        trimmed_query = query.strip()
        if not trimmed_query or not self.pages:
            return []

        # Escape query for safe regex
        escaped_query = re.escape(trimmed_query)
        pattern = re.compile(f"({escaped_query})", re.IGNORECASE)

        results: List[SearchResult] = []

        for page in self.pages:
            text = extract_searchable_text(page.ocr_text or "")
            if not text:
                continue

            matches = list(pattern.finditer(text))
            if not matches:
                continue

            # Generate snippets with surrounding context
            snippets = []
            for match in matches:
                start_idx = max(0, match.start() - context_window)
                end_idx = min(len(text), match.end() + context_window)

                snippet_raw = text[start_idx:end_idx].strip()
                # Clean up line breaks for compact preview
                snippet_clean = re.sub(r"\s+", " ", snippet_raw)

                # Highlight the matched word in snippet
                snippet_highlighted = pattern.sub(
                    r"<mark style='background-color: #fde047; color: #1e293b; padding: 2px 4px; border-radius: 3px; font-weight: 600;'>\1</mark>",
                    html.escape(snippet_clean)
                )

                prefix = "... " if start_idx > 0 else ""
                suffix = " ..." if end_idx < len(text) else ""
                snippets.append(f"{prefix}{snippet_highlighted}{suffix}")

            # Also generate highlighted full text
            highlighted_full = pattern.sub(
                r"<mark style='background-color: #fde047; color: #1e293b; padding: 2px 4px; border-radius: 3px; font-weight: 600;'>\1</mark>",
                html.escape(text)
            )

            results.append(SearchResult(
                page_number=page.page_number,
                page_id=page.page_id,
                source_filename=page.source_filename,
                match_count=len(matches),
                snippets=snippets[:5],  # Top 5 snippets per page
                highlighted_full_text=highlighted_full
            ))

        # Rank by match count
        results.sort(key=lambda r: r.match_count, reverse=True)
        return results


def highlight_terms_in_text(text: str, query: str) -> str:
    """Utility to highlight search query in raw text using styled HTML marks."""
    if not query.strip() or not text:
        return html.escape(text).replace("\n", "<br>")

    escaped_query = re.escape(query.strip())
    pattern = re.compile(f"({escaped_query})", re.IGNORECASE)

    escaped_text = html.escape(text)
    highlighted = pattern.sub(
        r"<mark style='background-color: #fde047; color: #1e293b; padding: 2px 4px; border-radius: 3px; font-weight: 600;'>\1</mark>",
        escaped_text
    )
    return highlighted.replace("\n", "<br>")
