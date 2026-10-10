"""Background processing of generation jobs.

Scheduled with FastAPI ``BackgroundTasks`` after a job is committed. Each run
opens its own database session: a session is never shared across a
background-task boundary.

Important limitation: BackgroundTasks is not a durable job queue. If the
process crashes, in-flight work is interrupted and will not resume. There is
no crash recovery and no exactly-once guarantee.
"""

import logging
import uuid

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Certificate, CertificateStatus, GenerationJob, JobStatus
from app.services.pdf_service import (
    CertificateData,
    build_output_path,
    certificate_relative_path,
    generate_certificate,
)

logger = logging.getLogger(__name__)


def process_job(job_id: uuid.UUID) -> None:
    """Entry point scheduled as a background task for one generation job."""
    try:
        db = SessionLocal()
        try:
            _run(db, job_id)
        finally:
            db.close()
    except Exception:
        # Safety net: a worker-level failure must never leave the job stuck in
        # "processing" without a recorded failure.
        logger.exception("Unhandled error while processing job %s", job_id)
        _finalize_job_as_failed(job_id)


def _run(db: Session, job_id: uuid.UUID) -> None:
    """Generate PDFs for every pending certificate in the job."""
    job = db.get(GenerationJob, job_id)
    if job is None:
        logger.warning("Job %s not found; nothing to process", job_id)
        return

    job.status = JobStatus.PROCESSING
    db.commit()

    # Snapshot the pending list up front; statuses change as we iterate.
    pending = [cert for cert in job.certificates if cert.status == CertificateStatus.PENDING]
    for certificate in pending:
        _process_certificate(db, job, certificate)

    _finalize_job(db, job)
    db.commit()


def _process_certificate(db: Session, job: GenerationJob, certificate: Certificate) -> None:
    """Generate one PDF and record the outcome, never raising to the caller."""
    certificate.status = CertificateStatus.PROCESSING
    db.commit()

    try:
        data = CertificateData(
            certificate_id=certificate.id,
            recipient_name=certificate.recipient_name or "",
            course_name=job.course_name,
            event_date=job.event_date,
        )
        output_path = build_output_path(certificate.job_id, certificate.id)
        generate_certificate(data, output_path)
    except Exception as exc:
        # One bad certificate must not stop the rest of the batch.
        certificate.status = CertificateStatus.FAILED
        certificate.error_message = f"PDF generation failed: {exc}"
    else:
        certificate.status = CertificateStatus.COMPLETED
        # Store the path relative to the output directory for safe downloads.
        certificate.pdf_path = str(
            certificate_relative_path(certificate.job_id, certificate.id)
        )

    # Commit per certificate so a crash mid-job keeps earlier progress.
    db.commit()


def _finalize_job(db: Session, job: GenerationJob) -> None:
    """Recompute counts and set the final job status from certificate states."""
    statuses = [cert.status for cert in job.certificates]
    succeeded = sum(1 for status in statuses if status == CertificateStatus.COMPLETED)
    failed = sum(1 for status in statuses if status == CertificateStatus.FAILED)

    job.total_count = len(statuses)
    job.success_count = succeeded
    job.failure_count = failed

    if succeeded and not failed:
        job.status = JobStatus.COMPLETED
    elif succeeded and failed:
        job.status = JobStatus.PARTIALLY_COMPLETED
    else:
        # Includes the all-invalid case: nothing was generated successfully.
        job.status = JobStatus.FAILED


def _finalize_job_as_failed(job_id: uuid.UUID) -> None:
    """Best-effort attempt to mark a job failed using a fresh session."""
    try:
        db = SessionLocal()
        try:
            job = db.get(GenerationJob, job_id)
            if job is not None:
                job.status = JobStatus.FAILED
                db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Could not mark job %s as failed", job_id)
