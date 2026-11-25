import uuid
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.api.health import router as health_router
from app.api.v1.router import router as v1_router
from app.core.config import settings
from app.core.db import dispose_engine
from app.core.logging import configure_logging

configure_logging(settings.log_level)
logger = structlog.get_logger(__name__)

@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("application_starting", environment=settings.app_env)
    yield
    await dispose_engine()
    logger.info("application_stopped")

class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path, method=request.method)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled_request_error")
            response = JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "An unexpected error occurred", "request_id": request_id}})
        response.headers["X-Request-ID"] = request_id
        return response

app = FastAPI(title=settings.app_name, version="0.1.0",
    description="Everpay API foundation. Financial routes are enabled only after approved schemas and provider contracts are integrated.",
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url="/redoc" if settings.app_env != "production" else None,
    openapi_url="/openapi.json" if settings.app_env != "production" else None, lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"], expose_headers=["X-Request-ID"])
app.include_router(health_router)
app.include_router(v1_router, prefix=settings.api_v1_prefix)

@app.get("/", tags=["root"])
async def root():
    return {"service": "Everpay API", "version": "0.1.0", "docs": "/docs" if settings.app_env != "production" else None}
