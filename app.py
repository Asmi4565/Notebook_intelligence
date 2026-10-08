"""
Notebook Intelligence System (NIS) - Production Application
Modular backend application serving:
1. Static decoupled frontend at `GET /` (with KaTeX, Multi-Notebooks, and responsive UI)
2. Persistent SQLite database synchronization via `db.py`
3. REST API endpoints:
   - `POST /explain` (FR-10, FR-11)
   - `POST /quiz` (FR-12, FR-13, Section 6.3)
   - `POST /run-code` (FR-15, FR-16, FR-17, NFR-06)
   - `POST /api/upload` (FR-01, FR-02, FR-03)
   - `POST /api/load-sample` (Demo test fixtures)
   - `GET /api/pages` (FR-04)
   - `PUT /api/pages/{page_num}` (FR-06)
   - `POST /api/search` (FR-08, FR-09)
   - `GET /api/notebooks`, `POST /api/notebooks`, `DELETE /api/notebooks/{id}`
   - `GET /api/notebooks/{id}/export`, `GET /api/notebooks/{id}/export-quiz`
"""

import base64
import io
import os
import hashlib
import uuid
import re
import json
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request, Response
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw
from dotenv import load_dotenv

load_dotenv()

from document_processor import (
    validate_file,
    extract_pages_from_pdf,
    extract_pages_from_image,
    PageRecord
)
from ocr_engine import batch_ocr_pages, perform_ocr_on_page
from search_indexer import SearchIndexer, highlight_terms_in_text
from code_sandbox import execute_python_code, RunCodeRequest, RunCodeResponse
from ai_assistant import (
    explain_topic,
    generate_quiz,
    registry,
    ExplainRequest,
    ExplainResponse,
    QuizRequest,
    QuizQuestionItem
)
import db


# ==================== FastAPI Setup ====================
api = FastAPI(
    title="Notebook Intelligence System (NIS) API",
    description="Full-stack educational assistant for handwritten lecture notes, grounded Q&A, and code sandbox execution.",
    version="2.0.0"
)

# Mount decoupled frontend assets
frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.exists(frontend_dir):
    api.mount("/static", StaticFiles(directory=frontend_dir), name="static")


