"""
OCR Engine module for Notebook Intelligence System (NIS).
Uses Google Gemini multimodal vision (gemini-2.5-flash) to extract handwritten text,
equations, diagrams, and programming code from notebook page images.
Never returns fake or simulated mock transcripts.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import io
import time
import json
import re
from typing import Callable, List, Optional, Tuple, Dict, Any
from PIL import Image
from document_processor import PageRecord

PROMPT_HANDWRITING_OCR = """Transcribe exactly what is written on this page. Do not add, summarize, explain or invent anything. Mark unreadable words as [illegible]. Keep headings, lists, tables and formulas. If there is a diagram, output it as simple valid Mermaid (flowchart TD or LR, short labels).

Return strictly a JSON object with a "blocks" array:
{
  "blocks": [
    { "type": "text", "content": "..." },
    { "type": "diagram", "mermaid": "flowchart TD\\n  A[Start] --> B[End]" }
  ]
}
"""


def is_valid_key_str(key: Optional[str]) -> bool:
    """Checks if a string is a non-empty, non-placeholder API key."""
    if not key:
        return False
    k = key.strip()
    if not k or k in ("your_key_here", "your_gemini_api_key_here", "your_google_api_key_here", "YOUR_API_KEY", "[SENSITIVE]"):
        return False
    if k.startswith("your_") or k.startswith("YOUR_") or k.startswith("[SENSITIVE"):
        return False
    return True


def normalize_ocr_response(raw_text: str) -> Optional[str]:
    """
    Ensures the OCR response is a valid JSON string with a 'blocks' array.
    Returns None if raw_text cannot be parsed as valid JSON blocks.
    """
    if not raw_text or not raw_text.strip():
        return None
    
    # Try parsing direct JSON
    try:
        data = json.loads(raw_text)
        if isinstance(data, dict) and "blocks" in data and isinstance(data["blocks"], list):
            return json.dumps(data)
        elif isinstance(data, list):
            return json.dumps({"blocks": data})
    except Exception:
        pass

    # Try extracting JSON block if wrapped in markdown code fence
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text)
    if match:
        try:
            data = json.loads(match[1])
            if isinstance(data, dict) and "blocks" in data and isinstance(data["blocks"], list):
                return json.dumps(data)
            elif isinstance(data, list):
                return json.dumps({"blocks": data})
        except Exception:
            pass

    return None


def get_gemini_client(api_key: Optional[str] = None):
    """Initializes Google GenAI Client if a valid API key is provided or present in environment."""
    if api_key and is_valid_key_str(api_key):
        key = api_key.strip()
    else:
        g_key = os.environ.get("GEMINI_API_KEY") or ""
        goo_key = os.environ.get("GOOGLE_API_KEY") or ""
        if is_valid_key_str(g_key):
            key = g_key.strip()
        elif is_valid_key_str(goo_key):
            key = goo_key.strip()
        else:
            key = ""

    if not key:
        return None
    try:
        from google import genai
        os.environ["GOOGLE_API_KEY"] = key
        os.environ["GEMINI_API_KEY"] = key
        return genai.Client(api_key=key)
    except Exception as e:
        print(f"Error initializing GenAI Client: {e}")
        return None


DISCOVERED_GEMINI_MODEL: Optional[str] = None
FAILED_GEMINI_MODELS: set = set()

# Current active Gemini Flash models in priority order
PREFERRED_FLASH_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]


def get_gemini_model() -> str:
    """Central getter for GEMINI_MODEL env var or auto-discovered model, defaulting to gemini-2.5-flash."""
    global DISCOVERED_GEMINI_MODEL
    if DISCOVERED_GEMINI_MODEL and DISCOVERED_GEMINI_MODEL not in FAILED_GEMINI_MODELS:
        return DISCOVERED_GEMINI_MODEL
    
    env_model = os.environ.get("GEMINI_MODEL", "").strip()
    if env_model and env_model not in FAILED_GEMINI_MODELS:
        return env_model
        
    return "gemini-2.5-flash"


def discover_active_gemini_model(client, failed_model: Optional[str] = None) -> str:
    """
    Queries ListModels API, blacklists failing models, and returns an active Gemini Flash model
    supporting generateContent. Never selects live-preview or capacity-starved preview/medium models.
    """
    global DISCOVERED_GEMINI_MODEL, FAILED_GEMINI_MODELS

    if failed_model:
        clean_failed = failed_model.replace("models/", "").strip()
        FAILED_GEMINI_MODELS.add(clean_failed)
        FAILED_GEMINI_MODELS.add(f"models/{clean_failed}")
        if DISCOVERED_GEMINI_MODEL in FAILED_GEMINI_MODELS:
            DISCOVERED_GEMINI_MODEL = None

    if DISCOVERED_GEMINI_MODEL and DISCOVERED_GEMINI_MODEL not in FAILED_GEMINI_MODELS:
        return DISCOVERED_GEMINI_MODEL

    try:
        models = list(client.models.list())
        catalog_models = set()
        EXCLUDED_KEYWORDS = ["live", "bidi", "realtime", "embed", "imagen", "audio", "tts", "stt"]
        for m in models:
            m_name = getattr(m, "name", "") or str(m)
            clean_name = m_name.replace("models/", "").strip()
            methods = getattr(m, "supported_generation_methods", []) or []
            if methods and "generateContent" not in methods:
                continue
            if any(k in clean_name.lower() for k in EXCLUDED_KEYWORDS):
                continue
            catalog_models.add(clean_name)

        # 1. Check preferred active flash models in order
        for pref in PREFERRED_FLASH_MODELS:
            if pref not in FAILED_GEMINI_MODELS and (pref in catalog_models or not catalog_models):
                DISCOVERED_GEMINI_MODEL = pref
                print(f"[Gemini Model Auto-Discovery] Selected active model: '{pref}'")
                return pref

        # 2. Check any flash model in catalog
        for cm in catalog_models:
            if cm not in FAILED_GEMINI_MODELS and "flash" in cm.lower():
                DISCOVERED_GEMINI_MODEL = cm
                print(f"[Gemini Model Auto-Discovery] Selected catalog flash model: '{cm}'")
                return cm

        # 3. Fallback to gemini-2.5-flash or gemini-2.0-flash
        fallback = "gemini-2.5-flash" if "gemini-2.5-flash" not in FAILED_GEMINI_MODELS else ("gemini-2.0-flash" if "gemini-2.0-flash" not in FAILED_GEMINI_MODELS else "gemini-1.5-flash")
        DISCOVERED_GEMINI_MODEL = fallback
        print(f"[Gemini Model Auto-Discovery] Fallback model selected: '{fallback}'")
        return fallback

    except Exception as e:
        print(f"[Gemini Model Auto-Discovery Warning] Could not list models ({e}), defaulting to gemini-2.5-flash")
        fallback = "gemini-2.5-flash" if "gemini-2.5-flash" not in FAILED_GEMINI_MODELS else ("gemini-2.0-flash" if "gemini-2.0-flash" not in FAILED_GEMINI_MODELS else "gemini-1.5-flash")
        DISCOVERED_GEMINI_MODEL = fallback
        return fallback


def perform_ocr_on_page(
    page: PageRecord,
    api_key: Optional[str] = None,
    model_name: Optional[str] = None
) -> Tuple[str, bool, Optional[str]]:
    """
    Performs OCR on a single PageRecord using real Gemini multimodal vision.
    Never returns fake/mock transcripts.
    Returns: (extracted_text_json, is_code, error_message)
    """
    if page.image is None:
        return "", False, "Page has no rendered image available for OCR."

    client = get_gemini_client(api_key)
    if client is None:
        return "", False, "API key missing. Please configure GEMINI_API_KEY in environment or settings."

    # Prepare image payload and measure dimensions
    img = page.image
    w, h = img.size
    buf = io.BytesIO()
    img_rgb = img.convert("RGB") if img.mode != "RGB" else img
    img_rgb.save(buf, format="JPEG", quality=85, optimize=True)
    img_bytes_len = buf.tell()

    primary_model = model_name or get_gemini_model()
    models_to_try = [primary_model]
    last_err_msg = None

    for m in models_to_try:
        # Retry up to 2 times on transient 429 / 503 errors
        max_retries = 2
        for attempt in range(max_retries + 1):
            start_t = time.time()
            try:
                # 1st Attempt: Request structured JSON
                response = client.models.generate_content(
                    model=m,
                    contents=[img_rgb, PROMPT_HANDWRITING_OCR],
                    config={"response_mime_type": "application/json"}
                )
                elapsed = round(time.time() - start_t, 2)
                raw_output = response.text or ""
                
                normalized = normalize_ocr_response(raw_output)
                if not normalized:
                    # Retry once with explicit plain prompt if JSON parsing failed
                    retry_prompt = "Transcribe all handwritten text and diagrams on this image accurately."
                    retry_resp = client.models.generate_content(
                        model=m,
                        contents=[img_rgb, retry_prompt]
                    )
                    raw_text = (retry_resp.text or raw_output).strip()
                    normalized = json.dumps({"blocks": [{"type": "text", "content": raw_text}]})

                is_code = "[CODE_DETECTED]" in normalized or "```" in normalized

                # Populate debug metadata (never exposing keys)
                page.metadata["ocr_debug"] = {
                    "model": m,
                    "image_size": f"{w}x{h}",
                    "payload_size_kb": round(img_bytes_len / 1024, 1),
                    "latency_sec": elapsed,
                    "status": "success",
                    "error": None
                }
                return normalized, is_code, None

            except Exception as ex:
                elapsed = round(time.time() - start_t, 2)
                err_str = str(ex)
                last_err_msg = err_str

                # Log failure
                print(f"[OCR Warning] Model '{m}' attempt {attempt+1} failed: {err_str}")

                # Populate debug metadata with error
                page.metadata["ocr_debug"] = {
                    "model": m,
                    "image_size": f"{w}x{h}",
                    "payload_size_kb": round(img_bytes_len / 1024, 1),
                    "latency_sec": elapsed,
                    "status": "error",
                    "error": err_str
                }

                # Handle 404/503/429 by blacklisting model and discovering active fallback model
                if any(term in err_str.lower() for term in ["404", "not_found", "503", "unavailable", "capacity", "429", "quota", "resource_exhausted"]):
                    discovered = discover_active_gemini_model(client, failed_model=m)
                    if discovered not in models_to_try:
                        models_to_try.append(discovered)
                        break

                break


    # Categorize error for plain-English user display
    err_lower = str(last_err_msg).lower()
    if any(term in err_lower for term in ["401", "unauthenticated", "api_key_invalid", "api key not valid", "unauthorized", "invalid authentication"]):
        user_err = "API key invalid or rejected by Gemini API."
    elif any(term in err_lower for term in ["429", "quota", "resource_exhausted", "rate_limit", "rate limit"]):
        user_err = "Quota exceeded, try again in a minute."
    elif any(term in err_lower for term in ["503", "unavailable", "capacity", "no capacity"]):
        user_err = "Model temporarily unavailable or capacity limited, try again in a minute."
    elif any(term in err_lower for term in ["404", "not_found", "not found", "no longer available"]):
        user_err = "Model not available."
    else:
        user_err = f"OCR failed: {last_err_msg or 'Unknown API error'}"

    return "", False, user_err


def batch_ocr_pages(
    pages: List[PageRecord],
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, PageRecord], None]] = None,
    force_rerun: bool = False
) -> List[PageRecord]:
    """
    Processes pages in parallel. If force_rerun is True, re-runs OCR on all pages.
    Never generates fake or mock transcripts.
    """
    if force_rerun:
        pending_pages = pages
    else:
        pending_pages = [p for p in pages if not (p.ocr_status == "success" and p.ocr_text)]
    
    total = len(pages)
    if not pending_pages:
        if progress_callback:
            for idx, p in enumerate(pages, start=1):
                progress_callback(idx, total, p)
        return pages

    def _ocr_worker(page: PageRecord) -> PageRecord:
        text, is_code, error = perform_ocr_on_page(page, api_key=api_key, model_name=model_name)
        if error:
            page.ocr_status = "error"
            page.ocr_error = error
            page.ocr_text = ""
        else:
            page.ocr_status = "success"
            page.ocr_text = text
            page.is_code = is_code
            page.ocr_error = None
        return page

    completed_count = total - len(pending_pages)
    for idx, page in enumerate(pending_pages):
        if idx > 0:
            time.sleep(0.3)
        _ocr_worker(page)
        completed_count += 1
        if progress_callback:
            progress_callback(completed_count, total, page)

    return pages
