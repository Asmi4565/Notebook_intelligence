# Deploying Notebook Intelligence System (NIS) v2.0 to Vercel

This guide outlines the deployment steps, environment variables, database configuration, and security settings for hosting NIS v2.0 on Vercel.

---

## 1. Environment Variables Configuration

Set the following environment variables in your **Vercel Project Settings** -> **Environment Variables**:

| Variable Name | Required | Description | Example / Recommended Value |
|---|---|---|---|
| `GEMINI_API_KEY` | **Yes** | Google Gemini API key for OCR, transcription, and quiz generation. | `AIzaSy...` |
| `SECRET_KEY` | **Yes** | Random secret key for signing session JWT tokens. | `a_long_random_secret_string` |
| `DATABASE_URL` | **Yes** (Prod) | PostgreSQL Pooler connection string (IPv4 compatible). | `postgresql://postgres.[ref]:[pass]@aws-0-[region].pooler.supabase.com:6543/postgres` |
| `DISABLE_CODE_LAB` | **Yes** | Disable the Python code execution sandbox for cloud security. | `true` |
| `ENV` | **Yes** | Application environment mode (enforces secure HTTPS session cookies). | `production` |

> [!IMPORTANT]
> **Supabase Connection Pooler Requirement:**
> Supabase direct connections (`db.[ref].supabase.co:5432`) are **IPv6-only**. Because Vercel Serverless operates on an IPv4 network, you **must use the Supabase Connection Pooler URL** (Port `6543` or `5432` on host `pooler.supabase.com` or `aws-0-[region].pooler.supabase.com`).
> 
> **How to get the Pooler URL in Supabase:**
> 1. Go to your **Supabase Dashboard** -> Select your Project.
> 2. Navigate to **Project Settings** -> **Database**.
> 3. Scroll down to **Connection string** and select **Transaction Pooler** (or **Session Pooler**).
> 4. Copy the URI string (it looks like `postgresql://postgres.[ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres`).
> 5. Update `DATABASE_URL` in Vercel with this pooler URL.

---

## 2. Deployment Instructions

### Deploying via Vercel CLI
```bash
npx vercel --prod --yes
```

### Manual Redeploy via GitHub / Vercel Web
1. Push your repository changes to GitHub.
2. Connect your repository to Vercel.
3. Add the 5 Environment Variables.
4. Trigger a deployment.

---

## 3. Serverless Architecture & Features

- **Entrypoint:** `api/index.py` exposes FastAPI `app`.
- **Database:** Automatic schema setup on cold start. Uses PostgreSQL (`DATABASE_URL`) in production and falls back to `/tmp/notebooks.db` SQLite if offline/local.
- **Payload Limits:** Client-side image auto-resizing (max 1600px width, JPEG compression) and chunked uploads enforce payloads strictly below Vercel's 4.5 MB request limit.
- **Code Lab Guard:** Setting `DISABLE_CODE_LAB=true` blocks `/run-code` requests with `403 Forbidden` and hides Code Lab from the frontend UI.
- **Rate Limits:** Enforces max 50 OCR uploads/day and max 50 AI generation calls/day per user.
- **Sanitized Health Check:** `/health` returns status flags only without exposing internal credentials or paths.

---

## 4. Verification Checklist

- [x] Web UI loads landing page at `/`
- [x] User authentication (Sign up / Login) works via PostgreSQL / SQLite
- [x] OCR image upload resizes client-side and processes successfully
- [x] Full-text search operates via Postgres `ILIKE` / SQLite `FTS5`
- [x] Code Lab returns 403 Forbidden when disabled
- [x] `/health` endpoint returns `{ "status": "ok", "database_connected": true, "api_key_configured": true, "disable_code_lab": true }`
