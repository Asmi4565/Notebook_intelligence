"""
Standalone OCR test script for Notebook Intelligence System (NIS).
Tests Gemini multimodal vision API on an image file.
"""

import sys
import os
import time
import json
from PIL import Image
from dotenv import load_dotenv

load_dotenv(".env.production")
load_dotenv(".env")

# Step 1 Check: GEMINI_API_KEY presence
raw_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
api_key_found = bool(raw_key and raw_key.strip())
print(f"GEMINI_API_KEY status: {'found' if api_key_found else 'missing'}")

if not api_key_found:
    print("Error: GEMINI_API_KEY is missing from environment.")
    sys.exit(1)

from ocr_engine import get_gemini_client, PROMPT_HANDWRITING_OCR, normalize_ocr_response, get_gemini_model, perform_ocr_on_page

MODEL_NAME = get_gemini_model()

def run_test_ocr(image_path: str):
    print(f"\n--- Running OCR Test on: {image_path} ---")
    if not os.path.exists(image_path):
        print(f"Error: File not found: {image_path}")
        return

    try:
        img = Image.open(image_path)
        print(f"Loaded image: format={img.format}, size={img.size}, mode={img.mode}")

        # Resize for optimal vision quality if needed (longest side ~2000px)
        max_dim = 2000
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / float(max(w, h))
            new_size = (int(w * scale), int(h * scale))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            print(f"Resized image for vision: {new_size}")

        client = get_gemini_client()
        if client is None:
            print("Error: Could not initialize GenAI Client.")
            return

        print(f"Using Model: {MODEL_NAME}")
        start_time = time.time()

        # Strict instruction prompt
        strict_instruction = (
            "Transcribe exactly what is written on this page. Do not add, summarize, explain or invent anything. "
            "Mark unreadable words as [illegible]. Keep headings, lists, tables and formulas. "
            "If there is a diagram, output it as simple valid Mermaid (flowchart TD or LR, short labels)."
        )

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[img, strict_instruction],
            config={"response_mime_type": "application/json"}
        )
        elapsed = round(time.time() - start_time, 2)
        raw_text = response.text or ""

        print(f"\n[API Success] Elapsed Time: {elapsed} seconds")
        print("\n--- RAW API RESPONSE ---")
        print(raw_text)
        print("--- END RAW API RESPONSE ---\n")

        normalized = normalize_ocr_response(raw_text)
        try:
            parsed = json.loads(normalized)
            print("--- PARSED STRUCTURED JSON ---")
            print(json.dumps(parsed, indent=2))
        except Exception as pe:
            print(f"JSON Parsing warning: {pe}")

    except Exception as e:
        print(f"\n[API Exception] OCR call failed: {e}")

if __name__ == "__main__":
    test_img = sys.argv[1] if len(sys.argv) > 1 else r"c:\Project_antigravity\Notebook_intelligence\scratch\page1_from_db.jpg"
    run_test_ocr(test_img)