# Prevent browser caching of static UI assets during development and live updates
@api.middleware("http")
async def add_cache_control_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in ("/", "/app", "/api/auth/me", "/api/config"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ==================== Helper Utilities ====================
def image_to_base64_url(pil_img: Image.Image) -> str:
    """Converts a PIL Image to a compressed base64 Data URL for zero-dependency browser rendering."""
    buf = io.BytesIO()
    if pil_img.mode in ("RGBA", "P"):
        pil_img = pil_img.convert("RGB")
    pil_img.save(buf, format="JPEG", quality=85, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def base64_to_image(b64_url: str) -> Optional[Image.Image]:
    """Converts a base64 Data URL back to a PIL Image."""
    try:
        if "," in b64_url:
            b64_url = b64_url.split(",")[1]
        data = base64.b64decode(b64_url)
        return Image.open(io.BytesIO(data))
    except Exception:
        return None


def create_sample_page_image(title: str, text_lines: list[str]) -> Image.Image:
    width, height = 750, 950
    img = Image.new("RGB", (width, height), color=(254, 252, 245))
    draw = ImageDraw.Draw(img)

    draw.line([(90, 0), (90, height)], fill=(239, 68, 68), width=2)
    for y in range(80, height - 40, 36):
        draw.line([(0, y), (width, y)], fill=(226, 232, 240), width=1)

    draw.text((110, 45), title, fill=(30, 41, 59))
    y_pos = 90
    for line in text_lines:
        draw.text((110, y_pos), line, fill=(51, 65, 85))
        y_pos += 36
    return img


def sync_from_database(notebook_id: str = "current_session", user_id: Optional[str] = None) -> List[PageRecord]:
    """Hydrates in-memory registry from persistent SQLite database, scoped to user_id."""
    db_rows = db.get_db_pages(notebook_id, user_id=user_id)
    if not db_rows:
        existing = registry.get_pages(notebook_id)
        if existing:
            return existing
        return []
    pages = []
    for r in db_rows:
        img = base64_to_image(r["image_data"]) if r.get("image_data") else None
        p = PageRecord(
            page_id=r["page_id"],
            page_number=r["page_number"],
            source_filename=r["source_filename"],
            image=img,
            ocr_text=r["ocr_text"],
            ocr_status=r["ocr_status"],
            is_code=bool(r["is_code"]),
            ocr_error=r.get("ocr_error"),
            folder=r.get("folder") or ""
        )
        pages.append(p)
    registry.register(notebook_id, pages)
    return pages


# ==================== Authentication & Session Setup ====================
import hmac
import time
import secrets
from fastapi import Cookie, Request, Response, Depends, status

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY or SECRET_KEY in ("your_secret_key_here", "your_key_here", "[SENSITIVE]"):
    SECRET_KEY = "nis_v2_stable_production_session_secret_key_2026_x89"
os.environ["SECRET_KEY"] = SECRET_KEY

IS_PROD = bool(
    os.environ.get("VERCEL") or
    os.environ.get("ENV", "").lower() in ("production", "prod") or
    os.environ.get("VERCEL_ENV", "").lower() in ("production", "preview")
)
DISABLE_CODE_LAB = os.environ.get("DISABLE_CODE_LAB", "false").lower() in ("true", "1", "yes")
COOKIE_NAME = "nis_session"
FAILED_LOGIN_ATTEMPTS = {}  # ip -> list of timestamps


def create_session_token(user_id: str, email: str = "", name: str = "") -> str:
    timestamp = str(int(time.time()))
    safe_email = (email or "").replace(":", "_").strip().lower()
    safe_name = (name or "").replace(":", "_").strip()
    payload = f"{user_id}:{safe_email}:{safe_name}:{timestamp}"
    signature = hmac.new(SECRET_KEY.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_session_token(token: str) -> Optional[dict]:
    if not token or ":" not in token:
        return None
    parts = token.split(":")
    
    # 5-part self-provisioning token: user_id:email:name:timestamp:sig
    if len(parts) == 5:
        user_id, email, name, timestamp_str, signature = parts
        payload = f"{user_id}:{email}:{name}:{timestamp_str}"
        expected_sig = hmac.new(SECRET_KEY.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected_sig):
            u = db.get_user_by_id(user_id)
            if u:
                return u
            # Auto-provision user in local SQLite DB if missing (e.g. fresh lambda cold start)
            clean_name = name or "User"
            clean_email = email or f"{user_id}@user.local"
            db.create_user(clean_name, clean_email, "", user_id=user_id)
            return {"id": user_id, "name": clean_name, "email": clean_email}
        return None
        
    # 3-part legacy token: user_id:timestamp:sig
    if len(parts) == 3:
        user_id, timestamp_str, signature = parts
        payload = f"{user_id}:{timestamp_str}"
        expected_sig = hmac.new(SECRET_KEY.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected_sig):
            u = db.get_user_by_id(user_id)
            if u:
                return u
            db.create_user("User", f"{user_id}@user.local", "", user_id=user_id)
            return {"id": user_id, "name": "User", "email": f"{user_id}@user.local"}
        return None

    return None


def get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "127.0.0.1"


def decode_supabase_jwt(token: str) -> Optional[dict]:
    """Decodes unverified/verified payload of Supabase JWT token and syncs user to DB."""
    try:
        parts = token.split(".")
        if len(parts) == 3:
            payload_b64 = parts[1]
            payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
            decoded_bytes = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
            data = json.loads(decoded_bytes.decode("utf-8"))
            exp = data.get("exp")
            if exp and exp < time.time():
                return None
            user_id = data.get("sub")
            email = (data.get("email") or "").strip().lower()
            metadata = data.get("user_metadata", {})
            raw_name = (
                metadata.get("full_name") or
                metadata.get("name") or
                metadata.get("display_name") or
                metadata.get("user_name") or
                metadata.get("preferred_username") or
                ((metadata.get("given_name", "") + " " + metadata.get("family_name", "")).strip() if metadata.get("given_name") else "") or
                data.get("name")
            )
            if raw_name and raw_name.strip() and raw_name.strip().lower() != "user":
                name = raw_name.strip()
            elif email:
                raw_prefix = email.split("@")[0].replace(".", " ").replace("_", " ").replace("-", " ")
                name = " ".join([w.capitalize() for w in raw_prefix.split() if w]) or "User"
            else:
                name = "User"

            if user_id:
                # Sync user into local SQLite database so notebooks and profile operations work smoothly
                existing = db.get_user_by_id(user_id)
                if not existing:
                    if email:
                        by_email = db.get_user_by_email(email)
                        if by_email:
                            user_id = by_email["id"]
                            if by_email.get("name") and by_email["name"] not in ("User", ""):
                                name = by_email["name"]
                            elif name and name != "User":
                                db.update_user_name(user_id, name)
                        else:
                            db.create_user(name, email, "", user_id=user_id)
                    else:
                        db.create_user(name, f"{user_id}@supabase.user", "", user_id=user_id)
                else:
                    if existing.get("name") and existing["name"] not in ("User", ""):
                        name = existing["name"]
                    elif name and name != "User":
                        db.update_user_name(user_id, name)
                    email = existing.get("email") or email
                return {"id": user_id, "name": name, "email": email}
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error decoding supabase jwt: {e}")
    return None


def get_current_user_from_request(request: Request) -> Optional[dict]:
    if not request:
        return None
    auth_header = request.headers.get("Authorization", "")
    header_token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else None
    cookie_token = request.cookies.get(COOKIE_NAME)

    if header_token:
        if header_token.startswith("eyJ"):
            supa_user = decode_supabase_jwt(header_token)
            if supa_user:
                return supa_user
        verified = verify_session_token(header_token)
        if verified:
            return verified

    if cookie_token:
        if cookie_token.startswith("eyJ"):
            supa_user = decode_supabase_jwt(cookie_token)
            if supa_user:
                return supa_user
        verified = verify_session_token(cookie_token)
        if verified:
            return verified

    return None


def get_effective_notebook_id(request: Request, requested_id: Optional[str] = "current_session") -> str:
    user = get_current_user_from_request(request)
    user_id = user["id"] if user else None
    req_id = (requested_id or "").strip()
    if user_id:
        user_default_id = f"nb_{user_id[:8]}"
        user_name = user.get("name") or "My"
        if not req_id or req_id == "current_session":
            db.create_notebook(user_default_id, f"{user_name}'s Notes", user_id=user_id)
            return user_default_id
        if db.verify_notebook_owner(req_id, user_id):
            return req_id
        db.create_notebook(user_default_id, f"{user_name}'s Notes", user_id=user_id)
        return user_default_id
    return req_id if req_id else "current_session"


def require_current_user(request: Request) -> dict:
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in."
        )
    return user


# ==================== Auth Schemas & Endpoints ====================

class SignUpRequest(BaseModel):
    name: str = Field(..., min_length=1)
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)


class LoginRequest(BaseModel):
    email: str
    password: str


@api.post("/api/auth/signup")
def auth_signup(req: SignUpRequest, response: Response):
    existing = db.get_user_by_email(req.email)
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email address already exists.")
    user = db.create_user(req.name, req.email, req.password)
    token = create_session_token(user["id"], user.get("email", ""), user.get("name", ""))
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=IS_PROD,
        samesite="lax",
        max_age=86400 * 30
    )
    return {"status": "success", "token": token, "user": {"id": user["id"], "name": user["name"], "email": user["email"]}}


