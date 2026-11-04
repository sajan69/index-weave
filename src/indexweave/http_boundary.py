"""Browser access policy for the local service; not user authentication."""

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

MAX_BODY_BYTES = 512 * 1024
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; font-src 'self'; img-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}


class LocalBoundary:
    def __init__(self, app, port):
        self.app = app
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {"http://" + host for host in self.hosts}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in SECURITY_HEADERS.items():
                    headers[key] = value
            await send(message)

        async def reject(status, code):
            await JSONResponse({"error": code}, status_code=status)(
                scope, receive, secure_send
            )

        headers = Headers(scope=scope)
        if len(headers.getlist("host")) != 1 or headers["host"] not in self.hosts:
            await reject(400, "invalid_host")
            return
        origins = headers.getlist("origin")
        if len(origins) > 1 or (origins and origins[0] not in self.origins):
            await reject(403, "origin_refused")
            return
        if scope["path"].startswith("/api/"):
            if (
                scope["method"] == "OPTIONS"
                or headers.get("x-indexweave-request") != "1"
                or headers.get("sec-fetch-site", "none") not in {"none", "same-origin"}
            ):
                await reject(403, "browser_request_refused")
                return
            if (
                scope["method"] in {"POST", "PUT", "DELETE"}
                and headers.get("content-type", "").split(";", 1)[0].strip()
                != "application/json"
            ):
                await reject(415, "json_required")
                return
        length = headers.get("content-length")
        if length is not None and (
            not length.isascii()
            or not length.isdigit()
            or len(length) > 10
            or int(length) > MAX_BODY_BYTES
        ):
            await reject(413, "body_too_large")
            return
        body = bytearray()
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_BODY_BYTES:
                await reject(413, "body_too_large")
                return
            more = message.get("more_body", False)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, secure_send)
