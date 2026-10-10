"""Unit tests for the ReportLab PDF certificate service."""

import uuid
from datetime import date

import pytest
from pypdf import PdfReader

from app.services.pdf_service import (
    CertificateData,
    CertificateGenerationError,
    build_output_path,
    generate_certificate,
)


@pytest.fixture()
def sample_data() -> CertificateData:
    return CertificateData(
        certificate_id=uuid.uuid4(),
        recipient_name="Aarav Patil",
        course_name="Python Programming",
        event_date=date(2026, 10, 10),
    )


def test_generate_certificate_creates_non_empty_file(sample_data, tmp_path):
    """A successful call writes exactly one non-empty PDF at the target path."""
    output_path = tmp_path / "cert.pdf"

    result = generate_certificate(sample_data, output_path)

    assert result == output_path
    assert output_path.is_file()
    assert output_path.stat().st_size > 0
    # Exactly one file is produced, nothing extra beside it.
    assert list(tmp_path.iterdir()) == [output_path]


def test_generated_pdf_is_valid_and_landscape(sample_data, tmp_path):
    """The file is a readable single-page landscape PDF."""
    output_path = tmp_path / "cert.pdf"
    generate_certificate(sample_data, output_path)

    reader = PdfReader(str(output_path))
    assert len(reader.pages) == 1

    media_box = reader.pages[0].mediabox
    assert float(media_box.width) > float(media_box.height)


def test_pdf_contains_recipient_specific_content(sample_data, tmp_path):
    """Recipient name, course, date and certificate ID appear in the text."""
    output_path = tmp_path / "cert.pdf"
    generate_certificate(sample_data, output_path)

    reader = PdfReader(str(output_path))
    text = reader.pages[0].extract_text()

    assert sample_data.recipient_name in text
    assert sample_data.course_name in text
    assert str(sample_data.certificate_id) in text
    assert "October 10, 2026" in text


def test_generate_certificate_creates_missing_directories(sample_data, tmp_path):
    """Missing parent directories are created automatically."""
    output_path = tmp_path / "job-dir" / "cert.pdf"

    generate_certificate(sample_data, output_path)

    assert output_path.is_file()


def test_build_output_path_uses_uuid_filenames(tmp_path):
    """Paths follow generated_certificates/<job_id>/<certificate_id>.pdf."""
    job_id = uuid.uuid4()
    certificate_id = uuid.uuid4()

    path = build_output_path(job_id, certificate_id, base_dir=tmp_path)

    assert path == tmp_path / str(job_id) / f"{certificate_id}.pdf"


def test_blank_recipient_name_raises_and_writes_nothing(tmp_path):
    """Blank input is rejected before any file is created."""
    data = CertificateData(
        certificate_id=uuid.uuid4(),
        recipient_name="   ",
        course_name="Python Programming",
        event_date=date(2026, 10, 10),
    )
    output_path = tmp_path / "cert.pdf"

    with pytest.raises(CertificateGenerationError):
        generate_certificate(data, output_path)

    assert not output_path.exists()


def test_unwritable_output_path_raises_meaningful_error(sample_data, tmp_path):
    """Rendering onto a directory raises instead of silently corrupting."""
    output_path = tmp_path / "as-directory"
    output_path.mkdir()

    with pytest.raises(CertificateGenerationError):
        generate_certificate(sample_data, output_path)
