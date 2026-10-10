"""Tests for the POST /api/v1/jobs endpoint."""

import uuid

import pytest
from sqlalchemy import select

from app.models import Certificate, CertificateStatus, GenerationJob, JobStatus
from app.schemas import MAX_RECIPIENTS_PER_BATCH
from app.services import job_processor

VALID_PAYLOAD = {
    "course_name": "Python Programming",
    "event_date": "2026-10-10",
    "recipients": [
        {"name": "Aarav Patil", "email": "aarav@example.com"},
        {"name": "Priya Sharma", "email": "priya@example.com"},
    ],
}


@pytest.fixture(autouse=True)
def _no_background_processing(monkeypatch):
    """These tests cover intake/validation only; keep the worker a no-op.

    Without this, POST would run the real background worker against whatever
    database DATABASE_URL points at.
    """
    monkeypatch.setattr(job_processor, "process_job", lambda job_id: None)


def test_create_job_all_valid(client, db_session):
    """A fully valid batch is accepted and every recipient starts pending."""
    response = client.post("/api/v1/jobs", json=VALID_PAYLOAD)

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert data["total_count"] == 2
    assert data["status_url"] == f"/api/v1/jobs/{data['job_id']}"

    job = db_session.get(GenerationJob, uuid.UUID(data["job_id"]))
    assert job.status == JobStatus.QUEUED
    assert job.total_count == 2
    # No PDFs generated yet, so nothing has succeeded.
    assert job.success_count == 0
    assert job.failure_count == 0

    certificates = db_session.scalars(
        select(Certificate).where(Certificate.job_id == job.id)
    ).all()
    assert len(certificates) == 2
    assert all(cert.status == CertificateStatus.PENDING for cert in certificates)
    assert {cert.recipient_email for cert in certificates} == {
        "aarav@example.com",
        "priya@example.com",
    }


def test_create_job_mixed_valid_and_invalid(client, db_session):
    """One invalid recipient does not reject the batch; it is stored as failed."""
    payload = {
        "course_name": "Python Programming",
        "event_date": "2026-10-10",
        "recipients": [
            {"name": "Aarav Patil", "email": "aarav@example.com"},
            {"name": "Priya Sharma", "email": "invalid-email"},
        ],
    }
    response = client.post("/api/v1/jobs", json=payload)

    assert response.status_code == 202
    data = response.json()
    assert data["total_count"] == 2

    job = db_session.get(GenerationJob, uuid.UUID(data["job_id"]))
    assert job.total_count == 2
    # The invalid recipient counts as failed; the valid one is still pending.
    assert job.success_count == 0
    assert job.failure_count == 1
    assert job.status == JobStatus.QUEUED

    certificates = db_session.scalars(
        select(Certificate).where(Certificate.job_id == job.id)
    ).all()
    assert len(certificates) == 2

    failed = [c for c in certificates if c.status == CertificateStatus.FAILED]
    pending = [c for c in certificates if c.status == CertificateStatus.PENDING]
    assert len(failed) == 1
    assert len(pending) == 1
    assert failed[0].recipient_email is None
    assert failed[0].recipient_name is None
    assert "email" in failed[0].error_message
    assert pending[0].recipient_email == "aarav@example.com"


def test_create_job_all_invalid(client, db_session):
    """If nothing is valid the job is already failed with all recipients failed."""
    payload = {
        "course_name": "Python Programming",
        "event_date": "2026-10-10",
        "recipients": [{"name": "", "email": "not-an-email"}],
    }
    response = client.post("/api/v1/jobs", json=payload)

    assert response.status_code == 202
    job = db_session.get(GenerationJob, uuid.UUID(response.json()["job_id"]))
    assert job.status == JobStatus.FAILED
    assert job.success_count == 0
    assert job.failure_count == 1


def test_create_job_missing_course_name_is_rejected(client):
    """Malformed top-level input returns 422 without creating a job."""
    payload = {**VALID_PAYLOAD, "course_name": ""}
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422


def test_create_job_empty_recipients_is_rejected(client):
    """An empty recipient list violates the batch constraint and returns 422."""
    payload = {**VALID_PAYLOAD, "recipients": []}
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422


def test_create_job_bad_event_date_is_rejected(client):
    """A non-ISO date is rejected with 422."""
    payload = {**VALID_PAYLOAD, "event_date": "10/10/2026"}
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422


def test_create_job_oversized_batch_is_rejected(client):
    """More than the documented maximum recipients returns 422."""
    recipients = [
        {"name": f"Recipient {i}", "email": f"user{i}@example.com"}
        for i in range(MAX_RECIPIENTS_PER_BATCH + 1)
    ]
    payload = {**VALID_PAYLOAD, "recipients": recipients}
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422


def test_non_dict_recipient_is_stored_as_failed_not_rejected(client, db_session):
    """A non-dict entry is a per-recipient failure, not a top-level 422."""
    payload = {
        "course_name": "Python Programming",
        "event_date": "2026-10-10",
        "recipients": ["not-a-dict", {"name": "Aarav Patil", "email": "aarav@example.com"}],
    }
    response = client.post("/api/v1/jobs", json=payload)

    assert response.status_code == 202
    job = db_session.get(GenerationJob, uuid.UUID(response.json()["job_id"]))
    assert job.failure_count == 1
    assert job.success_count == 0
