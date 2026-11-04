import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from test_semantic import FakeEncoder

from indexweave import Document, Put, Writer, read_snapshot
from indexweave.api import create_app
from indexweave.errors import Busy, CommitUnknown

BASE = "http://127.0.0.1:8765"
HEADERS = {"X-IndexWeave-Request": "1", "Origin": BASE}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        with Writer.create(self.source):
            pass
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<!doctype html><title>Fixture</title>")
        self.app = create_app(self.source, web_root=self.web)
        self.client = TestClient(self.app, base_url=BASE, headers=HEADERS)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def payload(self, identifier="guide", revision=0, text="A recovery guide"):
        return {
            "id": identifier,
            "title": "Guide",
            "text": text,
            "tags": ["notes"],
            "expected_revision": revision,
        }

    def test_complete_lifecycle_search_and_source_revisions(self):
        self.assertEqual(
            self.client.post("/api/documents", json=self.payload()).status_code, 201
        )
        record = self.client.get("/api/documents/guide").json()
        self.assertEqual(record["document_revision"], 1)
        self.assertEqual(self.client.get("/api/status").json()["document_count"], 1)
        result = self.client.post(
            "/api/search", json={"query": "recovery?", "mode": "text"}
        ).json()
        self.assertEqual(result["hits"][0]["id"], "guide")
        self.assertEqual(
            self.client.put(
                "/api/documents/guide",
                json=self.payload(revision=1, text="Updated note"),
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post("/api/search", json={"query": "recovery"}).json()[
                "total_matches"
            ],
            0,
        )
        self.assertEqual(
            self.client.request(
                "DELETE", "/api/documents/guide", json={"expected_revision": 2}
            ).status_code,
            200,
        )
        self.assertEqual(self.client.get("/api/documents/guide").status_code, 404)

    def test_stale_cas_create_collision_and_no_lost_update(self):
        self.client.post("/api/documents", json=self.payload())
        self.assertEqual(
            self.client.post(
                "/api/documents", json=self.payload(revision=1)
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.put(
                "/api/documents/guide", json=self.payload(revision=0, text="Lost edit")
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.get("/api/documents/guide").json()["document"]["text"],
            "A recovery guide",
        )
        self.assertEqual(
            self.client.put(
                "/api/documents/missing",
                json=self.payload(identifier="missing", revision=1),
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.put(
                "/api/documents/guide",
                json=self.payload(identifier="other", revision=1),
            ).status_code,
            422,
        )
        self.assertEqual(read_snapshot(self.source).revision, 1)

    def test_browser_origin_host_fetch_metadata_and_header_boundary(self):
        for headers, status in [
            ({"Host": "evil.example:8765"}, 400),
            ({"Host": "localhost:1234"}, 400),
            ({"Origin": "http://evil.example"}, 403),
            ({"Origin": "null"}, 403),
            ({"Sec-Fetch-Site": "cross-site"}, 403),
            ({"Sec-Fetch-Site": "same-site"}, 403),
            ({"X-IndexWeave-Request": "wrong"}, 403),
        ]:
            with self.subTest(headers=headers):
                self.assertEqual(
                    self.client.get("/api/status", headers=headers).status_code, status
                )
        self.assertEqual(self.client.options("/api/status").status_code, 403)
        saved = self.client.headers.pop("x-indexweave-request")
        self.assertEqual(self.client.get("/api/status").status_code, 403)
        self.client.headers["x-indexweave-request"] = saved
        response = self.client.get("/")
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_strict_validation_no_input_echo_and_bounded_body(self):
        before = (self.source / "documents.iwl").read_bytes()
        for payload in [
            self.payload(revision=True),
            {**self.payload(), "unexpected": "PRIVATE-TEXT"},
            self.payload(text="🙂" * 20000),
            {**self.payload(), "tags": ["bad tag"]},
            self.payload(identifier="../escape"),
        ]:
            response = self.client.post("/api/documents", json=payload)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("PRIVATE-TEXT", response.text)
        self.assertEqual(
            self.client.post(
                "/api/documents", content="x", headers={"Content-Type": "text/plain"}
            ).status_code,
            415,
        )
        self.assertEqual(
            self.client.post(
                "/api/documents",
                content=b"x" * (512 * 1024 + 1),
                headers={"Content-Type": "application/json"},
            ).status_code,
            413,
        )
        self.assertEqual(
            self.client.post(
                "/api/documents",
                content=b"{bad",
                headers={"Content-Type": "application/json"},
            ).status_code,
            422,
        )
        self.assertEqual((self.source / "documents.iwl").read_bytes(), before)

    def test_pagination_private_paths_and_lifetime_lease(self):
        service = self.app.state.service
        for i in range(3):
            service.put(Document("n" + str(i), "Note", "Text"), i, create=True)
        first = self.client.get("/api/documents?limit=2").json()
        self.assertEqual([x["id"] for x in first["documents"]], ["n0", "n1"])
        self.assertEqual(
            self.client.get("/api/documents?after=n1").json()["documents"][0]["id"],
            "n2",
        )
        self.assertEqual(self.client.get("/api/documents?limit=101").status_code, 422)
        self.assertEqual(
            self.client.get("/api/documents?after=../bad").status_code, 422
        )
        self.assertEqual(self.client.get("/api/files/etc/passwd").status_code, 404)
        with self.assertRaises(Busy):
            Writer(self.source)

    def test_unknown_commit_and_io_failure_preserve_records(self):
        writer = self.app.state.service.writer
        before = (self.source / "documents.iwl").read_bytes()
        with patch.object(writer, "write", side_effect=OSError("PRIVATE/PATH")):
            response = self.client.post("/api/documents", json=self.payload())
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("PRIVATE/PATH", response.text)
        with patch.object(
            writer,
            "write",
            side_effect=CommitUnknown("Outcome unknown; inspect before retry."),
        ):
            self.assertEqual(
                self.client.post("/api/documents", json=self.payload()).status_code, 503
            )
        self.assertEqual(self.client.get("/api/status").status_code, 503)
        self.assertEqual((self.source / "documents.iwl").read_bytes(), before)

    def test_streamed_body_cap_without_content_length(self):
        response = self.client.post(
            "/api/documents",
            content=iter([b"{", b"x" * (512 * 1024)]),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.get("/api/status").json()["revision"], 0)

    def test_search_admission_is_nonblocking(self):
        gate = self.app.state.service.query_gate
        gate.acquire()
        try:
            self.assertEqual(
                self.client.post("/api/search", json={"query": "notes"}).status_code,
                503,
            )
        finally:
            gate.release()
        self.assertEqual(
            self.client.post("/api/search", json={"query": "notes"}).status_code, 200
        )


class SemanticServiceTests(unittest.TestCase):
    def test_obsolete_background_build_never_publishes_and_refresh_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            with Writer.create(source) as writer:
                writer.write(
                    [Put(Document("a", "Guide", "red fox"))], expected_revision=0
                )
            from indexweave.app_service import WorkspaceService

            encoder = FakeEncoder()
            started = threading.Event()
            release = threading.Event()
            original = encoder.encode_documents

            def slow(texts):
                started.set()
                release.wait(5)
                return original(texts)

            encoder.encode_documents = slow
            service = WorkspaceService(source, encoder)
            try:
                service.refresh(1)
                self.assertTrue(started.wait(5))
                with self.assertRaises(Busy):
                    service.refresh(1)
                service.put(Document("a", "New", "blue fox"), 1, create=False)
                release.set()
                deadline = time.monotonic() + 5
                while (
                    service.status()["semantic_state"] == "building"
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                self.assertEqual(service.status()["semantic_state"], "stale")
                self.assertEqual(
                    service.search("blue", "text", 10)["hits"][0]["document_revision"],
                    2,
                )
                service.refresh(2)
                deadline = time.monotonic() + 5
                while (
                    service.status()["semantic_state"] == "building"
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                self.assertEqual(service.status()["semantic_state"], "ready")
                self.assertEqual(
                    service.search("blue", "semantic", 10)["hits"][0][
                        "document_revision"
                    ],
                    2,
                )
                service.delete("a", 2)
                self.assertEqual(service.status()["semantic_state"], "stale")
            finally:
                release.set()
                service.close()

    def test_background_failure_is_visible_and_lexical_remains_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            with Writer.create(source) as writer:
                writer.write(
                    [Put(Document("a", "Note", "red fox"))], expected_revision=0
                )
            from indexweave.app_service import WorkspaceService

            encoder = FakeEncoder()
            encoder.encode_documents = lambda texts: (_ for _ in ()).throw(
                RuntimeError("PRIVATE")
            )
            service = WorkspaceService(source, encoder)
            try:
                service.refresh(1)
                deadline = time.monotonic() + 5
                while (
                    service.status()["semantic_state"] == "building"
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                self.assertEqual(service.status()["semantic_state"], "error")
                self.assertEqual(
                    service.search("red", "text", 10)["hits"][0]["id"], "a"
                )
            finally:
                service.close()
