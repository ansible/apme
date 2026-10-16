"""Request-size limits enforced before Gateway route parsing."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

IMPORT_SCAN_MAX_BODY_BYTES = 16 * 1024 * 1024


class ImportScanBodyLimitMiddleware:
    """Reject oversized scan imports before FastAPI parses their JSON bodies.

    Args:
        app: Wrapped ASGI application.
        max_body_bytes: Maximum number of bytes accepted by the import route.
    """

    def __init__(self, app: ASGIApp, max_body_bytes: int = IMPORT_SCAN_MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Enforce the import route limit for fixed-length and streamed bodies.

        Args:
            scope: ASGI connection scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/v1/scans/import":
            await self.app(scope, receive, send)
            return

        content_length = next(
            (value for name, value in scope.get("headers", []) if name.lower() == b"content-length"),
            None,
        )
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    await self._send_too_large(scope, receive, send)
                    return
            except ValueError:
                pass

        messages: list[Message] = []
        body_size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body_size += len(message.get("body", b""))
            if body_size > self.max_body_bytes:
                await self._send_too_large(scope, receive, send)
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        message_index = 0

        async def replay_receive() -> Message:
            nonlocal message_index
            if message_index < len(messages):
                message = messages[message_index]
                message_index += 1
                return message
            return await receive()

        await self.app(scope, replay_receive, send)

    async def _send_too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Send the stable 413 response for oversized import bodies.

        Args:
            scope: ASGI connection scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        response = JSONResponse(
            status_code=413,
            content={"detail": f"Request body exceeds the {self.max_body_bytes} byte limit"},
        )
        await response(scope, receive, send)
