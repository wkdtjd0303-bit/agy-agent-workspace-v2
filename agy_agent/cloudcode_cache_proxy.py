#!/usr/bin/env python3
"""
CloudCode Caching Proxy — AGY Language Server Quota Leak Neutralizer
====================================================================
Intercepts AGY CLI's internal Language Server polling (loadCodeAssist,
fetchAvailableModels) by acting as a local HTTP caching proxy.

How it works:
  1. AGY subprocess is started with CLOUD_CODE_URL=http://127.0.0.1:<port>
  2. First request to each API path → proxied to real Google server, response cached
  3. All subsequent requests → instant cached response (zero Google API calls)

This eliminates the ~0.3% Gemini quota drain per 6-minute polling cycle while
preserving the initial Eligibility check that AGY requires to start.
"""

import json
import logging
import os
import ssl
import threading
import time
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Dict, Any

logger = logging.getLogger("CloudCodeProxy")

REAL_SERVER = "https://daily-cloudcode-pa.googleapis.com"
PROXY_PORT = 19876
PROXY_HOST = "127.0.0.1"

# Cache: {path: (response_bytes, timestamp)}
_cache: dict = {}
_cache_lock = threading.Lock()
# Cache TTL: 4 hours (well beyond typical session duration)
CACHE_TTL_SEC = 4 * 3600

_server_instance = None
_server_thread = None


class CloudCodeCachingHandler(BaseHTTPRequestHandler):
    """Caching reverse proxy for AGY CloudCode API calls."""

    # Suppress per-request log lines (too noisy)
    def log_message(self, format, *args):
        pass

    # Control-plane paths that should be cached (LS polling)
    _CACHEABLE_PATHS = {
        "/v1internal:loadCodeAssist",
        "/v1internal:fetchAvailableModels",
        "/v1internal:fetchAdminControls",
        "/v1internal:fetchUserInfo",
        "/v1internal:retrieveUserQuotaSummary",
        "/v1internal:listExperiments",
        "/v1internal:writeTrajectoryAcls",
    }

    def _read_request_body(self) -> bytes:
        """Read request body handling both Content-Length and chunked encoding.

        AGY's Go HTTP client uses Transfer-Encoding: chunked for inference
        requests (streamGenerateContent). Python's BaseHTTPRequestHandler
        doesn't auto-dechunk, so we do it manually.
        """
        content_length = self.headers.get("Content-Length")
        if content_length is not None:
            return self.rfile.read(int(content_length))

        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            body_parts = []
            while True:
                size_line = self.rfile.readline().strip()
                if not size_line:
                    continue
                chunk_size = int(size_line, 16)
                if chunk_size == 0:
                    self.rfile.readline()  # trailing CRLF
                    break
                chunk_data = self.rfile.read(chunk_size)
                body_parts.append(chunk_data)
                self.rfile.readline()  # chunk CRLF
            return b"".join(body_parts)

        return b""

    def do_POST(self):
        path = self.path
        body = self._read_request_body()

        # LLM inference paths (streamGenerateContent, etc.) must be proxied
        # directly to Google WITHOUT caching and with full header fidelity.
        base_path = path.split("?")[0]
        if base_path not in self._CACHEABLE_PATHS:
            self._passthrough(path, body)
            return

        # Check cache for control-plane paths
        with _cache_lock:
            if path in _cache:
                cached_data, cached_time, cached_ct = _cache[path]
                age = time.time() - cached_time
                if age < CACHE_TTL_SEC:
                    self._send_cached(cached_data, path, age, cached_ct)
                    return

        # Cache miss or expired → proxy to real server
        logger.info(
            f"[CloudCodeProxy] REQ {path} bodyLen={len(body)}"
        )
        self._proxy_and_cache(path, body)

    def do_GET(self):
        """Some endpoints might use GET."""
        path = self.path
        with _cache_lock:
            if path in _cache:
                cached_data, cached_time, cached_ct = _cache[path]
                age = time.time() - cached_time
                if age < CACHE_TTL_SEC:
                    self._send_cached(cached_data, path, age, cached_ct)
                    return
        self._proxy_and_cache(path, None, method="GET")

    def _passthrough(self, path: str, body: bytes | None, method: str = "POST"):
        """Direct passthrough proxy for LLM inference — no caching, raw streaming.

        Uses http.client instead of urllib to support SSE streaming without buffering.
        """
        import http.client as httplib

        # Forward ALL headers for full fidelity
        skip_headers = {"host", "transfer-encoding", "connection"}
        forward_headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in skip_headers
        }
        if body is not None:
            forward_headers["Content-Length"] = str(len(body))

        real_host = "daily-cloudcode-pa.googleapis.com"
        try:
            ctx = ssl.create_default_context()
            conn = httplib.HTTPSConnection(real_host, 443, context=ctx, timeout=300)
            conn.request(method, path, body=body, headers=forward_headers)
            resp = conn.getresponse()

            # Forward status and headers
            self.send_response(resp.status)
            for hdr_name, hdr_val in resp.getheaders():
                hn = hdr_name.lower()
                if hn in ("transfer-encoding", "connection", "keep-alive"):
                    continue
                self.send_header(hdr_name, hdr_val)
            self.end_headers()

            # Stream response in chunks (supports SSE)
            while True:
                chunk = resp.read(32768)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except BrokenPipeError:
                    break

            conn.close()

        except Exception as e:
            logger.warning(f"[CloudCodeProxy] Passthrough error for {path}: {e}")
            try:
                error_msg = json.dumps({"error": str(e)}).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(error_msg)))
                self.end_headers()
                self.wfile.write(error_msg)
            except BrokenPipeError:
                pass

    def _send_cached(self, data: bytes, path: str, age: float,
                     content_type: str = "application/x-protobuf"):
        """Return cached response instantly."""
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Cache", "HIT")
        self.send_header("X-Cache-Age", f"{int(age)}s")
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass  # Client disconnected, ignore

    def _proxy_and_cache(self, path: str, body: bytes | None, method: str = "POST"):
        """Forward request to real Google server and cache the response."""
        # Collect headers to forward (skip hop-by-hop headers)
        # IMPORTANT: Strip Accept-Encoding to get uncompressed responses from Google.
        # AGY sends Accept-Encoding: gzip, but our HTTP/1.1 proxy can't transparently
        # handle gzip encoding. Without stripping, Google returns gzip-compressed data
        # that gets passed raw to AGY, causing "proto: syntax error" parsing failures.
        skip_headers = {
            "host", "content-length", "transfer-encoding", "connection",
            "accept-encoding",
        }
        forward_headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in skip_headers
        }

        target_url = REAL_SERVER + path

        try:
            req = urllib.request.Request(
                target_url,
                data=body,
                headers=forward_headers,
                method=method,
            )
            # Create SSL context that verifies certificates
            ctx = ssl.create_default_context()

            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                response_data = resp.read()
                response_status = resp.status
                response_headers = dict(resp.headers)

            # Cache successful responses
            if 200 <= response_status < 300:
                with _cache_lock:
                    # Store response data along with content-type for accurate replay
                    content_type = response_headers.get(
                        "Content-Type", "application/x-protobuf"
                    )
                    _cache[path] = (response_data, time.time(), content_type)
                logger.info(
                    f"[CloudCodeProxy] MISS → cached {path} "
                    f"({len(response_data)} bytes, {content_type})"
                )

            # Forward the response to AGY — preserve all headers faithfully
            self.send_response(response_status)
            # Forward key upstream response headers
            for hdr in ("Content-Type", "Content-Encoding", "Grpc-Status",
                         "Grpc-Message", "X-Goog-Upload-Status"):
                val = response_headers.get(hdr)
                if val:
                    self.send_header(hdr, val)
            self.send_header("Content-Length", str(len(response_data)))
            self.send_header("X-Cache", "MISS")
            self.end_headers()
            try:
                self.wfile.write(response_data)
            except BrokenPipeError:
                pass

        except urllib.error.HTTPError as e:
            error_body = e.read() if hasattr(e, "read") else b""
            logger.warning(
                f"[CloudCodeProxy] Upstream HTTP {e.code} for {path}: "
                f"{error_body[:200]}"
            )
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error_body)))
            self.end_headers()
            self.wfile.write(error_body)

        except Exception as e:
            logger.warning(f"[CloudCodeProxy] Upstream error for {path}: {e}")
            error_msg = json.dumps({"error": str(e)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error_msg)))
            self.end_headers()
            self.wfile.write(error_msg)