@api.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request, response: Response):
    ip = get_client_ip(request)
    now = time.time()
    FAILED_LOGIN_ATTEMPTS.setdefault(ip, [])
    FAILED_LOGIN_ATTEMPTS[ip] = [t for t in FAILED_LOGIN_ATTEMPTS[ip] if now - t < 60]
    attempts = FAILED_LOGIN_ATTEMPTS[ip]
    max_attempts = 5 if IS_PROD else 50
    if len(attempts) >= max_attempts:
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Please wait 1 minute before trying again."
        )

    user = db.get_user_by_email(req.email)
    if not user:
        FAILED_LOGIN_ATTEMPTS[ip].append(now)
        raise HTTPException(
            status_code=401,
            detail="Account not found. Please click 'Sign Up' to create your account with this email address."
        )

    if not db.verify_password(req.password, user["password_hash"]):
        FAILED_LOGIN_ATTEMPTS[ip].append(now)
        raise HTTPException(
            status_code=401,
            detail="Incorrect password. Please verify your password and try again."
        )

    FAILED_LOGIN_ATTEMPTS[ip] = []

    # Upgrade placeholder name "User" or empty string from email prefix
    current_name = (user.get("name") or "").strip()
    if not current_name or current_name.lower() in ("user", ""):
        raw_prefix = req.email.split("@")[0].replace(".", " ").replace("_", " ").replace("-", " ")
        resolved_name = " ".join([w.capitalize() for w in raw_prefix.split() if w]) or "Account"
        if resolved_name and resolved_name != "Account":
            db.update_user_name(user["id"], resolved_name)
            user["name"] = resolved_name

    token = create_session_token(user["id"], user.get("email", ""), user.get("name", ""))
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=IS_PROD,
        samesite="lax",
        max_age=86400 * 30
    )
    return {"status": "success", "token": token, "user": {"id": user["id"], "name": user["name"], "email": user["email"]}}


@api.post("/api/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"status": "success"}


@api.get("/api/auth/me")
def auth_me(request: Request, response: Response):
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Upgrade placeholder name "User" or empty string from email prefix
    current_name = (user.get("name") or "").strip()
    if not current_name or current_name.lower() in ("user", ""):
        raw_prefix = (user.get("email") or "").split("@")[0].replace(".", " ").replace("_", " ").replace("-", " ")
        resolved_name = " ".join([w.capitalize() for w in raw_prefix.split() if w]) or "Account"
        if resolved_name and resolved_name != "Account":
            db.update_user_name(user["id"], resolved_name)
            user["name"] = resolved_name

    fresh_token = create_session_token(user["id"], user.get("email", ""), user.get("name", ""))
    response.set_cookie(
        key=COOKIE_NAME,
        value=fresh_token,
        httponly=True,
        secure=IS_PROD,
        samesite="lax",
        max_age=86400 * 30
    )
    
    return {
        "status": "success", 
        "token": fresh_token,
        "user": {"id": user["id"], "name": user["name"], "email": user["email"]}
    }


class UpdateProfileRequest(BaseModel):
    name: str = Field(..., min_length=1)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6)


@api.put("/api/user/profile")
def update_profile(req: UpdateProfileRequest, request: Request):
    user = require_current_user(request)
    success = db.update_user_name(user["id"], req.name)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update profile name.")
    return {"status": "success", "name": req.name.strip()}


@api.post("/api/user/change-password")
def change_password(req: ChangePasswordRequest, request: Request):
    user = require_current_user(request)
    db_user = db.get_user_by_id(user["id"])
    if not db_user:
        raise HTTPException(status_code=404, detail="User account not found.")

    user_full = db.get_user_by_email(db_user["email"])
    if user_full and user_full.get("password_hash"):
        if not db.verify_password(req.current_password, user_full["password_hash"]):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")

    success = db.update_user_password(user["id"], req.new_password)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update password.")
    return {"status": "success", "message": "Password updated successfully."}


@api.get("/api/user/stats")
def get_user_stats_endpoint(request: Request):
    user = require_current_user(request)
    stats = db.get_user_stats(user["id"])
    return {"status": "success", "stats": stats}


