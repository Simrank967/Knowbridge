"""KNOWBRIDGE FastAPI application (Simran's module: reconstruction engine).
Handover generation and the UI are out of scope and are meant to consume these endpoints."""
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from pathlib import Path

from .config import VERSION
from .docs_ui import CONTACT, DESCRIPTION, LICENSE, TAGS, TITLE, swagger_page
from .routers import documents, knowledge, sources, system
from .chatbot.router import router as chatbot_router
from .reports.router import router as reports_router
from .handover import router as handover_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("knowbridge.api")

app = FastAPI(
    title=TITLE, version=VERSION, description=DESCRIPTION, contact=CONTACT, license_info=LICENSE,
    openapi_tags=TAGS, docs_url=None, redoc_url="/redoc",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def timing_and_errors(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:  # never leak a stack trace to API consumers
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "internal server error"}, status_code=500)
    response.headers["X-Process-Time-ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
    return response


@app.get("/", include_in_schema=False)
def root():
    page = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/docs", include_in_schema=False)
def custom_docs():
    return swagger_page(app.openapi_url)


app.include_router(system.router)
app.include_router(documents.router)
app.include_router(sources.router)
app.include_router(knowledge.router)
app.include_router(chatbot_router)
app.include_router(reports_router)
app.include_router(handover_router)
