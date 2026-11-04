"""Single-workspace local HTTP API and bundled interface."""

import importlib.resources
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .app_service import WorkspaceService
from .errors import (
    Busy,
    CommitUnknown,
    Conflict,
    InvalidInput,
    MissingDocument,
    StoreError,
    UnusableWriter,
)
from .http_boundary import SECURITY_HEADERS, LocalBoundary
from .model import MAX_REVISION, Document

Revision = Annotated[int, Field(ge=0, le=MAX_REVISION)]


class RevisionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    expected_revision: Revision


class DocumentRequest(RevisionRequest):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1, max_length=65536)
    tags: list[str] = Field(default_factory=list, max_length=16)

    def document(self):
        return Document(self.id, self.title, self.text, tuple(self.tags))


class SearchRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    query: str = Field(min_length=1, max_length=2048)
    mode: Literal["text", "strict", "semantic", "hybrid"] = "text"
    limit: int = Field(default=10, ge=1, le=100)


def create_app(workspace, *, port=8765, model_path=None, encoder=None, web_root=None):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Local server port requires 1024–65535")
    root = (
        Path(web_root)
        if web_root
        else Path(str(importlib.resources.files("indexweave").joinpath("web")))
    )

    @asynccontextmanager
    async def lifespan(app):
        nonlocal encoder
        if model_path is not None:
            from .embedding import MiniLMEncoder

            encoder = MiniLMEncoder(model_path)
        service = WorkspaceService(workspace, encoder)
        app.state.service = service
        try:
            yield
        finally:
            service.close()

    app = FastAPI(
        title="IndexWeave local API",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost"],
        www_redirect=False,
    )
    app.add_middleware(LocalBoundary, port=port)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse(
            {
                "error": "invalid_request",
                "message": "Check required fields, types and input limits.",
            },
            status_code=422,
        )

    @app.exception_handler(StoreError)
    async def store_error(request, error):
        status = (
            503
            if isinstance(error, (Busy, CommitUnknown, UnusableWriter))
            else 409
            if isinstance(error, Conflict)
            else 404
            if isinstance(error, MissingDocument)
            else 422
            if isinstance(error, InvalidInput)
            else 503
        )
        message = (
            str(error)
            if isinstance(
                error, (Conflict, Busy, MissingDocument, InvalidInput, CommitUnknown)
            )
            else "Storage is unavailable. Stop the server and inspect the workspace."
        )
        return JSONResponse(
            {"error": type(error).__name__, "message": message}, status_code=status
        )

    @app.exception_handler(OSError)
    async def io_error(request, error):
        return JSONResponse(
            {
                "error": "unavailable",
                "message": (
                    "Operation failed. Preserve your draft and inspect "
                    "the workspace before retrying."
                ),
            },
            status_code=503,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        return JSONResponse(
            {
                "error": "unavailable",
                "message": (
                    "Server operation failed. Preserve your draft "
                    "and inspect before retrying."
                ),
            },
            status_code=503,
            headers=SECURITY_HEADERS,
        )

    @app.get("/api/status")
    def status():
        return app.state.service.status()

    @app.get("/api/documents")
    def listing(
        after: str = Query(default="", max_length=64),
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return app.state.service.list_documents(after, limit)

    @app.get("/api/documents/{identifier}")
    def get(identifier: str):
        return app.state.service.get(identifier)

    @app.post("/api/documents", status_code=201)
    def create(body: DocumentRequest):
        return app.state.service.put(
            body.document(), body.expected_revision, create=True
        )

    @app.put("/api/documents/{identifier}")
    def update(identifier: str, body: DocumentRequest):
        if body.id != identifier:
            raise InvalidInput("Document ID does not match its route.")
        return app.state.service.put(
            body.document(), body.expected_revision, create=False
        )

    @app.delete("/api/documents/{identifier}")
    def delete(identifier: str, body: RevisionRequest):
        return app.state.service.delete(identifier, body.expected_revision)

    @app.post("/api/search")
    def search(body: SearchRequest):
        return app.state.service.search(body.query, body.mode, body.limit)

    @app.post("/api/semantic/refresh", status_code=202)
    def refresh(body: RevisionRequest):
        return app.state.service.refresh(body.expected_revision)

    @app.get("/", include_in_schema=False)
    def index():
        if not (root / "index.html").is_file():
            raise HTTPException(503, "Build the interface before serving it.")
        return FileResponse(root / "index.html")

    if (root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")
    return app