@api.get("/api/config")
def get_public_config():
    raw_url = (os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip()
    clean_url = ""
    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        try:
            from urllib.parse import urlparse
            parsed = urlparse(raw_url)
            clean_url = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            clean_url = ""
    return {
        "supabase_url": clean_url,
        "supabase_anon_key": os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or ""
    }


# ==================== Notebook / Course Management Routes ====================




# ==================== Page Serving Routes ====================

@api.get("/", response_class=HTMLResponse)
def serve_home(request: Request):
    user = get_current_user_from_request(request)
    if user:
        index_path = os.path.join(frontend_dir, "index.html")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    landing_path = os.path.join(frontend_dir, "landing.html")
    if os.path.exists(landing_path):
        with open(landing_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Notebook Intelligence System</h1><p>Frontend assets missing.</p>")


@api.get("/app", response_class=HTMLResponse)
def serve_app(request: Request):
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Notebook Intelligence System</h1><p>Frontend assets missing.</p>")







# ==================== Notebook Management Endpoints ====================

class CreateNotebookRequest(BaseModel):
    id: str
    name: str


@api.get("/api/notebooks")
def list_notebooks(request: Request):
    user = get_current_user_from_request(request)
    user_id = user["id"] if user else None
    return db.list_notebooks(user_id=user_id)


@api.post("/api/notebooks")
def create_new_notebook(req: CreateNotebookRequest, request: Request):
    user = get_current_user_from_request(request)
    user_id = user["id"] if user else None
    return db.create_notebook(req.id, req.name, user_id=user_id)



@api.delete("/api/notebooks/{notebook_id}")
def delete_notebook_endpoint(notebook_id: str, request: Request):
    user = get_current_user_from_request(request)
    user_id = user["id"] if user else None
    if not db.verify_notebook_owner(notebook_id, user_id):
        raise HTTPException(status_code=403, detail="You do not have permission to delete this notebook.")
    registry.register(notebook_id, [])
    return {"status": "success", "deleted": db.delete_notebook(notebook_id, user_id=user_id)}


def generate_collective_pdf(pages: List[PageRecord], title: str = "Collective Academic Notes") -> bytes:
    """
    Generates a consolidated, publication-quality PDF from a list of PageRecords.
    Includes a cover header, page numbers, scanned handwritten images, and clean digitized notes.
    """
    import pymupdf
    import io
    from datetime import datetime
    
    doc = pymupdf.open()
    
    # 1. Cover / Title Page
    cover = doc.new_page(width=595, height=842)  # A4 points
    cover.draw_rect(pymupdf.Rect(40, 40, 555, 140), color=(0.15, 0.35, 0.8), fill=(0.95, 0.97, 1.0), width=1.5)
    cover.insert_text(pymupdf.Point(60, 85), "Notebook Intelligence System", fontsize=20, fontname="helv", color=(0.1, 0.2, 0.6))
    cover.insert_text(pymupdf.Point(60, 115), f"Collective Notes: {title}", fontsize=14, fontname="helv", color=(0.2, 0.2, 0.2))
    
    meta_text = (
        f"Export Date: {datetime.now().strftime('%B %d, %Y - %H:%M')}\n"
        f"Total Notes / Pages: {len(pages)}\n"
        f"Compiled from handwritten notes, diagrams, and OCR transcriptions.\n"
    )
    cover.insert_text(pymupdf.Point(60, 170), meta_text, fontsize=11, fontname="helv", color=(0.3, 0.3, 0.3))
    
    # Table of contents summary
    y = 250
    cover.insert_text(pymupdf.Point(60, y), "Table of Contents:", fontsize=13, fontname="helv", color=(0.1, 0.2, 0.6))
    y += 24
    for idx, p in enumerate(pages[:22], 1):
        f_name = p.source_filename or f"Page {p.page_number}"
        fld = f" [{p.folder}]" if getattr(p, "folder", None) else ""
        cover.insert_text(pymupdf.Point(70, y), f"• Note {idx}: {f_name}{fld}", fontsize=10, fontname="helv", color=(0.2, 0.2, 0.2))
        y += 18
        if y > 780:
            break
            
    cover.insert_text(pymupdf.Point(60, 810), "Generated by Notebook Intelligence System v2.0", fontsize=9, fontname="helv", color=(0.5, 0.5, 0.5))
    
    # 2. Pages with Scan and Transcription
    for idx, p in enumerate(pages, 1):
        page = doc.new_page(width=595, height=842)
        # Header banner
        page.draw_rect(pymupdf.Rect(40, 30, 555, 60), fill=(0.93, 0.95, 0.98))
        page.insert_text(pymupdf.Point(50, 48), f"Note #{idx} | {p.source_filename}", fontsize=10, fontname="helv", color=(0.15, 0.25, 0.5))
        if getattr(p, "folder", None):
            page.insert_text(pymupdf.Point(380, 48), f"Folder: {p.folder}", fontsize=10, fontname="helv", color=(0.2, 0.4, 0.2))
        
        content_top = 70
        if p.image:
            try:
                buf = io.BytesIO()
                img_to_save = p.image.convert("RGB")
                img_to_save.save(buf, format="JPEG", quality=85)
                img_bytes = buf.getvalue()
                
                w, h = p.image.size
                aspect = h / max(w, 1)
                max_w = 515
                max_h = 380
                if aspect * max_w > max_h:
                    img_h = max_h
                    img_w = img_h / aspect
                else:
                    img_w = max_w
                    img_h = img_w * aspect
                img_x = 40 + (max_w - img_w) / 2
                img_rect = pymupdf.Rect(img_x, content_top, img_x + img_w, content_top + img_h)
                page.insert_image(img_rect, stream=img_bytes)
                content_top += img_h + 15
            except Exception as e:
                print(f"Error inserting image into PDF: {e}")
        
        # Divider line
        page.draw_line(pymupdf.Point(40, content_top), pymupdf.Point(555, content_top), color=(0.8, 0.8, 0.8), width=0.8)
        content_top += 18
        
        page.insert_text(pymupdf.Point(40, content_top), "Digitized Content & Transcription:", fontsize=10, fontname="helv", color=(0.1, 0.2, 0.6))
        content_top += 16
        
        raw_text = p.ocr_text or ""
        try:
            d = json.loads(raw_text)
            if isinstance(d, dict) and "blocks" in d:
                lines = []
                for b in d["blocks"]:
                    if b.get("type") == "diagram" and b.get("mermaid"):
                        lines.append(f"[Diagram: {b['mermaid'].splitlines()[0] if b['mermaid'].splitlines() else 'Mermaid'}]")
                    elif b.get("content"):
                        lines.append(b["content"])
                raw_text = "\n".join(lines)
        except Exception:
            pass
            
        text_lines = raw_text.splitlines()
        max_y = 800
        for line in text_lines:
            clean_line = line.strip()
            if not clean_line:
                content_top += 8
                continue
            while len(clean_line) > 85:
                part = clean_line[:85]
                clean_line = clean_line[85:]
                if content_top < max_y:
                    page.insert_text(pymupdf.Point(40, content_top), part, fontsize=8.5, fontname="helv", color=(0.2, 0.2, 0.2))
                    content_top += 12
            if content_top < max_y:
                page.insert_text(pymupdf.Point(40, content_top), clean_line, fontsize=8.5, fontname="helv", color=(0.2, 0.2, 0.2))
                content_top += 12
            else:
                page.insert_text(pymupdf.Point(40, max_y), "...[full content viewable in digital app]", fontsize=8, fontname="helv", color=(0.5, 0.5, 0.5))
                break
                
        page.insert_text(pymupdf.Point(40, 825), f"Notebook Intelligence System | Page {idx} of {len(pages)}", fontsize=8, fontname="helv", color=(0.5, 0.5, 0.5))
        
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@api.get("/api/folders/{folder_name}/export-pdf")
def export_folder_pdf_endpoint(folder_name: str, request: Request, notebook_id: str = "current_session"):
    user = get_current_user_from_request(request)
    user_id = user["id"] if user else None
    nb_id = get_effective_notebook_id(request, notebook_id)
    pages = sync_from_database(nb_id, user_id=user_id)
    
    clean_folder = folder_name.strip()
    if clean_folder.lower() == "all":
        target_pages = pages
        title = "All Notes"
    elif clean_folder == "__unassigned__":
        target_pages = [p for p in pages if not getattr(p, "folder", None)]
        title = "Unassigned Notes"
    else:
        target_pages = [p for p in pages if getattr(p, "folder", None) == clean_folder]
        title = clean_folder
        
    if not target_pages:
        raise HTTPException(status_code=404, detail=f"No notes found in folder '{title}'.")
        
    pdf_bytes = generate_collective_pdf(target_pages, title=title)
    safe_name = re.sub(r"[^\w\-]", "_", title)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}_collective_notes.pdf"'
        }
    )


