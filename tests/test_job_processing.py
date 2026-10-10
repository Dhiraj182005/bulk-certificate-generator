"""End-to-end tests for background processing and the read/download endpoints.

The ``background_env`` fixture points the background worker's own session and
the PDF output directory at the test database and a temp dir. Starlette's
TestClient runs FastAPI BackgroundTasks synchronously as part of the request,
so after ``client.post(...)`` the job is already fully processed.
"""

import io
import uuid
from datetime import date

from pypdf import PdfReader

from app.models import CertificateStatus, GenerationJob, JobStatus
from app.schemas import JobCreateRequest
from app.services import job_processor, job_service
from app.services.pdf_service import CertificateGenerationError


def _create_job_via_api(client, recipients):
    payload = {
        "course_name": "Python Programming",
        "event_date": "2026-10-10",
        "recipients": recipients,
    }
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 202
    return response.json()


def test_post_processes_valid_job_to_completion(client, background_env):
    """A fully valid job is processed to completed with PDFs on disk."""
    data = _create_job_via_api(
        client,
        [
            {"name": "Aarav Patil", "email": "aarav@example.com"},
            {"name": "Priya Sharma", "email": "priya@example.com"},
        ],
    )

    status_response = client.get(f"/api/v1/jobs/{data['job_id']}")
    assert status_response.status_code == 200
    status_data = status_response.json()
    assert status_data["status"] == "completed"
    assert status_data["total_count"] == 2
    assert status_data["success_count"] == 2
    assert status_data["failure_count"] == 0
    assert status_data["progress_percent"] == 100
    assert "created_at" in status_data
    assert "updated_at" in status_data

    # Exactly one PDF per valid recipient.
    pdf_files = list(background_env.rglob("*.pdf"))
    assert len(pdf_files) == 2


def test_post_mixed_job_is_partially_completed(client, background_env):
    """Valid recipients get PDFs; invalid ones stay failed with an error."""
    data = _create_job_via_api(
        client,
        [
            {"name": "Aarav Patil", "email": "aarav@example.com"},
            {"name": "Priya Sharma", "email": "invalid-email"},
        ],
    )

    status_data = client.get(f"/api/v1/jobs/{data['job_id']}").json()
    assert status_data["status"] == "partially_completed"
    assert status_data["success_count"] == 1
    assert status_data["failure_count"] == 1
    assert status_data["progress_percent"] == 100

    certs = client.get(f"/api/v1/jobs/{data['job_id']}/certificates").json()
    assert certs["total"] == 2
    completed = [c for c in certs["items"] if c["status"] == "completed"]
    failed = [c for c in certs["items"] if c["status"] == "failed"]
    assert len(completed) == 1
    assert len(failed) == 1
    assert completed[0]["download_url"] is not None
    assert failed[0]["download_url"] is None
    assert "email" in failed[0]["error_message"]
    # Server filesystem paths are never exposed.
    assert all("generated_certificates" not in str(item) for item in certs["items"])

    assert len(list(background_env.rglob("*.pdf"))) == 1


def test_post_all_invalid_job_fails_without_generating_pdfs(client, background_env):
    """All-invalid jobs finalize as failed and never run PDF generation."""
    data = _create_job_via_api(client, [{"name": "", "email": "not-an-email"}])

    status_data = client.get(f"/api/v1/jobs/{data['job_id']}").json()
    assert status_data["status"] == "failed"
    assert status_data["success_count"] == 0
    assert status_data["failure_count"] == 1

    assert not background_env.exists() or not list(background_env.rglob("*.pdf"))


def test_get_job_missing_returns_404(client):
    response = client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert response.status_code == 404


def test_list_certificates_is_paginated(client, background_env):
    """limit/offset paginate results while total reports the full count."""
    data = _create_job_via_api(
        client,
        [
            {"name": f"Recipient {i}", "email": f"user{i}@example.com"}
            for i in range(3)
        ],
    )
    job_id = data["job_id"]

    page1 = client.get(f"/api/v1/jobs/{job_id}/certificates?limit=2&offset=0").json()
    assert page1["total"] == 3
    assert page1["limit"] == 2
    assert page1["offset"] == 0
    assert len(page1["items"]) == 2

    page2 = client.get(f"/api/v1/jobs/{job_id}/certificates?limit=2&offset=2").json()
    assert len(page2["items"]) == 1

    ids = {item["certificate_id"] for item in page1["items"]}
    ids |= {item["certificate_id"] for item in page2["items"]}
    assert len(ids) == 3


def test_list_certificates_missing_job_returns_404(client):
    response = client.get(f"/api/v1/jobs/{uuid.uuid4()}/certificates")
    assert response.status_code == 404


