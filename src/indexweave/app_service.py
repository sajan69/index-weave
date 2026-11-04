"""One leased workspace and bounded, revision-aware application caches."""

from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Lock, RLock

from .errors import (
    Busy,
    CommitUnknown,
    Conflict,
    InvalidInput,
    MissingDocument,
    StaleIndex,
    UnusableWriter,
)
from .lexical import LexicalIndex
from .model import Delete, Put
from .semantic import SemanticIndex, hybrid_search
from .storage import Writer


class WorkspaceService:
    def __init__(self, workspace, encoder=None):
        self.writer = Writer(workspace)
        self.encoder = encoder
        self.lock = RLock()
        self.inference_lock = Lock()
        self.query_gate = BoundedSemaphore(1)
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="indexweave")
        self.lexical = None
        self.semantic = None
        self.building = False
        self.build_error = False
        self.closed = False
        self.failed = False

    def _snapshot(self):
        if self.failed:
            raise UnusableWriter("Stop the server and inspect before restarting.")
        return self.writer.snapshot()

    def _write(self, operations, expected_revision):
        try:
            return self.writer.write(operations, expected_revision=expected_revision)
        except CommitUnknown:
            self.failed = True
            raise

    def close(self):
        with self.lock:
            self.closed = True
        self.worker.shutdown(wait=True, cancel_futures=True)
        self.writer.close()

    def status(self):
        with self.lock:
            snapshot = self._snapshot()
            state = (
                "unavailable"
                if self.encoder is None
                else "building"
                if self.building
                else "ready"
                if self.semantic is not None
                else "error"
                if self.build_error
                else "stale"
            )
            return {
                "revision": snapshot.revision,
                "document_count": len(snapshot.documents),
                "semantic_state": state,
                "semantic_revision": self.semantic.snapshot.revision
                if self.semantic
                else None,
            }

    def list_documents(self, after="", limit=25):
        from .model import valid_id

        if after:
            valid_id(after)
        with self.lock:
            snapshot = self._snapshot()
        keys = sorted(key for key in snapshot.documents if key > after)
        page = keys[:limit]
        return {
            "revision": snapshot.revision,
            "documents": [
                {
                    "id": key,
                    "title": snapshot.documents[key].document.title,
                    "tags": list(snapshot.documents[key].document.tags),
                    "document_revision": snapshot.documents[key].revision,
                }
                for key in page
            ],
            "next_after": page[-1] if len(keys) > len(page) and page else None,
        }

    def get(self, identifier):
        with self.lock:
            snapshot = self._snapshot()
            record = snapshot.get(identifier)
        if record is None:
            raise MissingDocument("Document was not found.")
        return {
            "store_revision": snapshot.revision,
            "document_revision": record.revision,
            "document": record.document.to_dict(),
        }

    def put(self, document, expected_revision, *, create):
        with self.lock:
            snapshot = self._snapshot()
            if snapshot.revision != expected_revision:
                raise Conflict(
                    "Collection changed. Review the saved version before retrying."
                )
            exists = snapshot.get(document.id) is not None
            if create and exists:
                raise Conflict("This document ID already exists. Open it to edit.")
            if not create and not exists:
                raise MissingDocument("Document was not found.")
            result = self._write([Put(document)], expected_revision)
            self.lexical = self.semantic = None
            self.build_error = False
            return {
                "revision": result.revision,
                "document_count": len(result.documents),
            }

    def delete(self, identifier, expected_revision):
        with self.lock:
            result = self._write([Delete(identifier)], expected_revision)
            self.lexical = self.semantic = None
            self.build_error = False
            return {
                "revision": result.revision,
                "document_count": len(result.documents),
            }

    def refresh(self, expected_revision):
        with self.lock:
            snapshot = self._snapshot()
            if self.encoder is None:
                raise InvalidInput("No local embedding model is configured.")
            if snapshot.revision != expected_revision:
                raise Conflict("Collection changed. Refresh its status first.")
            if self.building:
                raise Busy("One semantic refresh is already running.")
            self.semantic = None
            self.building = True
            self.build_error = False
            self.worker.submit(self._build, snapshot)
            return {"revision": snapshot.revision, "semantic_state": "building"}

    def _build(self, snapshot):
        try:
            with self.inference_lock:
                candidate = SemanticIndex.from_snapshot(snapshot, self.encoder)
            with self.lock:
                if not self.closed and self._snapshot().revision == snapshot.revision:
                    self.semantic = candidate
        except Exception:
            # Do not expose document text, paths or model internals in HTTP errors.
            with self.lock:
                self.build_error = True
        finally:
            with self.lock:
                self.building = False

    def search(self, query, mode, limit):
        if not self.query_gate.acquire(blocking=False):
            raise Busy("A search is already running. Retry after it finishes.")
        try:
            with self.lock:
                snapshot = self._snapshot()
                if mode != "semantic" and self.lexical is None:
                    self.lexical = LexicalIndex.from_snapshot(snapshot)
                lexical = self.lexical
                semantic = self.semantic
            if mode in {"semantic", "hybrid"}:
                if semantic is None:
                    raise StaleIndex(
                        "Refresh the semantic index for this collection revision first."
                    )
                with self.inference_lock:
                    if mode == "semantic":
                        return semantic.search(
                            query, expected_snapshot=snapshot, limit=limit
                        )
                    return hybrid_search(
                        lexical,
                        semantic,
                        query,
                        expected_snapshot=snapshot,
                        limit=limit,
                    )
            return (
                lexical.search(query, limit=limit)
                if mode == "strict"
                else lexical.rank_text(query, limit=limit)
            )
        finally:
            self.query_gate.release()