@api.get("/api/notebooks/{notebook_id}/export-pdf")
def export_notebook_pdf_endpoint(notebook_id: str, request: Request, folder: Optional[str] = None):
    user = get_current_user_from_request(request)
    user_id = user["id"] if user else None
    nb_id = get_effective_notebook_id(request, notebook_id)
    pages = sync_from_database(nb_id, user_id=user_id)
    
    if folder and folder.strip() and folder.strip().lower() != "all":
        clean_folder = folder.strip()
        if clean_folder == "__unassigned__":
            target_pages = [p for p in pages if not getattr(p, "folder", None)]
            title = "Unassigned Notes"
        else:
            target_pages = [p for p in pages if getattr(p, "folder", None) == clean_folder]
            title = clean_folder
    else:
        target_pages = pages
        title = "Complete Notebook"
        
    if not target_pages:
        raise HTTPException(status_code=404, detail="No notes available to export as PDF.")
        
    pdf_bytes = generate_collective_pdf(target_pages, title=title)
    safe_name = re.sub(r"[^\w\-]", "_", title)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}_collective_notes.pdf"'
        }
    )


@api.get("/api/notebooks/{notebook_id}/export")
def export_notebook_markdown(notebook_id: str, request: Request):
    user = get_current_user_from_request(request)
    user_id = user["id"] if user else None
    nb_id = get_effective_notebook_id(request, notebook_id)
    pages = sync_from_database(nb_id, user_id=user_id)
    
    md_lines = [f"# 📓 Notebook Export: {nb_id}\n", f"*Generated by Notebook Intelligence System*\n\n---\n"]
    for p in pages:
        md_lines.append(f"## Page {p.page_number} ({p.source_filename})\n\n")
        md_lines.append(f"{p.ocr_text}\n\n---\n")
    
    return {"markdown": "".join(md_lines), "page_count": len(pages)}


@api.get("/api/notebooks/{notebook_id}/export-quiz")
def export_quiz_anki(notebook_id: str, request: Request):
    user = get_current_user_from_request(request)
    user_id = user["id"] if user else None
    nb_id = get_effective_notebook_id(request, notebook_id)
    pages = sync_from_database(nb_id, user_id=user_id)
    if not registry.exists(nb_id):
        sync_from_database(nb_id)
    questions = generate_quiz(notebook_id=nb_id, num_questions=5)
    
    # Anki CSV format: "Front Text","Back Text"
    csv_lines = ["Question,Answer,Rationale,Page"]
    for q in questions:
        q_text = q.get("question", "") if isinstance(q, dict) else getattr(q, "question", "")
        ans_text = q.get("correct_answer", "") if isinstance(q, dict) else getattr(q, "correct_answer", "")
        rat_text = q.get("explanation", "") if isinstance(q, dict) else getattr(q, "explanation", "")
        pg_num = q.get("page_number", "") if isinstance(q, dict) else getattr(q, "page_number", "")
        
        q_fmt = f"\"{str(q_text).replace('\"', '\"\"')}\""
        ans_fmt = f"\"{str(ans_text).replace('\"', '\"\"')}\""
        rat_fmt = f"\"{str(rat_text).replace('\"', '\"\"')}\""
        csv_lines.append(f"{q_fmt},{ans_fmt},{rat_fmt},{pg_num}")
        
    return {"csv": "\n".join(csv_lines), "count": len(questions)}


# ==================== Pages & OCR Endpoints ====================

class SearchRequest(BaseModel):
    query: str
    notebook_id: str = "current_session"


class PageUpdateRequest(BaseModel):
    ocr_text: str


import json
import re


def parse_blocks_helper(ocr_text: str) -> list:
    """Safely extracts blocks array from ocr_text JSON string or text."""
    if not ocr_text:
        return [{"type": "text", "content": ""}]
    try:
        data = json.loads(ocr_text)
        if isinstance(data, dict) and "blocks" in data and isinstance(data["blocks"], list):
            return data["blocks"]
        elif isinstance(data, list):
            return data
    except Exception:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", ocr_text)
        if match:
            try:
                data = json.loads(match[1])
                if isinstance(data, dict) and "blocks" in data:
                    return data["blocks"]
            except Exception:
                pass
    return [{"type": "text", "content": ocr_text}]


@api.get("/api/pages")
def get_pages(request: Request, notebook_id: str = "current_session"):
    user = get_current_user_from_request(request)
    user_id = user["id"] if user else None
    nb_id = get_effective_notebook_id(request, notebook_id)
    pages = sync_from_database(nb_id, user_id=user_id)

    output = []
    for p in pages:
        output.append({
            "page_id": p.page_id,
            "page_number": p.page_number,
            "source_filename": p.source_filename,
            "ocr_text": p.ocr_text,
            "blocks": parse_blocks_helper(p.ocr_text),
            "ocr_status": p.ocr_status,
            "is_code": p.is_code,
            "ocr_error": p.ocr_error,
            "folder": getattr(p, "folder", "") or "",
            "image_url": image_to_base64_url(p.image) if p.image else None
        })
    return output


@api.put("/api/pages/{page_number}")
def update_page_text(page_number: int, req: PageUpdateRequest, request: Request, notebook_id: str = "current_session"):
    nb_id = get_effective_notebook_id(request, notebook_id)
    pages = registry.get_pages(nb_id)
    if not pages:
        pages = sync_from_database(nb_id)
        
    found = False
    for p in pages:
        if p.page_number == page_number:
            p.ocr_text = req.ocr_text
            p.is_code = "[CODE_DETECTED]" in req.ocr_text or "```" in req.ocr_text
            found = True
            db.update_page_transcript(nb_id, page_number, p.ocr_text, p.is_code)
            break
    if not found:
        raise HTTPException(status_code=404, detail="Page not found")

    # Re-index
    registry.register(nb_id, pages)
    return {"status": "success", "page_number": page_number}