def test_download_completed_certificate(client, background_env):
    """Downloading a completed certificate returns a real PDF file."""
    data = _create_job_via_api(client, [{"name": "Aarav Patil", "email": "aarav@example.com"}])
    certs = client.get(f"/api/v1/jobs/{data['job_id']}/certificates").json()
    download_url = certs["items"][0]["download_url"]
    assert download_url is not None

    response = client.get(download_url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")

    # The downloaded document actually contains this recipient's details.
    reader = PdfReader(io.BytesIO(response.content))
    text = reader.pages[0].extract_text()
    assert "Aarav Patil" in text
    assert "Python Programming" in text


def test_download_missing_certificate_returns_404(client):
    response = client.get(f"/api/v1/certificates/{uuid.uuid4()}/download")
    assert response.status_code == 404


def test_download_pending_certificate_returns_409(client, db_session):
    """A certificate that has not completed yet is a 409 conflict."""
    job = job_service.create_generation_job(
        db_session,
        JobCreateRequest(
            course_name="Python Programming",
            event_date=date(2026, 10, 10),
            recipients=[{"name": "Aarav Patil", "email": "aarav@example.com"}],
        ),
    )
    certificate = job.certificates[0]
    assert certificate.status == CertificateStatus.PENDING

    response = client.get(f"/api/v1/certificates/{certificate.id}/download")
    assert response.status_code == 409


def test_download_rejects_paths_outside_output_dir(client, db_session, background_env):
    """A tampered pdf_path that escapes the output directory is rejected."""
    job = job_service.create_generation_job(
        db_session,
        JobCreateRequest(
            course_name="Python Programming",
            event_date=date(2026, 10, 10),
            recipients=[{"name": "Aarav Patil", "email": "aarav@example.com"}],
        ),
    )
    certificate = job.certificates[0]
    certificate.status = CertificateStatus.COMPLETED
    certificate.pdf_path = "../../outside.pdf"
    db_session.commit()

    response = client.get(f"/api/v1/certificates/{certificate.id}/download")
    assert response.status_code == 404


def test_worker_level_failure_marks_job_failed(db_session, background_env, monkeypatch):
    """An unexpected worker error never leaves the job stuck in processing."""
    job = job_service.create_generation_job(
        db_session,
        JobCreateRequest(
            course_name="Python Programming",
            event_date=date(2026, 10, 10),
            recipients=[{"name": "Aarav Patil", "email": "aarav@example.com"}],
        ),
    )

    def _boom(session, job_id):
        raise RuntimeError("simulated worker crash")

    monkeypatch.setattr(job_processor, "_run", _boom)
    job_processor.process_job(job.id)

    db_session.expire_all()
    reloaded = db_session.get(GenerationJob, job.id)
    assert reloaded.status == JobStatus.FAILED


def test_one_certificate_failure_does_not_stop_others(client, background_env, monkeypatch):
    """A deterministic PDF failure for one recipient skips only that one.

    The middle recipient's generation is mocked to fail; the recipients
    before and after it must still complete.
    """
    real_generate = job_processor.generate_certificate

    def flaky_generate(data, output_path):
        if data.recipient_name == "Bob Broken":
            raise CertificateGenerationError("simulated generation failure")
        return real_generate(data, output_path)

    monkeypatch.setattr(job_processor, "generate_certificate", flaky_generate)

    data = _create_job_via_api(
        client,
        [
            {"name": "Alice First", "email": "alice@example.com"},
            {"name": "Bob Broken", "email": "bob@example.com"},
            {"name": "Carol Last", "email": "carol@example.com"},
        ],
    )

    status_data = client.get(f"/api/v1/jobs/{data['job_id']}").json()
    assert status_data["status"] == "partially_completed"
    assert status_data["success_count"] == 2
    assert status_data["failure_count"] == 1
    assert status_data["progress_percent"] == 100

    certs = client.get(f"/api/v1/jobs/{data['job_id']}/certificates").json()
    by_name = {item["recipient_name"]: item for item in certs["items"]}
    assert by_name["Alice First"]["status"] == "completed"
    assert by_name["Alice First"]["download_url"] is not None
    # The failure is recorded with a useful message and no download link.
    assert by_name["Bob Broken"]["status"] == "failed"
    assert "simulated generation failure" in by_name["Bob Broken"]["error_message"]
    assert by_name["Bob Broken"]["download_url"] is None
    # Recipients after the failure still succeed.
    assert by_name["Carol Last"]["status"] == "completed"
    assert by_name["Carol Last"]["download_url"] is not None

    # Exactly two PDFs were written (the failed one produced nothing).
    assert len(list(background_env.rglob("*.pdf"))) == 2


def test_download_failed_certificate_returns_409(client, db_session):
    """A failed certificate cannot be downloaded as a successful PDF."""
    job = job_service.create_generation_job(
        db_session,
        JobCreateRequest(
            course_name="Python Programming",
            event_date=date(2026, 10, 10),
            recipients=[{"name": "", "email": "not-an-email"}],
        ),
    )
    certificate = job.certificates[0]
    assert certificate.status == CertificateStatus.FAILED

    response = client.get(f"/api/v1/certificates/{certificate.id}/download")
    assert response.status_code == 409


def test_queued_job_reports_zero_progress(client, db_session):
    """A fresh all-valid job has not succeeded anything: 0% progress."""
    job = job_service.create_generation_job(
        db_session,
        JobCreateRequest(
            course_name="Python Programming",
            event_date=date(2026, 10, 10),
            recipients=[
                {"name": "Aarav Patil", "email": "aarav@example.com"},
                {"name": "Priya Sharma", "email": "priya@example.com"},
            ],
        ),
    )

    status_data = client.get(f"/api/v1/jobs/{job.id}").json()
    assert status_data["status"] == "queued"
    assert status_data["success_count"] == 0
    assert status_data["failure_count"] == 0
    assert status_data["progress_percent"] == 0


def test_mixed_job_intake_counts_invalid_as_progress(client, db_session):
    """At intake only the already-failed invalid recipient counts as processed."""
    job = job_service.create_generation_job(
        db_session,
        JobCreateRequest(
            course_name="Python Programming",
            event_date=date(2026, 10, 10),
            recipients=[
                {"name": "Aarav Patil", "email": "aarav@example.com"},
                {"name": "Priya Sharma", "email": "invalid-email"},
            ],
        ),
    )

    status_data = client.get(f"/api/v1/jobs/{job.id}").json()
    assert status_data["status"] == "queued"
    assert status_data["success_count"] == 0
    assert status_data["failure_count"] == 1
    assert status_data["progress_percent"] == 50
