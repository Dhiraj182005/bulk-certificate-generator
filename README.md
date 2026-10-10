# Bulk Certificate Generator

A FastAPI service that generates PDF certificates in bulk for a course or
event. You submit one request with a course, a date and up to 1000 recipients;
the service validates each recipient independently, generates one professional
landscape PDF per valid recipient in the background, and lets you poll job
progress and download the finished certificates.

## Features

- Bulk job creation (`POST /api/v1/jobs`) with per-recipient validation
- One invalid recipient never rejects the batch — it is stored as `failed`
  with a clear error message while the rest continue
- Background PDF generation (FastAPI `BackgroundTasks`) with per-recipient
  status tracking: `pending → processing → completed | failed`
- Job-level rollup status: `queued`, `processing`, `completed`,
  `partially_completed`, `failed`, plus success/failure counts and progress %
- Safe PDF download endpoint (UUID-only filenames, output-directory jail)
- Alembic-managed PostgreSQL schema; isolated in-memory SQLite test suite

## Technology stack and architecture

| Layer | Choice |
| --- | --- |
| API | FastAPI + Uvicorn (Python 3.11) |
| Database | PostgreSQL, SQLAlchemy 2.x (synchronous ORM), Alembic |
| Validation | Pydantic v2 + pydantic-settings (env-based config) |
| PDFs | ReportLab (landscape A4 canvas template) |
| Tests | Pytest + HTTPX `TestClient` against in-memory SQLite |

Architecture — small modules with one responsibility each:

```
app/
  main.py              FastAPI app factory, /api/v1 router wiring
  config.py            Settings from environment (DATABASE_URL, CERTIFICATE_OUTPUT_DIR)
  database.py          Sync engine, SessionLocal, get_db dependency, Base
  models.py            GenerationJob, Certificate (SQLAlchemy 2.x mapped columns)
  schemas.py           Pydantic request/response models (incl. MAX_RECIPIENTS_PER_BATCH)
  api/routes/          health.py, jobs.py, certificates.py
  services/
    job_service.py     Create job + recipients in one transaction (intake validation)
    job_processor.py   Background worker: own session per run, finalize counts/status
    pdf_service.py     ReportLab rendering only — no HTTP/DB knowledge
tests/                 Pytest suite (per-test in-memory SQLite + temp output dir)
alembic/               Migrations (URL injected from app settings)
generated_certificates/  Output PDFs: <job_id>/<certificate_id>.pdf (gitignored)
```

Request flow: `POST /jobs` → `job_service` persists job + certificates in one
transaction → route schedules `job_processor.process_job` as a background task
→ worker generates PDFs per pending certificate (committing after each) →
`GET /jobs/{id}` polls status → `GET /certificates/{id}/download` streams the
PDF.

## Setup

### 1. Python environment

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

### 2. PostgreSQL

Using Docker Compose (recommended for local development):

```bash
docker compose up -d          # postgres:16 on localhost:5432, db=certificates
```

Or point `DATABASE_URL` at any PostgreSQL 14+ instance you already run.

### 3. Environment variables

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | yes | SQLAlchemy URL, e.g. `postgresql+psycopg2://postgres:postgres@localhost:5432/certificates` |
| `CERTIFICATE_OUTPUT_DIR` | no | PDF output directory (default `generated_certificates/`) |

`.env` is gitignored — never commit real credentials. The committed
`docker-compose.yml` uses well-known local-dev credentials only.

## Database migrations

Tables are created exclusively by Alembic; the application never creates
schema at startup. Alembic reads the same `DATABASE_URL` as the app.

```bash
alembic upgrade head                               # apply all migrations
alembic downgrade base                             # roll everything back
alembic revision --autogenerate -m "description"   # generate a new migration
alembic current                                    # show applied revision
```

## Running the application

```bash
uvicorn app.main:app --reload
```

- Health check: `GET http://127.0.0.1:8000/api/v1/health` → `{"status":"ok"}`
- Interactive API docs: <http://127.0.0.1:8000/docs>

## Running the tests

```bash
pytest            # or: python -m pytest -v
```

Every test gets its own in-memory SQLite database and a temp PDF directory —
tests never touch PostgreSQL, the network, or the repo's output directory.

## API usage examples

### Create a bulk job

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{
        "course_name": "Python Programming",
        "event_date": "2026-10-10",
        "recipients": [
          {"name": "Aarav Patil", "email": "aarav@example.com"},
          {"name": "Priya Sharma", "email": "invalid-email"}
        ]
      }'