@api.post("/api/re-ocr/{page_number}")
def re_run_ocr(page_number: int, request: Request, notebook_id: str = "current_session"):
    nb_id = get_effective_notebook_id(request, notebook_id)
    raw_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    has_key = bool(raw_key and raw_key.strip() and raw_key.strip() != "your_key_here")
    if not has_key:
        return {
            "status": "error",
            "message": "AI service is not configured. Please contact the app owner."
        }

    pages = registry.get_pages(nb_id)
    if not pages:
        pages = sync_from_database(nb_id)
        
    target_page = None
    for p in pages:
        if p.page_number == page_number:
            target_page = p
            break
    if not target_page:
        raise HTTPException(status_code=404, detail="Page not found")

    text, is_code, err = perform_ocr_on_page(target_page)
    if err:
        return {"status": "error", "message": err}
    target_page.ocr_text = text
    target_page.is_code = is_code
    target_page.ocr_status = "success"
    target_page.ocr_error = None

    db.update_page_transcript(nb_id, page_number, text, is_code)
    registry.register(nb_id, pages)
    return {
        "status": "success",
        "page_number": page_number,
        "ocr_text": text,
        "is_code": is_code
    }


class BulkDeleteRequest(BaseModel):
    page_numbers: List[int]
    notebook_id: str = "current_session"


@api.delete("/api/pages/{page_number}")
def delete_single_page(page_number: int, request: Request, notebook_id: str = "current_session"):
    nb_id = get_effective_notebook_id(request, notebook_id)
    updated_rows = db.delete_page(nb_id, page_number)
    sync_from_database(nb_id)
    return {
        "status": "success",
        "deleted_page": page_number,
        "remaining_pages": len(updated_rows)
    }


@api.post("/api/pages/delete-bulk")
def delete_bulk_pages(req: BulkDeleteRequest, request: Request):
    nb_id = get_effective_notebook_id(request, req.notebook_id)
    updated_rows = db.delete_pages_by_numbers(nb_id, req.page_numbers)
    sync_from_database(nb_id)
    return {
        "status": "success",
        "deleted_count": len(req.page_numbers),
        "remaining_pages": len(updated_rows)
    }


@api.delete("/api/documents/{source_filename}")
def delete_document(source_filename: str, request: Request, notebook_id: str = "current_session"):
    nb_id = get_effective_notebook_id(request, notebook_id)
    updated_rows = db.delete_document_pages(nb_id, source_filename)
    sync_from_database(nb_id)
    return {
        "status": "success",
        "deleted_document": source_filename,
        "remaining_pages": len(updated_rows)
    }


class RestorePagesRequest(BaseModel):
    notebook_id: str = "current_session"
    pages: List[dict]


@api.post("/api/pages/restore")
def restore_pages_endpoint(req: RestorePagesRequest, request: Request):
    user = get_current_user_from_request(request)
    user_id = user["id"] if user else None
    nb_id = get_effective_notebook_id(request, req.notebook_id)
    existing = db.get_db_pages(nb_id, user_id=user_id)
    if not existing and req.pages:
        db_records = []
        for p in req.pages:
            db_records.append({
                "page_id": p.get("page_id") or f"{nb_id}_p{p.get('page_number', 1)}_{uuid.uuid4().hex[:6]}",
                "page_number": p.get("page_number", 1),
                "source_filename": p.get("source_filename", "restored_page.png"),
                "image_data": p.get("image_url") or p.get("image_data"),
                "ocr_text": p.get("ocr_text", ""),
                "ocr_status": p.get("ocr_status", "success"),
                "is_code": bool(p.get("is_code", False)),
                "ocr_error": p.get("ocr_error"),
                "folder": p.get("folder") or None
            })
        db.save_pages(nb_id, db_records, user_id=user_id)
        sync_from_database(nb_id, user_id=user_id)
        return {"status": "success", "restored": len(db_records)}
    return {"status": "skipped", "existing": len(existing)}


# Hydrate active session from database if exists
try:
    sync_from_database("current_session")
except Exception as e:
    print(f"[DB Warning] Initial database sync deferred: {e}")



class FolderUpdateRequest(BaseModel):
    folder: Optional[str] = ""


class CreateFolderRequest(BaseModel):
    name: str


@api.get("/api/folders")
def get_folders_endpoint(request: Request, notebook_id: str = "current_session"):
    user = get_current_user_from_request(request)
    user_id = user["id"] if user else None
    nb_id = get_effective_notebook_id(request, notebook_id)
    folders = db.get_notebook_folders(nb_id, user_id=user_id)
    return {"folders": folders}


@api.post("/api/folders")
def create_folder_endpoint(req: CreateFolderRequest, request: Request, notebook_id: str = "current_session"):
    nb_id = get_effective_notebook_id(request, notebook_id)
    clean_name = req.name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Folder name cannot be empty.")
    result = db.create_folder(nb_id, clean_name)
    return {"status": "success", "folder": result}


@api.delete("/api/folders/{folder_name}")
def delete_folder_endpoint(folder_name: str, request: Request, notebook_id: str = "current_session"):
    nb_id = get_effective_notebook_id(request, notebook_id)
    db.delete_folder(nb_id, folder_name)
    sync_from_database(nb_id)
    return {"status": "success", "deleted": folder_name}

@api.get("/api/folders/{folder_name}/pages")
def get_pages_by_folder(folder_name: str, request: Request, notebook_id: str = "current_session"):
    nb_id = get_effective_notebook_id(request, notebook_id)
    # Retrieve all pages for the notebook (including user‑specific if logged in)
    pages = db.get_db_pages(nb_id, user_id=get_current_user_from_request(request).get("id") if get_current_user_from_request(request) else None)
    # Filter by folder name (case‑sensitive match)
    filtered = [p for p in pages if p.get("folder") == folder_name]
    return {"folder": folder_name, "pages": filtered}


@api.put("/api/pages/{page_number}/folder")
def update_page_folder_endpoint(page_number: int, req: FolderUpdateRequest, request: Request, notebook_id: str = "current_session"):
    nb_id = get_effective_notebook_id(request, notebook_id)
    folder_val = (req.folder or "").strip() or None
    db.update_page_folder(nb_id, page_number, folder_val)
    sync_from_database(nb_id)
    return {"status": "success", "page_number": page_number, "folder": folder_val or ""}


@api.put("/api/documents/{source_filename}/folder")
def update_document_folder_endpoint(source_filename: str, req: FolderUpdateRequest, request: Request, notebook_id: str = "current_session"):
    nb_id = get_effective_notebook_id(request, notebook_id)
    folder_val = (req.folder or "").strip() or None
    db.update_document_folder(nb_id, source_filename, folder_val)
    sync_from_database(nb_id)
    return {"status": "success", "source_filename": source_filename, "folder": folder_val or ""}


