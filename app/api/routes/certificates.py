"""Certificate download endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Certificate, CertificateStatus

router = APIRouter(tags=["certificates"])


@router.get("/certificates/{certificate_id}/download")
def download_certificate(
    certificate_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Return the generated PDF for one completed certificate.

    404 if the certificate (or its file) is missing; 409 if it is not
    completed yet. The stored path is re-checked so it can never point
    outside the configured generated-certificates directory.
    """
    certificate = db.get(Certificate, certificate_id)
    if certificate is None:
        raise HTTPException(status_code=404, detail="Certificate not found")

    if certificate.status != CertificateStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="Certificate is not ready for download",
        )

    if not certificate.pdf_path:
        raise HTTPException(status_code=404, detail="Certificate file not found")

    base_dir = get_settings().certificate_output_dir.resolve()
    # pdf_path is server-generated (<job_id>/<certificate_id>.pdf); resolve it
    # and verify it still lives inside the output directory.
    file_path = (base_dir / certificate.pdf_path).resolve()
    if not file_path.is_relative_to(base_dir) or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Certificate file not found")

    return FileResponse(
        file_path,
        media_type="application/pdf",
        filename=f"{certificate.id}.pdf",
    )