```

Response — `202 Accepted`:

```json
{
  "job_id": "8f164109-813a-44c5-a362-e4cdcc0e7fdf",
  "status": "queued",
  "total_count": 2,
  "status_url": "/api/v1/jobs/8f164109-813a-44c5-a362-e4cdcc0e7fdf"
}
```

Malformed top-level input (missing/empty `course_name`, bad `event_date`,
empty recipient list, or more than 1000 recipients) returns `422 Unprocessable
Entity`.

### Poll job status

`GET /api/v1/jobs/{job_id}` — `200 OK`:

```json
{
  "job_id": "8f164109-813a-44c5-a362-e4cdcc0e7fdf",
  "status": "partially_completed",
  "total_count": 2,
  "success_count": 1,
  "failure_count": 1,
  "progress_percent": 100,
  "created_at": "2026-10-10T10:51:48.389394Z",
  "updated_at": "2026-10-10T10:51:48.444160Z"
}
```

`status` is `completed` when every recipient succeeds, `partially_completed`
when successes and failures coexist, and `failed` when none succeeds.
`success_count` counts generated PDFs, `failure_count` counts permanently
failed recipients, and `progress_percent = (success + failure) / total`.
Unknown job IDs return `404`.

### List certificate results

`GET /api/v1/jobs/{job_id}/certificates?limit=50&offset=0` — `200 OK`:

```json
{
  "items": [
    {
      "certificate_id": "188fd507-ee41-4d7c-a841-0f0a1f21f4b9",
      "recipient_name": null,
      "status": "failed",
      "error_message": "email: value is not a valid email address: An email address must have an @-sign.",
      "download_url": null
    },
    {
      "certificate_id": "32caae23-6eff-49fc-9186-8e2722c309c6",
      "recipient_name": "Aarav Patil",
      "status": "completed",
      "error_message": null,
      "download_url": "/api/v1/certificates/32caae23-6eff-49fc-9186-8e2722c309c6/download"
    }
  ],
  "total": 2,
  "limit": 50,
  "offset": 0
}
```

Pagination: `limit` 1–200 (default 50), `offset` ≥ 0. `download_url` is
present only for completed certificates. Server filesystem paths are never
exposed.

### Download a generated certificate

```bash
curl -OJ http://127.0.0.1:8000/api/v1/certificates/32caae23-6eff-49fc-9186-8e2722c309c6/download
```

Returns `200` with `Content-Type: application/pdf` for completed certificates.
`404` if the certificate (or its file) is missing; `409 Conflict` if it is
still `pending`, `processing`, or `failed`. The stored path is resolved and
re-checked so it can never point outside the configured output directory.

## How errors are handled

- **Invalid recipient at intake** (bad/missing name, invalid email, non-dict
  entry): stored as a `failed` certificate with a human-readable
  `error_message`; all other recipients in the batch are unaffected.
- **Individual PDF-generation failure at runtime**: that certificate becomes
  `failed` with `error_message: "PDF generation failed: ..."`, no file is
  written (partial files are deleted), and processing continues with the
  remaining recipients. The job ends `partially_completed`.
- **Worker-level crash**: a safety net marks the job `failed` so it is never
  left stuck in `processing` without a recorded failure.
- **All-invalid batch**: the job is finalized `failed` immediately and no PDF
  generation runs.

## Background processing: why and its limitations

`BackgroundTasks` was chosen because the assignment targets a simple, explainable
initial implementation: no extra infrastructure (no Celery/Redis/broker), the
worker is a plain function (`job_processor.process_job`) with its own database
session per run, and it is trivially testable.

Limitations (be honest about these):

- **Not durable**: `BackgroundTasks` runs in-process. A crash/deploy/restart
  interrupts in-flight work; it will **not** resume. There is no crash
  recovery and no exactly-once guarantee.
- **No retries**: failed certificates are terminal; there is no automatic
  retry (which also guarantees no duplicate PDF generation from retries).
- **Single-process throughput**: work shares the API process; heavy batches
  compete with request handling.
- For production, the same `process_job(job_id)` function can be moved to
  Celery/RQ/Dramatiq with minimal change — the seam is deliberately narrow.

## Current limitations

- **Local filesystem storage**: PDFs are written to one local directory
  (`generated_certificates/`); there is no S3/object storage, so the service
  is single-node and volumes must be backed up separately.
- **No durable job recovery**: see above — interrupted jobs stay
  `processing`/`pending` until manually re-run.
- **Synchronous SQLAlchemy + in-process worker**: fine for an assignment
  load; a production system would use an external queue and likely an
  async or pooled worker design.
- **Single certificate template**: one fixed landscape design; no template
  editor or branding configuration beyond one constant.
- **No authentication/rate limiting**: endpoints are open (out of scope).

## Design decisions (interview notes)

- **Sync SQLAlchemy + `BackgroundTasks`**: prioritizes readability and a
  small dependency surface over throughput; the worker is isolated in one
  module so swapping in Celery later is a one-line change at the call site.
- **Per-recipient validation with `RecipientInput.model_validate()`**: the
  request's `recipients` field is a raw list, so one bad entry is stored as a
  failed row instead of Pydantic rejecting the entire batch with 422.
- **Commit per certificate in the worker**: a crash mid-batch keeps all
  earlier progress; each certificate transitions at most once to a terminal
  state, so re-running a worker never regenerates completed PDFs.
- **`success_count` starts at 0 and is only raised by the worker**: keeps
  `progress_percent` honest for queued jobs (an early version counted
  pending-valid recipients and reported 100% before any PDF existed).
- **UUID-only filenames + output-directory jail**: `pdf_path` is stored
  relative to the output dir and re-verified with `Path.resolve()` +
  `is_relative_to()` on download; recipient names never touch the filesystem.
- **String-backed enums (`native_enum=False`)**: the same models and tests
  run on PostgreSQL (dev) and SQLite (tests) with identical stored values.
- **Per-test in-memory SQLite + temp PDF dir**: the full suite runs in
  under a second with zero shared state and no external services.
