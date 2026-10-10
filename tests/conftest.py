"""Shared pytest fixtures.

A fresh in-memory SQLite database is created for every test so tests never
touch the development PostgreSQL instance. The app's ``get_db`` dependency is
overridden to use that test engine, and tests may open their own session on
the same engine to inspect persisted rows.
"""

import os

# Set a harmless DATABASE_URL before importing the app so building the
# (unused) module-level engine never requires a live database.
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import Settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def db_engine():
    """One in-memory SQLite engine per test, shared through a single connection."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """A session for tests to inspect or seed data directly."""
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def client(db_engine):
    """TestClient whose requests use the same test database as ``db_session``."""
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def background_env(db_engine, tmp_path, monkeypatch):
    """Point background processing and PDF output at the test database/temp dir.

    Returns the directory where test certificates are written.
    """
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("app.services.job_processor.SessionLocal", factory)

    output_dir = tmp_path / "generated_certificates"
    test_settings = Settings(database_url="sqlite://", certificate_output_dir=output_dir)
    monkeypatch.setattr("app.services.pdf_service.get_settings", lambda: test_settings)
    monkeypatch.setattr("app.api.routes.certificates.get_settings", lambda: test_settings)
    return output_dir
