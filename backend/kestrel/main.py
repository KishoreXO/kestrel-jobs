from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from kestrel import __version__, scheduler
from kestrel.api.routes import router
from kestrel.config import get_settings
from kestrel.db import get_engine

# The API only serves this machine ("backend" is the service name inside docker compose).
LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]", "::1", "backend"]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_engine()
    scheduler.apply_schedule()
    yield
    scheduler.shutdown()


def _allowed_hosts(extra: str) -> list[str]:
    return LOCAL_HOSTS + [h.strip().lower() for h in extra.split(",") if h.strip()]


def create_app() -> FastAPI:
    settings = get_settings()
    docs = settings.kestrel_api_docs
    app = FastAPI(
        title="Kestrel",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    hosts = _allowed_hosts(settings.allowed_hosts)
    trusted_origins = {(urlsplit(settings.frontend_origin).hostname or "").lower(), *hosts}

    @app.middleware("http")
    async def reject_cross_site_writes(request: Request, call_next):
        """Another website open in your browser must not be able to change settings or start scans."""
        origin = request.headers.get("origin")
        if origin and request.method not in SAFE_METHODS:
            host = (urlsplit(origin).hostname or "").lower()
            if host not in trusted_origins and f"[{host}]" not in trusted_origins:
                return PlainTextResponse("Cross-site requests are not allowed.", status_code=403)
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Blocks DNS-rebinding: requests must be addressed to this machine, not to another domain name.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.include_router(router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    # Binds to localhost by default: your profile and keys are only reachable from this machine.
    uvicorn.run("kestrel.main:app", host=settings.host, port=settings.port, server_header=False)


if __name__ == "__main__":
    run()
