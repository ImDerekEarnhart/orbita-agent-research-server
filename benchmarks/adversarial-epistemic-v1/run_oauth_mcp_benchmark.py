#!/usr/bin/env python3
"""Acquire a short-lived Orbita OAuth token, run the MCP benchmark, then revoke it."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

BASE_URL = "https://orbita-agent-research-server-production.up.railway.app"
MCP_URL = f"{BASE_URL}/mcp"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_form(url: str, payload: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body.strip() else {}


def _register(redirect_uri: str) -> dict[str, Any]:
    return _post_json(
        f"{BASE_URL}/register",
        {
            "client_name": "Orbita GPT-5.6 benchmark",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": "orbita:use",
            "token_endpoint_auth_method": "client_secret_post",
        },
    )


def _revoke(token: str, client: dict[str, Any]) -> None:
    try:
        _post_form(
            f"{BASE_URL}/revoke",
            {
                "token": token,
                "client_id": str(client["client_id"]),
                "client_secret": str(client["client_secret"]),
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: automatic OAuth-token revocation failed: {type(exc).__name__}", flush=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--responses-from", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8767)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    redirect_uri = f"http://127.0.0.1:{args.port}/callback"
    client = _register(redirect_uri)
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(32)
    callback: dict[str, str] = {}
    callback_ready = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            values = urllib.parse.parse_qs(parsed.query)
            callback.update({key: items[0] for key, items in values.items() if items})
            body = (
                b"<html><body><h2>Orbita benchmark authorized.</h2>"
                b"<p>You can return to Codex. The benchmark is running.</p></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            callback_ready.set()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", args.port), CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    auth_url = f"{BASE_URL}/authorize?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "scope": "orbita:use",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": MCP_URL,
        }
    )
    print(f"AUTH_URL={auth_url}", flush=True)
    if not callback_ready.wait(timeout=600):
        server.shutdown()
        raise RuntimeError("OAuth authorization timed out after 10 minutes")
    server.shutdown()
    if callback.get("state") != state:
        raise RuntimeError("OAuth callback state did not match")
    if callback.get("error"):
        raise RuntimeError(f"OAuth authorization failed: {callback['error']}")
    code = callback.get("code")
    if not code:
        raise RuntimeError("OAuth callback did not contain an authorization code")

    token_response = _post_form(
        f"{BASE_URL}/token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": str(client["client_id"]),
            "client_secret": str(client["client_secret"]),
            "code_verifier": verifier,
            "resource": MCP_URL,
        },
    )
    access_token = str(token_response["access_token"])
    environment = os.environ.copy()
    environment["ORBITA_AGENT_API_TOKEN"] = access_token
    runner = Path(__file__).resolve().with_name("run_empirical.py")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(runner),
                "--out",
                str(args.out.resolve()),
                "--providers",
                "openai",
                "--conditions",
                "direct",
                "orbita-mcp",
                "--responses-from",
                str(args.responses_from.resolve()),
                "--workers",
                "2",
            ],
            env=environment,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"Benchmark runner exited with code {completed.returncode}")
    finally:
        _revoke(access_token, client)
    print("BENCHMARK_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
