"""
Document Processor module for Notebook Intelligence System (NIS).
Handles file validation, multi-page PDF rendering, image processing, and page tracking.
"""

from dataclasses import dataclass, field
import io
import os
from typing import List, Optional, Tuple
from PIL import Image
import pymupdf

MAX_FILE_SIZE_MB = 50
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


@dataclass
class PageRecord:
    page_id: str
    page_number: int
    source_filename: str
    image: Image.Image
    ocr_text: str = ""
    ocr_status: str = "pending"  # "pending", "success", "error"
    ocr_error: Optional[str] = None
    is_code: bool = False
    folder: str = ""
    metadata: dict = field(default_factory=dict)


def validate_file(filename: str, file_bytes: bytes) -> Tuple[bool, str]:
    """Validates file extension and size limits (FR-02)."""
    _, ext = os.path.splitext(filename.lower())
    if ext not in SUPPORTED_EXTENSIONS:
        return False, f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"

    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        return False, f"File size ({size_mb:.1f} MB) exceeds maximum limit of {MAX_FILE_SIZE_MB} MB."

    if len(file_bytes) == 0:
        return False, "File is empty."

    return True, "Valid"


def optimize_pil_image(pil_img: Image.Image, max_dim: int = 2000) -> Image.Image:
    """Downscales large camera photos to optimal resolution (longest side ~2000px) preserving handwriting clarity."""
    w, h = pil_img.size
    if max(w, h) > max_dim:
        scale = max_dim / float(max(w, h))
        new_w, new_h = int(w * scale), int(h * scale)
        pil_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return pil_img


def extract_pages_from_pdf(pdf_bytes: bytes, filename: str, dpi: int = 200) -> List[PageRecord]:
    """Renders every page of a PDF document into a PageRecord at 200 DPI with an optimized PIL Image."""
    pages: List[PageRecord] = []
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")

    try:
        total_pages = len(doc)
        for page_idx in range(total_pages):
            doc_page = doc[page_idx]
            pix = doc_page.get_pixmap(dpi=dpi)
            img_data = pix.tobytes("png")
            pil_img = Image.open(io.BytesIO(img_data)).convert("RGB")
            pil_img = optimize_pil_image(pil_img, max_dim=2000)

            page_rec = PageRecord(
                page_id=f"{filename}_p{page_idx + 1}",
                page_number=page_idx + 1,
                source_filename=filename,
                image=pil_img,
                ocr_status="pending",
                metadata={"total_pages": total_pages, "dpi": dpi}
            )
            pages.append(page_rec)
    finally:
        doc.close()

    return pages


def extract_pages_from_image(img_bytes: bytes, filename: str, page_number: int = 1) -> PageRecord:
    """Loads and optimizes a photo/image into a PageRecord."""
    pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    pil_img = optimize_pil_image(pil_img, max_dim=1500)
    return PageRecord(
        page_id=f"{filename}_p{page_number}",
        page_number=page_number,
        source_filename=filename,
        image=pil_img,
        ocr_status="pending"
    )


def process_uploaded_files(uploaded_files: list) -> Tuple[List[PageRecord], List[str]]:
    """
    Processes a list of file objects or tuples of (name, bytes).
    Preserves page order and returns (pages, error_messages).
    """
    all_pages: List[PageRecord] = []
    errors: List[str] = []

    global_page_counter = 1
    for f in uploaded_files:
        name = getattr(f, "name", None) or f[0]
        data = f.getvalue() if hasattr(f, "getvalue") else f[1]

        is_valid, err_msg = validate_file(name, data)
        if not is_valid:
            errors.append(f"{name}: {err_msg}")
            continue

        ext = os.path.splitext(name.lower())[1]
        try:
            if ext == ".pdf":
                pdf_pages = extract_pages_from_pdf(data, name)
                for p in pdf_pages:
                    p.page_number = global_page_counter
                    global_page_counter += 1
                    all_pages.append(p)
            else:
                img_page = extract_pages_from_image(data, name, page_number=global_page_counter)
                global_page_counter += 1
                all_pages.append(img_page)
        except Exception as ex:
            errors.append(f"Failed to process {name}: {str(ex)}")

    return all_pages, errors
