"""FastAPI application entry point.

Run locally with:
    uvicorn app.main:app --reload
"""

from fastapi import FastAPI

from app.api.routes import certificates, health, jobs


def create_app() -> FastAPI:
    """Application factory. Keeps setup testable and avoids import side effects."""
    application = FastAPI(
        title="Bulk Certificate Generator",
        version="0.1.0",
        description="Generate and track bulk PDF certificates for recipients.",
    )
    application.include_router(health.router, prefix="/api/v1")
    application.include_router(jobs.router, prefix="/api/v1")
    application.include_router(certificates.router, prefix="/api/v1")
    return application


app = create_app()
