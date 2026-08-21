from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event
from urllib.parse import parse_qs, urlparse


class OAuthCallbackServer:
    def __init__(self, host: str, port: int, expected_state: str) -> None:
        self.host = host
        self.port = port
        self.expected_state = expected_state
        self.code: str | None = None
        self.error: str | None = None
        self.done = Event()

    def wait_for_code(self) -> str:
        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                state = (params.get("state") or [""])[0]
                if state != server_ref.expected_state:
                    server_ref.error = "OAuth state mismatch."
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"State mismatch. Return to terminal.")
                elif "code" in params:
                    server_ref.code = params["code"][0]
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"Authorization complete. You can close this tab.")
                else:
                    server_ref.error = (params.get("error") or ["Authorization failed."])[0]
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Authorization failed. Return to terminal.")
                server_ref.done.set()

            def log_message(self, format: str, *args: object) -> None:
                return

        with HTTPServer((self.host, self.port), Handler) as httpd:
            while not self.done.is_set():
                httpd.handle_request()
        if self.error:
            raise SystemExit(self.error)
        if not self.code:
            raise SystemExit("OAuth callback did not include an authorization code.")
        return self.code
