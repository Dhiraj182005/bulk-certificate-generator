"""Business logic for creating bulk generation jobs."""

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models import Certificate, CertificateStatus, GenerationJob, JobStatus
from app.schemas import JobCreateRequest, RecipientInput


def _format_validation_error(error: ValidationError) -> str:
    """Turn a Pydantic error into a short, human-readable message."""
    parts: list[str] = []
    for item in error.errors():
        field = ".".join(str(loc) for loc in item["loc"]) or "value"
        parts.append(f"{field}: {item['msg']}")
    return "; ".join(parts)


def _build_certificate(raw_recipient: object) -> Certificate:
    """Validate one recipient dictionary and build its Certificate row.

    Valid recipients start as ``pending``. Invalid ones are stored as ``failed``
    with a clear error message so the rest of the batch is unaffected.
    """
    try:
        recipient = RecipientInput.model_validate(raw_recipient)
    except ValidationError as exc:
        return Certificate(
            status=CertificateStatus.FAILED,
            error_message=_format_validation_error(exc),
        )
    return Certificate(
        recipient_name=recipient.name,
        recipient_email=recipient.email,
        status=CertificateStatus.PENDING,
    )


def create_generation_job(db: Session, payload: JobCreateRequest) -> GenerationJob:
    """Persist a job and all its recipient rows in a single transaction.

    ``success_count`` counts certificates that have completed (PDF generated);
    it starts at 0 because none exist yet and is updated by the background
    processor. ``failure_count`` counts recipients that are already in a
    terminal failed state (invalid input). This keeps ``progress_percent``
    honest for queued jobs.
    """
    certificates = [_build_certificate(raw) for raw in payload.recipients]

    failure_count = sum(
        1 for cert in certificates if cert.status == CertificateStatus.FAILED
    )
    valid_count = len(certificates) - failure_count

    job = GenerationJob(
        course_name=payload.course_name,
        event_date=payload.event_date,
        # If nothing is valid there is nothing left to process, so the job is
        # already failed; otherwise it is queued for PDF processing.
        status=JobStatus.QUEUED if valid_count > 0 else JobStatus.FAILED,
        total_count=len(payload.recipients),
        success_count=0,
        failure_count=failure_count,
        certificates=certificates,
    )

    db.add(job)
    db.commit()
    db.refresh(job)
    return job
