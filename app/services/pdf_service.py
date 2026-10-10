"""PDF certificate rendering with ReportLab.

This module only turns plain certificate data into a PDF file. It knows
nothing about HTTP routes or database models, so it stays easy to test and
reuse. Exactly one PDF is produced per call, and failures raise
``CertificateGenerationError`` instead of leaving empty or corrupt files.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app.config import get_settings

# Fixed template branding. There is intentionally only one design.
ORGANIZATION_NAME = "AERON Academy"
CERTIFICATE_TITLE = "Certificate of Completion"

NAVY = colors.HexColor("#1F3A5F")
GRAY = colors.HexColor("#555555")
LIGHT_GRAY = colors.HexColor("#999999")


class CertificateGenerationError(RuntimeError):
    """Raised when a certificate PDF cannot be generated."""


@dataclass(frozen=True)
class CertificateData:
    """Plain input needed to render one certificate."""

    certificate_id: uuid.UUID
    recipient_name: str
    course_name: str
    event_date: date


def certificate_relative_path(job_id: uuid.UUID, certificate_id: uuid.UUID) -> Path:
    """Return ``<job_id>/<certificate_id>.pdf`` relative to the output directory.

    This is what gets stored in the database, so downloads never depend on the
    server's working directory and cannot escape the output directory.
    """
    return Path(str(job_id)) / f"{certificate_id}.pdf"


def build_output_path(
    job_id: uuid.UUID,
    certificate_id: uuid.UUID,
    base_dir: Path | None = None,
) -> Path:
    """Return ``generated_certificates/<job_id>/<certificate_id>.pdf``.

    Filenames always come from UUIDs, never from user-supplied text.
    """
    base = base_dir if base_dir is not None else get_settings().certificate_output_dir
    return Path(base) / certificate_relative_path(job_id, certificate_id)


def _validate(data: CertificateData) -> None:
    """Reject blank fields before touching the filesystem."""
    if not data.recipient_name.strip():
        raise CertificateGenerationError("recipient_name must not be empty")
    if not data.course_name.strip():
        raise CertificateGenerationError("course_name must not be empty")


def _draw_certificate(data: CertificateData, output_path: Path) -> None:
    """Draw the single predefined landscape template onto a new PDF."""
    page_width, page_height = landscape(A4)
    pdf = canvas.Canvas(str(output_path), pagesize=(page_width, page_height))
    pdf.setTitle(f"Certificate {data.certificate_id}")
    pdf.setAuthor(ORGANIZATION_NAME)

    center_x = page_width / 2

    # Double border: thick outer rule with a thin inner rule.
    pdf.setStrokeColor(NAVY)
    pdf.setLineWidth(3)
    pdf.rect(15 * mm, 15 * mm, page_width - 30 * mm, page_height - 30 * mm)
    pdf.setLineWidth(1)
    pdf.rect(20 * mm, 20 * mm, page_width - 40 * mm, page_height - 40 * mm)

    # Organization / event heading.
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawCentredString(center_x, page_height - 40 * mm, ORGANIZATION_NAME)

    # Thin divider under the heading.
    pdf.setStrokeColor(LIGHT_GRAY)
    pdf.setLineWidth(0.75)
    pdf.line(
        center_x - 40 * mm,
        page_height - 46 * mm,
        center_x + 40 * mm,
        page_height - 46 * mm,
    )

    # Certificate title.
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 30)
    pdf.drawCentredString(center_x, page_height - 65 * mm, CERTIFICATE_TITLE)

    # Intro line.
    pdf.setFillColor(GRAY)
    pdf.setFont("Helvetica-Oblique", 14)
    pdf.drawCentredString(center_x, page_height - 85 * mm, "This is to certify that")

    # Recipient name.
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 36)
    pdf.drawCentredString(center_x, page_height - 105 * mm, data.recipient_name)

    # Completion line.
    pdf.setFillColor(GRAY)
    pdf.setFont("Helvetica", 14)
    pdf.drawCentredString(
        center_x,
        page_height - 120 * mm,
        "has successfully completed the course",
    )

    # Course name.
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawCentredString(center_x, page_height - 135 * mm, data.course_name)

    # Event date.
    pdf.setFillColor(GRAY)
    pdf.setFont("Helvetica", 14)
    pdf.drawCentredString(
        center_x,
        page_height - 150 * mm,
        f"on {data.event_date.strftime('%B %d, %Y')}",
    )

    # Certificate ID (UUID), kept small at the bottom.
    pdf.setFillColor(LIGHT_GRAY)
    pdf.setFont("Helvetica", 9)
    pdf.drawCentredString(center_x, 25 * mm, f"Certificate ID: {data.certificate_id}")

    pdf.showPage()
    pdf.save()


def generate_certificate(
    certificate_data: CertificateData,
    output_path: Path | str,
) -> Path:
    """Render one certificate PDF to ``output_path`` and return the path.

    Creates missing parent directories, and guarantees that no empty or
    partial file is left behind if rendering fails.
    """
    _validate(certificate_data)
    path = Path(output_path)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CertificateGenerationError(
            f"Could not create directory {path.parent}: {exc}"
        ) from exc

    try:
        _draw_certificate(certificate_data, path)
    except Exception as exc:  # ReportLab can raise several error types
        # Never leave a partial/corrupt file pretending to be a certificate.
        if path.is_file():
            path.unlink()
        raise CertificateGenerationError(
            f"Failed to render certificate {certificate_data.certificate_id}: {exc}"
        ) from exc

    if not path.is_file() or path.stat().st_size == 0:
        raise CertificateGenerationError(
            f"Certificate file was not written correctly: {path}"
        )
    return path
