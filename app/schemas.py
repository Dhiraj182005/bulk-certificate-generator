"""Pydantic schemas for request validation and response serialization.

Design note: recipients are typed as a plain list at the top level so a single
bad recipient never rejects the whole request. Each recipient dictionary is
validated independently with ``RecipientInput.model_validate()`` inside the
job-creation service.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

# Documented maximum batch size. A larger batch is rejected with HTTP 422.
MAX_RECIPIENTS_PER_BATCH = 1000


class RecipientInput(BaseModel):
    """A single recipient, validated on its own."""

    name: str = Field(min_length=1, max_length=255)
    email: EmailStr


class JobCreateRequest(BaseModel):
    """Top-level payload for creating a bulk generation job."""

    course_name: str = Field(min_length=1, max_length=255)
    event_date: date
    # Raw dictionaries on purpose: each one is validated separately so one
    # invalid recipient does not reject the entire batch (see module docstring).
    recipients: list[Any] = Field(min_length=1, max_length=MAX_RECIPIENTS_PER_BATCH)


class JobCreateResponse(BaseModel):
    """Response returned with HTTP 202 when a job is accepted."""

    job_id: UUID
    status: str
    total_count: int
    status_url: str


class JobStatusResponse(BaseModel):
    """Job progress snapshot returned by GET /jobs/{job_id}."""

    job_id: UUID
    status: str
    total_count: int
    success_count: int
    failure_count: int
    progress_percent: int
    created_at: datetime
    updated_at: datetime


class CertificateResult(BaseModel):
    """One recipient's certificate as shown in job results."""

    certificate_id: UUID
    recipient_name: str | None
    status: str
    error_message: str | None
    # Present only when the certificate is completed and downloadable.
    download_url: str | None


class CertificateListResponse(BaseModel):
    """Paginated certificate results for one job."""

    items: list[CertificateResult]
    total: int
    limit: int
    offset: int
