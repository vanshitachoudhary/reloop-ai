#!/usr/bin/env python3
"""Local dev server: serves the frontend and runs the real Lambda handler against an in-memory table.
S3 is faked. Amazon Rekognition is real only if boto3 is installed and AWS credentials are configured
(`aws configure`); otherwise /scan returns an error and the UI falls back to manual category selection.
Usage: python scripts/dev_server.py [port]"""
import os, sys, types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ.update(TABLE="local", BUCKET="local")


class Table:
    def __init__(self): self.items = {}
    def put_item(self, Item): self.items[Item["id"]] = dict(Item)
    def get_item(self, Key): return {"Item": self.items.get(Key["id"])}
    def scan(self, Limit=200): return {"Items": list(self.items.values())[:Limit]}
    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
        item = self.items[Key["id"]]
        for part in UpdateExpression[4:].split(","):
            k, v = [x.strip() for x in part.split("=")]
            item[k] = ExpressionAttributeValues[v]


class FakeS3:
    def put_object(self, **kw): pass


try:
    import boto3 as _real
except ImportError:
    _real = None


def _rek():
    if _real:
        try:
            return _real.client("rekognition")
        except Exception:
            pass
    class Off:
        def detect_labels(self, **kw): raise RuntimeError("Rekognition not available locally")
    return Off()


TABLE = Table()
sys.modules["boto3"] = types.SimpleNamespace(
    client=lambda n, *a, **k: FakeS3() if n == "s3" else _rek(),
    resource=lambda *a, **k: types.SimpleNamespace(Table=lambda n: TABLE))
sys.path.insert(0, str(ROOT / "backend"))
import app  # noqa: E402

TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}


class H(BaseHTTPRequestHandler):
    def _send(self, code, data, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _api(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        r = app.handler({"requestContext": {"http": {"method": method}},
                         "rawPath": self.path.split("?")[0][4:], "body": body}, None)
        self._send(r["statusCode"], r["body"].encode())

    def do_GET(self):
        p = self.path.split("?")[0]
        if p.startswith("/api/"): return self._api("GET")
        if p == "/config.js": return self._send(200, b'window.RELOOP_API = location.origin + "/api"; window.RELOOP_LOCAL = true;', "text/javascript")
        f = (ROOT / "frontend" / ("index.html" if p == "/" else p.lstrip("/"))).resolve()
        if (ROOT / "frontend") not in f.parents or not f.is_file(): return self._send(404, b"not found", "text/plain")
        self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))

    def do_POST(self): self._api("POST") if self.path.startswith("/api/") else self._send(404, b"{}")
    def do_PATCH(self): self._api("PATCH") if self.path.startswith("/api/") else self._send(404, b"{}")
    def log_message(self, *a): pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "127.0.0.1")  # set HOST=0.0.0.0 on Render/Railway
    print(f"ReLoop AI dev server: http://{host}:{port}  (Ctrl+C to stop)")
    ThreadingHTTPServer((host, port), H).serve_forever()
