# 📘 Notebook Intelligence System (NIS) v2.0 — Technical Architecture & Component Guide

This document provides an in-depth breakdown of **what technologies and libraries were used**, **how each component is implemented**, and **how the files communicate with each other**.

![Notebook Intelligence System Architecture](system_architecture_diagram.jpg)

---

## 🛠️ 1. Technologies & Libraries Used

| Component / Layer | Technology | Why & How It Was Used |
| :--- | :--- | :--- |
| **Web Server & Backend API** | **FastAPI & Uvicorn** | Asynchronous REST API server handling all endpoints (`/explain`, `/quiz`, `/run-code`, `/api/upload`, `/api/search`) and static asset mounting. |
| **Persistent Storage** | **SQLite (`db.py`)** | Local relational database persisting notebooks, page scans, transcripts, user corrections, and quiz histories across server restarts. |
| **Multimodal Vision & AI** | **Google Gemini 2.5 Flash** | Transcribes messy handwriting, diagrams, formulas, and code blocks from note images. Generates grounded answers with citations and active-recall quizzes. |
| **Math & Formula Engine** | **KaTeX** | Client-side math rendering engine providing textbook-quality formatting for mathematical formulas (e.g., `$$\int_0^\infty e^{-x^2}dx$$`). |
| **PDF & Image Processing** | **`pypdfium2` & `Pillow` (PIL)** | C-backed PDF rendering engine converting multi-page PDFs into 150 DPI page scans and web-friendly Base64 images. |
| **Full-Text Search Engine** | **Custom In-Memory Token Indexer** | Inverted index tokenizing page text, scoring match frequencies, and extracting context snippets wrapped in `<mark>` tags. |
| **Secure Code Sandbox** | **Python Subprocess (`python -I`)** | Runs student code with 5-second hard timeouts, ephemeral isolated directories, and runtime monkeypatch blocks against socket/filesystem access. |
| **Frontend UI** | **HTML5, CSS3, & Modern JavaScript** | Clean decoupled Single Page Application (in `frontend/`) featuring dark mode, glassmorphism badges, and multi-notebook switching. |
| **Testing & Verification** | **`pytest` & HTTPX TestClient** | 16 automated test suites covering OCR processing, search ranking, sandbox security guardrails, schema validation, and API routes. |

---

## 📂 2. File-by-File Breakdown & Workflow

```
c:/Project_antigravity/Notebook_intelligence/
├── app.py                   # 🌐 FastAPI Gateway & Static Asset Server
├── db.py                    # 💾 SQLite Database Module (Notebooks, Pages, Quizzes)
├── document_processor.py    # 📄 PDF Rendering & Page Extraction (pypdfium2)
├── ocr_engine.py            # 👁️ Multimodal Handwriting Vision OCR (Gemini 2.5 Flash)
├── search_indexer.py        # 🔍 In-Memory Full-Text Search Engine
├── ai_assistant.py          # 💡 Grounded Q&A & Citation Generator
├── learning_assistant.py    # 📝 Quiz Generation & Interactive Active Recall
├── code_sandbox.py          # 🔒 Isolated Code Execution Environment
├── frontend/                # 🎨 Decoupled Frontend Single-Page App
│   ├── index.html           # Semantic HTML with KaTeX Math & Course Switcher
│   ├── css/style.css        # Responsive dark-theme styling
│   └── js/app.js            # UI controller (tabs, live editor, sandbox, export)
├── test_suite.py            # 🧪 16 Automated Pytest Test Cases
├── test_e2e_ui_flow.py      # 🚀 End-to-End Simulation Flow
├── requirements.txt         # 📦 Project Dependencies
└── .env.example             # 🔑 Environment Template
```

---

## 🚀 How to Run and Test

1. **Start the Application:**
   ```powershell
   python app.py
   ```
   Open **[http://localhost:8000](http://localhost:8000)** in your web browser.

2. **Run All Automated Tests:**
   ```powershell
   pytest test_suite.py -v
   ```

3. **Run End-to-End Flow Test:**
   ```powershell
   python test_e2e_ui_flow.py
   ```
