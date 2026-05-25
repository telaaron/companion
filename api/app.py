"""FastAPI application factory and configuration."""

import traceback
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.types import Receive, Scope, Send

from config.logging_config import configure_logging
from config.settings import get_settings
from core.trace import extract_claude_session_id_from_headers, trace_event
from providers.exceptions import ProviderError

from .admin_routes import router as admin_router
from .dashboard_routes import dashboard_router
from .routes import router
from .runtime import AppRuntime, startup_failure_message
from .validation_log import summarize_request_validation_body

# Pydantic emits noisy UserWarnings when serializing union-typed content blocks
# (tool_use, tool_result, etc.) — the dump still works correctly. Suppress at
# import time so multi-turn agent conversations don't flood the log.
warnings.filterwarnings(
    "ignore",
    message=r"Pydantic serializer warnings:",
    category=UserWarning,
    module=r"pydantic\.main",
)

_UI_STATIC_DIR = Path(__file__).resolve().parent / "ui_static"
# Modern SvelteKit build (preferred if present). Falls back to the legacy
# vanilla bundle in `ui_static/` so older deploys keep working.
_UI_SVELTE_DIR = Path(__file__).resolve().parents[1] / "web" / "build"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    runtime = AppRuntime.for_app(app, settings=get_settings())
    await runtime.startup()

    yield

    await runtime.shutdown()


class GracefulLifespanApp:
    """ASGI wrapper that reports startup failures without Starlette tracebacks."""

    def __init__(self, app: FastAPI):
        self.app = app

    def __getattr__(self, name: str) -> Any:
        return getattr(self.app, name)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":
            await self.app(scope, receive, send)
            return
        await self._lifespan(receive, send)

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        settings = get_settings()
        runtime = AppRuntime.for_app(self.app, settings=settings)
        startup_complete = False
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await runtime.startup()
                except Exception as exc:
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": startup_failure_message(settings, exc),
                        }
                    )
                    return
                startup_complete = True
                await send({"type": "lifespan.startup.complete"})
                continue

            if message["type"] == "lifespan.shutdown":
                if startup_complete:
                    try:
                        await runtime.shutdown()
                    except Exception as exc:
                        logger.error("Shutdown failed: exc_type={}", type(exc).__name__)
                        await send({"type": "lifespan.shutdown.failed", "message": ""})
                        return
                await send({"type": "lifespan.shutdown.complete"})
                return


def create_app(*, lifespan_enabled: bool = True) -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    configure_logging(
        settings.log_file, verbose_third_party=settings.log_raw_api_payloads
    )

    app_kwargs: dict[str, Any] = {
        "title": "Claude Code Proxy",
        "version": "2.0.0",
    }
    if lifespan_enabled:
        app_kwargs["lifespan"] = lifespan
    app = FastAPI(**app_kwargs)

    @app.middleware("http")
    async def trace_http_correlation(request: Request, call_next):
        """Attach HTTP identifiers and optional Claude session id to logs."""
        claude_sid = extract_claude_session_id_from_headers(request.headers)
        with logger.contextualize(
            http_method=request.method,
            http_path=request.url.path,
            claude_session_id=claude_sid,
        ):
            response = await call_next(request)
        return response

    # Register routes
    app.include_router(admin_router)
    app.include_router(router)
    app.include_router(dashboard_router)

    # Browser UI — single bundle served at /ui. Prefer the SvelteKit build
    # in `web/build/` when present; fall back to the legacy vanilla bundle in
    # `ui_static/`. The SvelteKit build needs an SPA fallback so deep links
    # like /ui/settings resolve to index.html on initial load.
    _ui_dir = _UI_SVELTE_DIR if _UI_SVELTE_DIR.is_dir() else _UI_STATIC_DIR
    if _ui_dir.is_dir():
        from fastapi.responses import FileResponse

        _index_html = _ui_dir / "index.html"
        app.mount(
            "/ui",
            StaticFiles(directory=str(_ui_dir), html=True),
            name="ui",
        )

        @app.get("/ui", include_in_schema=False)
        async def _ui_redirect() -> RedirectResponse:
            return RedirectResponse(url="/ui/", status_code=307)

        @app.get("/app", include_in_schema=False)
        async def _app_legacy_redirect() -> RedirectResponse:
            return RedirectResponse(url="/ui/", status_code=308)

        # SPA fallback — any unknown /ui/<path> returns index.html so the
        # client-side router (SvelteKit) can take over. Only fires for paths
        # that aren't a real file under the mount.
        if _ui_dir is _UI_SVELTE_DIR and _index_html.is_file():
            from starlette.exceptions import HTTPException as _StarletteHTTPException

            @app.exception_handler(404)
            async def _spa_fallback(request, exc):  # type: ignore[no-untyped-def]
                if request.url.path.startswith("/ui/"):
                    return FileResponse(str(_index_html))
                # Preserve default 404 behavior for everything else.
                if isinstance(exc, _StarletteHTTPException):
                    return JSONResponse(
                        {"detail": exc.detail}, status_code=exc.status_code
                    )
                return JSONResponse({"detail": "Not Found"}, status_code=404)

        @app.get("/app/", include_in_schema=False)
        async def _app_slash_legacy_redirect() -> RedirectResponse:
            return RedirectResponse(url="/ui/", status_code=308)
    else:
        logger.warning(
            "UI static dir not found at {} — /ui will 404 until the package "
            "is reinstalled.",
            _UI_STATIC_DIR,
        )

    # Exception handlers
    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        """Log request shape for 422 debugging without content values."""
        body: Any
        try:
            body = await request.json()
        except Exception as e:
            body = {"_json_error": type(e).__name__}

        message_summary, tool_names = summarize_request_validation_body(body)

        trace_event(
            stage="ingress",
            event="server.request.validation_failed",
            source="api",
            path=request.url.path,
            query=dict(request.query_params),
            error_locs=[list(error.get("loc", ())) for error in exc.errors()],
            error_types=[str(error.get("type", "")) for error in exc.errors()],
            message_summary=message_summary,
            tool_names=tool_names,
        )
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(ProviderError)
    async def provider_error_handler(request: Request, exc: ProviderError):
        """Handle provider-specific errors and return Anthropic format."""
        err_settings = get_settings()
        if err_settings.log_api_error_tracebacks:
            logger.error(
                "Provider Error: error_type={} status_code={} message={}",
                exc.error_type,
                exc.status_code,
                exc.message,
            )
        else:
            logger.error(
                "Provider Error: error_type={} status_code={}",
                exc.error_type,
                exc.status_code,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_anthropic_format(),
        )

    @app.exception_handler(Exception)
    async def general_error_handler(request: Request, exc: Exception):
        """Handle general errors and return Anthropic format."""
        settings = get_settings()
        if settings.log_api_error_tracebacks:
            logger.error("General Error: {}", exc)
            logger.error(traceback.format_exc())
        else:
            logger.error(
                "General Error: path={} method={} exc_type={}",
                request.url.path,
                request.method,
                type(exc).__name__,
            )
        return JSONResponse(
            status_code=500,
            content={
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": "An unexpected error occurred.",
                },
            },
        )

    return app


def create_asgi_app() -> GracefulLifespanApp:
    """Create the server ASGI app with graceful lifespan failure reporting."""
    return GracefulLifespanApp(create_app(lifespan_enabled=False))