def start_proxy(port: int = PROXY_PORT) -> int:
    """Start the caching proxy server in a daemon thread.
    Returns the actual port the server is listening on.
    """
    global _server_instance, _server_thread

    if _server_instance is not None:
        return _server_instance.server_address[1]

    try:
        server = ThreadingHTTPServer((PROXY_HOST, port), CloudCodeCachingHandler)
        server.timeout = 1.0
        _server_instance = server
        actual_port = server.server_address[1]

        def _serve():
            logger.info(
                f"[CloudCodeProxy] Started on {PROXY_HOST}:{actual_port} "
                f"(cache TTL={CACHE_TTL_SEC}s)"
            )
            server.serve_forever()

        t = threading.Thread(target=_serve, daemon=True, name="CloudCodeProxy")
        t.start()
        _server_thread = t

        return actual_port

    except OSError as e:
        logger.warning(f"[CloudCodeProxy] Failed to start on port {port}: {e}")
        # Try a random port
        server = ThreadingHTTPServer((PROXY_HOST, 0), CloudCodeCachingHandler)
        _server_instance = server
        actual_port = server.server_address[1]

        def _serve():
            logger.info(
                f"[CloudCodeProxy] Started on {PROXY_HOST}:{actual_port} (fallback port)"
            )
            server.serve_forever()

        t = threading.Thread(target=_serve, daemon=True, name="CloudCodeProxy")
        t.start()
        _server_thread = t
        return actual_port


def stop_proxy():
    """Stop the proxy server."""
    global _server_instance, _server_thread
    if _server_instance:
        _server_instance.shutdown()
        _server_instance = None
        _server_thread = None
        logger.info("[CloudCodeProxy] Stopped")


def get_proxy_url(port: Optional[int] = None) -> Optional[str]:
    """Returns the proxy URL if running or given a port, else None."""
    if port:
        return f"http://{PROXY_HOST}:{port}"
    if _server_instance:
        actual_port = _server_instance.server_address[1]
        return f"http://{PROXY_HOST}:{actual_port}"
    return None


def get_cache_stats() -> dict:
    """Returns cache statistics for monitoring."""
    with _cache_lock:
        stats = {}
        now = time.time()
        for path, entry in _cache.items():
            data, ts, ct = entry
            stats[path] = {
                "size_bytes": len(data),
                "age_seconds": int(now - ts),
                "content_type": ct,
                "expired": (now - ts) >= CACHE_TTL_SEC,
            }
        return stats


# Self-test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = start_proxy()
    print(f"CloudCode Caching Proxy running on http://{PROXY_HOST}:{port}")
    print("Press Ctrl+C to stop")
    try:
        while True:
            time.sleep(60)
            stats = get_cache_stats()
            if stats:
                print(f"Cache: {json.dumps(stats, indent=2)}")
    except KeyboardInterrupt:
        stop_proxy()