@api.post("/api/upload")
async def upload_files(
    request: Request,
    files: List[UploadFile] = File(...),
    notebook_id: str = "current_session",
    folder: Optional[str] = Form(None)
):
    user = get_current_user_from_request(request)
    user_id = user["id"] if user else None
    usage_user_id = user_id or "anonymous"
    nb_id = get_effective_notebook_id(request, notebook_id)
    allowed, count, limit = db.check_and_increment_daily_usage(usage_user_id, action_type="ocr", limit=50)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Daily OCR upload limit reached ({count}/{limit} uploads per day)."
        )

    existing = sync_from_database(nb_id, user_id=user_id)
    new_pages = []
    errors = []
    existing_nums = [p.page_number for p in existing if p.page_number is not None]
    offset = max(existing_nums, default=0)
    folder_param = folder or request.query_params.get("folder")
    target_folder = folder_param.strip() if (folder_param and folder_param.strip()) else None

    for f in files:
        fname = f.filename or "uploaded_page.png"
        try:
            content = await f.read()
            valid, msg = validate_file(fname, content)
            if not valid:
                errors.append(f"{fname}: {msg}")
                continue
            ext = os.path.splitext(fname.lower())[1]
            if ext == ".pdf":
                pdf_pages = extract_pages_from_pdf(content, fname)
                for p in pdf_pages:
                    p.page_number = offset + len(new_pages) + 1
                    p.page_id = f"{nb_id}_p{p.page_number}_{uuid.uuid4().hex[:6]}"
                    p.folder = target_folder or ""
                    new_pages.append(p)
            else:
                p = extract_pages_from_image(content, fname, page_number=offset + len(new_pages) + 1)
                p.page_id = f"{nb_id}_p{p.page_number}_{uuid.uuid4().hex[:6]}"
                p.folder = target_folder or ""
                new_pages.append(p)
        except Exception as e:
            errors.append(f"{fname}: {str(e)}")

    all_pages = existing + new_pages
    # Run batch OCR on newly added pending pages
    batch_ocr_pages(new_pages)
    registry.register(nb_id, all_pages)
    
    # Save newly added pages to database
    db_records = []
    for p in new_pages:
        db_records.append({
            "page_id": p.page_id,
            "page_number": p.page_number,
            "source_filename": p.source_filename,
            "image_data": image_to_base64_url(p.image) if p.image else None,
            "ocr_text": p.ocr_text,
            "ocr_status": p.ocr_status,
            "is_code": p.is_code,
            "ocr_error": p.ocr_error,
            "folder": getattr(p, "folder", None) or target_folder or None
        })
    db.save_pages(nb_id, db_records, user_id=user_id)
    
    has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    return {
        "status": "success" if new_pages else ("error" if errors else "empty"),
        "uploaded_pages": len(new_pages),
        "total_pages": len(all_pages),
        "has_key": has_key,
        "errors": errors
    }


@api.post("/api/load-sample")
def load_sample_notebook(request: Request, notebook_id: str = "current_session"):
    nb_id = get_effective_notebook_id(request, notebook_id)
    p1_blocks = {
        "blocks": [
            {
                "type": "text",
                "content": "# Lecture Notes: OSI vs TCP/IP Layer Mapping\nDate: Computer Networks Week 4\n\nKey Concepts:\n1. Quicksort algorithm uses divide-and-conquer strategy.\n2. Recurrence relation: T(n) = 2T(n/2) + O(n) resolves to O(n log n).\n3. Encapsulation moves packets down from Application to Physical layer."
            },
            {
                "type": "diagram",
                "mermaid": "flowchart LR\n    subgraph OSI [OSI 7-Layer Reference Model]\n        A7[7. Application]\n        A6[6. Presentation]\n        A5[5. Session]\n        A4[4. Transport]\n        A3[3. Network]\n        A2[2. Data Link]\n        A1[1. Physical]\n    end\n    subgraph TCPIP [TCP/IP 4-Layer Suite]\n        B4[Application Layer]\n        B3[Transport Layer]\n        B2[Internet Layer]\n        B1[Network Access]\n    end\n    A7 --> B4\n    A6 --> B4\n    A5 --> B4\n    A4 --> B3\n    A3 --> B2\n    A2 --> B1\n    A1 --> B1"
            },
            {
                "type": "text",
                "content": "Note: Session layer (OSI Layer 5) establishes, manages, and terminates connections, mapping directly into the TCP/IP Application layer."
            }
        ]
    }
    p1_json = json.dumps(p1_blocks)

    p2_lines = [
        "# Code Snippet: Python Quicksort Partition",
        "```python",
        "def partition(arr, low, high):",
        "    pivot = arr[high]",
        "    i = low - 1",
        "    for j in range(low, high):",
        "        if arr[j] <= pivot:",
        "            i += 1",
        "            arr[i], arr[j] = arr[j], arr[i]",
        "    arr[i + 1], arr[high] = arr[high], arr[i + 1]",
        "    return i + 1",
        "```",
        "[CODE_DETECTED]"
    ]
    p3_lines = [
        "# Master Theorem & Asymptotic Analysis",
        "1. T(n) = aT(n/b) + f(n)",
        "2. Case 1: f(n) = O(n^(log_b(a) - epsilon))",
        "3. Case 2: f(n) = Theta(n^(log_b(a))) -> T(n) = Theta(n^(log_b(a)) log n)",
        "Sum(i=1 to n) i = n(n+1)/2"
    ]

    img1 = create_sample_page_image("OSI vs TCP/IP Diagram & Quicksort Notes", [
        "# Lecture Notes: OSI vs TCP/IP Layer Mapping",
        "[Diagram Detected: OSI 7-Layer -> TCP/IP 4-Layer Mapping]",
        "Quicksort Recurrence: T(n) = 2T(n/2) + O(n)"
    ])
    img2 = create_sample_page_image("Python Partition Code", p2_lines)
    img3 = create_sample_page_image("Master Theorem Notes", p3_lines)

    sample_pages = [
        PageRecord(
            page_id=f"{nb_id}_p1",
            page_number=1,
            source_filename="sample_osi_tcpip.png",
            image=img1,
            ocr_text=p1_json,
            ocr_status="success",
            is_code=False
        ),
        PageRecord(
            page_id=f"{nb_id}_p2",
            page_number=2,
            source_filename="sample_code.png",
            image=img2,
            ocr_text="\n".join(p2_lines),
            ocr_status="success",
            is_code=True
        ),
        PageRecord(
            page_id=f"{nb_id}_p3",
            page_number=3,
            source_filename="sample_theory.png",
            image=img3,
            ocr_text="\n".join(p3_lines),
            ocr_status="success",
            is_code=False
        ),
    ]
    registry.register(nb_id, sample_pages)
    db_records = []
    for p in sample_pages:
        db_records.append({
            "page_id": p.page_id,
            "page_number": p.page_number,
            "source_filename": p.source_filename,
            "image_data": image_to_base64_url(p.image) if p.image else None,
            "ocr_text": p.ocr_text,
            "ocr_status": p.ocr_status,
            "is_code": p.is_code,
            "ocr_error": p.ocr_error
        })
    # Determine user ID for per‑user storage (if authenticated)
    user = get_current_user_from_request(request)
    user_id = user["id"] if user else None
    db.save_pages(nb_id, db_records, user_id=user_id)
    return {"status": "success", "count": len(sample_pages)}


