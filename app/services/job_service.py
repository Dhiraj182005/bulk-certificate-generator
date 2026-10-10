"""Business logic for creating bulk generation jobs."""

import json

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models import Certificate, CertificateStatus, GenerationJob, JobStatus
from app.schemas import JobCreateRequest, RecipientInput

RECIPIENT_STRUCTURE_ERROR = (
    "recipient must be a JSON object with 'name' and 'email' fields"
)


def _format_validation_error(error: ValidationError) -> str:
    """Turn a Pydantic error into a short, human-readable message."""
    parts: list[str] = []
    for item in error.errors():
        field = ".".join(str(loc) for loc in item["loc"]) or "value"
        parts.append(f"{field}: {item['msg']}")
    return "; ".join(parts)


def _decode_recipient(raw_recipient: object) -> object:
    """Return the recipient as a dict-ready value.

    Some clients (especially shell quoting on Windows) send each recipient as
    a double-encoded JSON string instead of an object; decode it so validation
    sees the dictionary. Anything else is passed through unchanged.
    """
    if isinstance(raw_recipient, str):
        try:
            return json.loads(raw_recipient)
        except json.JSONDecodeError:
            return None  # not JSON at all -> structure error below
    return raw_recipient


def _build_certificate(raw_recipient: object) -> Certificate:
    """Validate one recipient entry and build its Certificate row.

    Valid recipients start as ``pending``. Invalid ones are stored as ``failed``
    with a clear error message so the rest of the batch is unaffected.
    """
    data = _decode_recipient(raw_recipient)
    if not isinstance(data, dict):
        return Certificate(
            status=CertificateStatus.FAILED,
            error_message=RECIPIENT_STRUCTURE_ERROR,
        )

    try:
        recipient = RecipientInput.model_validate(data)
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
