"""Bulk generation job endpoints."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Certificate, CertificateStatus, GenerationJob, JobStatus
from app.schemas import (
    CertificateListResponse,
    CertificateResult,
    JobCreateRequest,
    JobCreateResponse,
    JobStatusResponse,
)
from app.services import job_processor, job_service

router = APIRouter(tags=["jobs"])

# Where a client can poll for job status (GET /jobs/{job_id} below).
JOB_STATUS_URL_TEMPLATE = "/api/v1/jobs/{job_id}"
DOWNLOAD_URL_TEMPLATE = "/api/v1/certificates/{certificate_id}/download"


@router.post(
    "/jobs",
    response_model=JobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_job(
    payload: JobCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> JobCreateResponse:
    """Accept a bulk certificate request and record every recipient.

    Invalid recipients are stored as failed without rejecting the batch.
    Malformed top-level input (missing fields, empty or oversized recipient
    list) is rejected automatically by Pydantic with HTTP 422. Processing is
    scheduled as a background task only after the database commit above.
    """
    job = job_service.create_generation_job(db, payload)

    # Jobs that already failed at creation have no pending work to process.
    if job.status == JobStatus.QUEUED:
        background_tasks.add_task(job_processor.process_job, job.id)

    return JobCreateResponse(
        job_id=job.id,
        status=job.status.value,
        total_count=job.total_count,
        status_url=JOB_STATUS_URL_TEMPLATE.format(job_id=job.id),
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> JobStatusResponse:
    """Return status and progress for one generation job."""
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    processed = job.success_count + job.failure_count
    progress_percent = (
        round(processed * 100 / job.total_count) if job.total_count else 0
    )

    return JobStatusResponse(
        job_id=job.id,
        status=job.status.value,
        total_count=job.total_count,
        success_count=job.success_count,
        failure_count=job.failure_count,
        progress_percent=progress_percent,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get(
    "/jobs/{job_id}/certificates",
    response_model=CertificateListResponse,
)
def list_job_certificates(
    job_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> CertificateListResponse:
    """Return paginated certificate results for one job.

    Only download URLs are exposed - never server filesystem paths.
    """
    job_exists = db.scalar(
        select(func.count()).select_from(GenerationJob).where(GenerationJob.id == job_id)
    )
    if not job_exists:
        raise HTTPException(status_code=404, detail="Job not found")

    total = db.scalar(
        select(func.count()).select_from(Certificate).where(Certificate.job_id == job_id)
    )
    certificates = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id)
        .order_by(Certificate.created_at.asc(), Certificate.id.asc())
        .limit(limit)
        .offset(offset)
    ).all()

    items = [
        CertificateResult(
            certificate_id=cert.id,
            recipient_name=cert.recipient_name,
            status=cert.status.value,
            error_message=cert.error_message,
            download_url=(
                DOWNLOAD_URL_TEMPLATE.format(certificate_id=cert.id)
                if cert.status == CertificateStatus.COMPLETED
                else None
            ),
        )
        for cert in certificates
    ]

    return CertificateListResponse(items=items, total=total, limit=limit, offset=offset)