@api.post("/api/clear")
def clear_session(request: Request, notebook_id: str = "current_session"):
    nb_id = get_effective_notebook_id(request, notebook_id)
    registry.register(nb_id, [])
    db.clear_db_pages(nb_id)
    return {"status": "success", "total_pages": 0}


@api.get("/health", summary="System Health & Connectivity Check")
def health_check():
    db_ok = False
    try:
        with db.DBConnection() as d:
            d.execute("SELECT 1")
            db_ok = True
    except Exception:
        db_ok = False

    raw_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    key_configured = bool(raw_key and raw_key.strip() and raw_key.strip() != "your_key_here")

    return {
        "status": "healthy" if db_ok else "unhealthy",
        "database_connected": db_ok,
        "api_key_configured": key_configured,
        "disable_code_lab": DISABLE_CODE_LAB
    }


@api.on_event("startup")
def startup_checks():
    db.init_db()
    raw_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not raw_key or not raw_key.strip() or raw_key.strip() == "your_key_here":
        print("\n" + "=" * 70)
        print("[WARNING] GEMINI_API_KEY is not set or using default placeholder!")
        print("    The application will run in Demo/Simulated OCR Mode.")
        print("    To enable live AI features, add your Gemini API key to .env:")
        print("    GEMINI_API_KEY=your_actual_key_here")
        print("    Get a free API key at: https://aistudio.google.com/app/apikey")
        print("=" * 70 + "\n")
    else:
        print("\n[OK] GEMINI_API_KEY is configured.\n")


@api.post("/api/search")
def search_notebook(req: SearchRequest, request: Request = None):
    user = get_current_user_from_request(request) if request else None
    user_id = user["id"] if user else None
    nb_id = get_effective_notebook_id(request, req.notebook_id) if request else req.notebook_id
    sync_from_database(nb_id, user_id=user_id)

    # Primary: Query SQLite FTS5 index
    fts_results = db.search_pages(nb_id, req.query)
    if fts_results:
        output = []
        for r in fts_results:
            output.append({
                "page_number": r["page_number"],
                "source_filename": r["source_filename"],
                "match_count": 1,
                "snippets": [r["snippet"]]
            })
        return {"query": req.query, "total_matches": len(output), "results": output}

    # Fallback to search_indexer
    indexer = registry.get_indexer(nb_id)
    if not indexer:
        indexer = registry.get_indexer("default_academic")
    if not indexer:
        return {"query": req.query, "total_matches": 0, "results": []}

    results = indexer.search(req.query)
    output = []
    total_matches = sum(r.match_count for r in results)
    for r in results:
        output.append({
            "page_number": r.page_number,
            "source_filename": r.source_filename,
            "match_count": r.match_count,
            "snippets": r.snippets
        })
    return {"query": req.query, "total_matches": total_matches, "results": output}


# ==================== Grounded Q&A, Quiz & Sandbox Routes ====================

@api.post("/api/explain")
@api.post("/explain")
def api_explain(req: ExplainRequest, request: Request = None):
    user = get_current_user_from_request(request) if request else None
    user_id = user["id"] if user else None
    nb_id = get_effective_notebook_id(request, req.notebook_id) if request else req.notebook_id
    sync_from_database(nb_id, user_id=user_id)
    return explain_topic(query=req.query, notebook_id=nb_id)


@api.post("/api/quiz")
@api.post("/quiz")
def api_quiz(req: QuizRequest, request: Request = None):
    user = get_current_user_from_request(request) if request else None
    user_id = user["id"] if user else None
    nb_id = get_effective_notebook_id(request, req.notebook_id) if request else req.notebook_id
    sync_from_database(nb_id, user_id=user_id)
    pr = tuple(req.page_range) if req.page_range and len(req.page_range) == 2 else None
    return generate_quiz(
        notebook_id=nb_id,
        page_range=pr,
        document_filter=req.document_filter,
        folder=req.folder,
        num_questions=req.num_questions,
        difficulty=req.difficulty or "Intermediate"
    )


@api.post("/api/sandbox/run")
@api.post("/sandbox/run")
@api.post("/run-code")
def api_sandbox_run(req: RunCodeRequest):
    res = execute_python_code(code=req.code, timeout_seconds=req.timeout_seconds)
    return {
        "stdout": res.stdout,
        "stderr": res.stderr,
        "exit_code": res.exit_code,
        "duration_ms": res.duration_ms,
        "timed_out": res.timed_out,
        "error_message": res.error_message
    }


# ==================== Local Standalone Server Entry ====================
if __name__ == "__main__":
    import uvicorn
    print("\nStarting Notebook Intelligence System (NIS) v2.0...")
    print("Access the Web UI at: http://localhost:8000\n")
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:api", host=host, port=port, reload=True)
