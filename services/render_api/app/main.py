import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import settings
from app.jobs import Scheduler, initialize


@asynccontextmanager
async def lifespan(app):
    # No migration or collection is performed on import. Startup requires a migrated DB.
    initialize()
    scheduler = Scheduler() if settings().scheduler_enabled else None
    if scheduler:
        scheduler.start()
    yield
    if scheduler:
        scheduler.close()


app = FastAPI(
    title="LeadGen Phase 1", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
)
app.include_router(router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(Exception)
async def sanitized_error(request: Request, exc: Exception):
    logging.getLogger("leadgen.api").error("request_failed code=%s", type(exc).__name__)
    return JSONResponse(
        status_code=503, content={"detail": "Service unavailable; inspect sanitized operational status"}
    )
