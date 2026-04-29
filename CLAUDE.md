# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Install: `pip install -r requirements.txt` (Python 3.11)
- Run dev server: `uvicorn app.main:app --reload` (binds to `0.0.0.0:8000`; OpenAPI at `/docs`)
- Run all tests: `pytest tests/ -v`
- Run a single test: `pytest tests/test_jobs.py::TestGetJobStatus::test_job_not_found_returns_404 -v`
- Docker build/run: `docker build -t mustache-api . && docker run -p 8000:8000 --env-file .env mustache-api`
- Env setup: copy `.env.example` to `.env`. Required keys: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY`, `GEMINI_API_KEY`.
- Deploy target: Railway (see `railway.toml`); health check at `/health`.

## Two parallel API surfaces — pick the right one

This repo contains **two independent FastAPI apps** that share dependencies but serve different purposes. Confirm which one a task targets before editing:

1. **`app/main.py` (canonical)** — full modular app with Supabase Auth, async jobs, storage persistence, and DB records. Mounted under `/api/*` (auth, jobs, styles). This is what `Dockerfile` and `railway.toml` run.
2. **`api.py` (standalone, root-level)** — single-file synchronous endpoint `POST /api/generate` that returns a JPEG response directly. Has its own `Settings`, its own Gemini client, and its own mustache prompt dictionary. Used by `test_api.py` for manual smoke tests against `http://localhost:8000/api/generate`.

The two have **divergent mustache style IDs and prompts**. `app/services/ai_pipeline.py` defines `chevron, handlebar, fu_manchu, pencil, walrus, english`. `api.py` defines `chevron, pencil, light_natural, walrus, horseshoe, imperial, anchor, k_style`. They are not in sync — when adding/changing a style, update whichever surface is in scope and check whether the other should follow.

## Architecture (the canonical app)

### Request flow for a job

`POST /api/jobs` (router `app/routers/jobs.py`) →
1. JWT verified via `app/dependencies/auth.py` (calls Supabase `auth.get_user(token)` with the anon key).
2. `validate_image` enforces JPEG/PNG, ≤5 MB, ≤4000×4000 (`app/utils/validators.py`).
3. `ImageService.create_job_records` (`app/services/image_service.py`) uploads the original to the `uploads` bucket at `{user_id}/{uuid}.{ext}`, inserts an `original_images` row, and inserts an `ai_generated_images` row with `status='pending'`.
4. A FastAPI `BackgroundTasks` entry runs `_process_job`, which calls Gemini via `overlay_mustache` (`app/services/ai_pipeline.py`), then `ImageService.save_ai_result` uploads the JPEG to the `results` bucket and flips the AI row to `status='done'`.
5. The router returns immediately with HTTP 202 + `job_id`.

### Job state — in-memory, ephemeral

`app/routers/jobs.py` keeps a module-level `_jobs_db: dict[str, dict]`. **Job state does not survive a restart**, and is not shared across workers/replicas. The Supabase `ai_generated_images` row is the persistent record; the in-memory dict is what `GET /api/jobs/{id}` and `GET /api/jobs` actually read. If you need durability or multi-worker correctness, this is the place — don't assume it's already backed by a DB.

### Output URL convention

`GET /api/jobs/{id}` returns `output_url` as a **base64 `data:image/jpeg;base64,…` URI**, not a Supabase signed URL. This is intentional so the mobile client can render without a second round trip. The actual signed Storage URL is computed in `save_ai_result` and persisted on the `ai_generated_images` row, but is not what the polling endpoint returns.

### Two Supabase clients, two keys

- **Anon key** (`auth_service.py`, `dependencies/auth.py`): all `auth.*` operations and JWT verification. Goes through Supabase RLS.
- **Service-role key** (`image_service.py`, `services/storage.py`): Storage uploads and direct table writes (`original_images`, `ai_generated_images`). Bypasses RLS. Server-side only — never expose.

### Stale code surfaces — verify before reusing

- `app/services/jobs_service.py` (`JobsService`) operates on `jobs` and `styles` tables. The current jobs router does **not** call it — only `app/routers/styles.py` uses it (for `get_styles`). The `jobs` table CRUD methods appear unused; the real persistence path goes through `ImageService` against `ai_generated_images`. Don't wire new code into `JobsService.create_job` etc. without checking whether the `jobs` table still exists in the Supabase schema.
- `tests/test_jobs.py` patches `app.routers.jobs.JobsService` and mocks `get_job`, but the real router reads from the in-memory `_jobs_db` and never instantiates `JobsService`. The tests are stale relative to the current router; expect failures and treat them as documentation of intent rather than ground truth.

### Rate limiting

`slowapi` with `get_remote_address` keying. Default is `5/minute` (configurable via `RATE_LIMIT`). Limiter is registered on `app.state.limiter` in `main.py` and re-instantiated per router (e.g. `jobs.py`) so decorators on routes work.

### Styles caching

`app/routers/styles.py` caches the styles list in a module-level tuple for 5 minutes. If Supabase is unreachable it falls back to a hardcoded mock list — fine for local dev, but means a misconfigured DB connection silently returns stub data instead of erroring.

## Conventions

- All endpoints under `/api/*` are mounted by `app/main.py`. New routers should be registered there with the `/api` prefix.
- `get_settings()` in `app/config.py` is `lru_cache`-d. Always call it via the function; do not instantiate `Settings()` directly.
- Models live in `app/models/{auth,job,style}.py` (Pydantic). Routers should import response models from there rather than defining inline schemas.
- Image bytes flow through the system as raw `bytes`. Resize only happens inside `ai_pipeline.generate_mustache` (max 1024px on the longest side before sending to Gemini).
