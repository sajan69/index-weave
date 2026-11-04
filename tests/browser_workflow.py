"""Actual installed-wheel browser workflows using only fictional documents."""

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from indexweave import Writer

ROOT = Path(__file__).resolve().parents[1]
MODEL = None


@contextmanager
def server():
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary) / "source"
        with Writer.create(workspace):
            pass
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        command = [
            str(Path(sys.executable).with_name("indexweave-serve")),
            str(workspace),
            "--port",
            str(port),
        ]
        if MODEL:
            command += ["--model", str(MODEL)]
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        try:
            deadline = time.monotonic() + 45
            while True:
                try:
                    with urllib.request.urlopen(
                        urllib.request.Request(
                            base + "/api/status", headers={"X-IndexWeave-Request": "1"}
                        ),
                        timeout=0.5,
                    ) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise AssertionError(
                            "Local server did not become ready"
                        ) from None
                    time.sleep(0.05)
            yield base, workspace
        finally:
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
            process.stderr.close()


class BrowserWorkflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()
        (ROOT / "artifacts/browser").mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def fill(self, page, identifier="guide", text="A durable recovery journal."):
        page.get_by_role("button", name="+ New document", exact=True).click()
        page.get_by_label("Document ID", exact=True).fill(identifier)
        page.get_by_label("Title", exact=True).fill("Recovery guide")
        page.get_by_label("Tags", exact=False).fill("notes, storage")
        page.get_by_label("Document text", exact=True).fill(text)
        page.get_by_role("button", name="Save document", exact=True).click()
        from playwright.sync_api import expect

        expect(
            page.get_by_text("Saved at collection revision 1.", exact=True)
        ).to_be_visible()

    def test_create_search_edit_conflict_and_delete(self):
        from playwright.sync_api import expect

        with server() as (base, workspace):
            context = self.browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto(base)
            expect(page.get_by_text("0 documents · revision 0")).to_be_visible()
            self.fill(page)
            page.get_by_label("Search your collection", exact=True).fill(
                '"recovery journal" tag:storage'
            )
            page.get_by_label("Search mode", exact=True).select_option("strict")
            page.get_by_role("button", name="Search", exact=True).click()
            expect(page.locator(".result h3")).to_have_text("Recovery guide")
            page.screenshot(
                path=str(ROOT / "artifacts/browser/desktop.png"), full_page=True
            )
            page.get_by_label("Document text", exact=True).fill(
                "Draft kept after a conflict."
            )
            response = page.request.put(
                base + "/api/documents/guide",
                headers={"X-IndexWeave-Request": "1", "Origin": base},
                data={
                    "id": "guide",
                    "title": "Other tab",
                    "text": "Saved in another tab.",
                    "tags": [],
                    "expected_revision": 1,
                },
            )
            self.assertEqual(response.status, 200)
            page.get_by_role("button", name="Save document", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("draft has been kept")
            expect(page.get_by_label("Document text", exact=True)).to_have_value(
                "Draft kept after a conflict."
            )
            page.get_by_role(
                "button", name="Reload saved version to review", exact=True
            ).click()
            expect(page.get_by_label("Document text", exact=True)).to_have_value(
                "Saved in another tab."
            )
            pending = []
            page.route("**/api/documents/guide", lambda route: pending.append(route))
            page.get_by_role("button", name="Save document", exact=True).click()
            expect(page.get_by_label("Title", exact=True)).to_be_disabled()
            expect(
                page.get_by_role("button", name="+ New document", exact=True)
            ).to_be_disabled()
            page.wait_for_timeout(100)
            self.assertEqual(len(pending), 1)
            pending[0].continue_()
            expect(
                page.get_by_text("Saved at collection revision 3.", exact=True)
            ).to_be_visible()
            page.unroute("**/api/documents/guide")
            page.get_by_role("button", name="Delete document", exact=True).click()
            expect(
                page.get_by_text("Document deleted from the live collection.")
            ).to_be_visible()
            expect(page.get_by_text("0 documents · revision 4")).to_be_visible()
            context.close()

    def test_import_inert_text_download_and_cross_origin_refusal(self):
        from playwright.sync_api import expect

        with server() as (base, workspace):
            context = self.browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.goto(base)
            expect(page.get_by_text("0 documents · revision 0")).to_be_visible()
            raw = (
                b"\xef\xbb\xbf# Fixture\r\n"
                b"<script>window.bad=true</script> recovery\r\n"
            )
            page.get_by_label("Import UTF-8 document").set_input_files(
                {"name": "fixture.md", "mimeType": "text/markdown", "buffer": raw}
            )
            page.get_by_role("button", name="Save document", exact=True).click()
            expect(
                page.get_by_text("Saved at collection revision 1.", exact=True)
            ).to_be_visible()
            page.get_by_label("Search your collection", exact=True).fill("recovery")
            page.get_by_role("button", name="Search", exact=True).click()
            expect(page.locator(".result .passage")).to_contain_text("<script>")
            self.assertIsNone(page.evaluate("window.bad"))
            with page.expect_download() as downloaded:
                page.get_by_role("button", name="Download text", exact=True).click()
            self.assertEqual(Path(downloaded.value.path()).read_bytes(), raw)
            attack = context.new_page()
            attack.goto("data:text/html,<title>Foreign origin</title>")
            result = attack.evaluate(
                """async (base)=>{
                    try {
                        await fetch(base+'/api/documents', {
                            method:'POST',
                            headers:{'Content-Type':'application/json',
                                     'X-IndexWeave-Request':'1'},
                            body:JSON.stringify({id:'attack',title:'Attack',
                                text:'Text',tags:[],expected_revision:1})
                        });
                        return 'sent';
                    } catch {return 'blocked';}
                }""",
                base,
            )
            self.assertEqual(result, "blocked")
            status = page.request.get(
                base + "/api/status", headers={"X-IndexWeave-Request": "1"}
            ).json()
            self.assertEqual(status["revision"], 1)
            context.close()

    def test_mobile_layout_and_file_validation(self):
        from playwright.sync_api import expect

        with server() as (base, workspace):
            context = self.browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            requests = []
            page.on("request", lambda request: requests.append(request.url))
            page.goto(base)
            expect(page.get_by_text("0 documents · revision 0")).to_be_visible()
            page.get_by_label("Import UTF-8 document").set_input_files(
                {"name": "bad.txt", "mimeType": "text/plain", "buffer": b"\xff"}
            )
            expect(page.get_by_role("alert")).to_be_visible()
            self.fill(page, text="Notes for a small local collection.")
            self.assertTrue(
                page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            )
            self.assertTrue(all(url.startswith(base) for url in requests))
            page.screenshot(
                path=str(ROOT / "artifacts/browser/mobile.png"), full_page=True
            )
            context.close()

    def test_actual_model_refresh_search_and_invalidation(self):
        from playwright.sync_api import expect

        with server() as (base, workspace):
            context = self.browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.goto(base)
            expect(page.get_by_text("0 documents · revision 0")).to_be_visible()
            self.fill(page)
            page.get_by_role(
                "button", name="Refresh semantic index", exact=True
            ).click()
            expect(page.locator(".model-state strong")).to_have_text(
                "ready", timeout=60000
            )
            page.get_by_label("Search mode", exact=True).select_option("hybrid")
            page.get_by_label("Search your collection", exact=True).fill(
                "recovery notes"
            )
            page.get_by_role("button", name="Search", exact=True).click()
            expect(page.locator(".result h3")).to_have_text(
                "Recovery guide", timeout=15000
            )
            page.get_by_label("Document text", exact=True).fill(
                "An updated durable guide."
            )
            page.get_by_role("button", name="Save document", exact=True).click()
            expect(
                page.get_by_text("Saved at collection revision 2.", exact=True)
            ).to_be_visible()
            expect(page.locator(".model-state strong")).to_have_text(
                "stale", timeout=10000
            )
            page.get_by_role(
                "button", name="Refresh semantic index", exact=True
            ).click()
            expect(page.locator(".model-state strong")).to_have_text(
                "ready", timeout=60000
            )
            page.get_by_role("button", name="Search", exact=True).click()
            expect(page.locator(".result .passage")).to_contain_text("updated durable")
            context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path)
    args = parser.parse_args()
    MODEL = args.model.resolve() if args.model else None
    suite = unittest.TestSuite()
    for test in unittest.defaultTestLoader.loadTestsFromTestCase(BrowserWorkflows):
        if MODEL or "actual_model" not in test.id():
            suite.addTest(test)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print(
        "BROWSER_WORKFLOW="
        + json.dumps(
            {
                "completed": True,
                "scenarios": result.testsRun,
                "actual_model": MODEL is not None,
            }
        )
    )
