"""Limited localhost-only adapter for AAP's authenticated HTTPS-to-tunnel calls.

IMPORTANT: AAP talks to this gateway, NOT directly to Kubernetes. Kubernetes
itself enforces --resource-version and --current-replicas via kubectl scale.
Do not deploy or use for production clusters.
"""
from __future__ import annotations
import argparse
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .cluster_lab import KubeError, KubeLab, is_precondition_conflict


def handler_factory(lab, token):
    token_bytes = token.encode('utf-8')

    class Handler(BaseHTTPRequestHandler):
        server_version = "EDKRestrictedGateway/1.0"

        def log_message(self, fmt, *args):
            # Never log authorization headers or request bodies.
            sys.stderr.write("edk-gateway request completed\n")

        def reply(self, status, data):
            contents = json.dumps(data, separators=(",", ":")).encode('utf-8')
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(contents)))
            self.end_headers()
            self.wfile.write(contents)

        def authorized(self):
            auth = self.headers.get("Authorization", "")
            supplied = auth[7:] if auth.startswith("Bearer ") else ""
            if not hmac.compare_digest(supplied.encode('utf-8'), token_bytes):
                self.reply(401, {"error": "unauthorized"})
                return False
            return True

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, {"status": "ok", "component": "restricted Kubernetes scale adapter"})
                return
            if self.path != "/v1/state":
                self.reply(404, {"error": "not_found"})
                return
            if not self.authorized():
                return
            try:
                self.reply(200, lab.state())
            except Exception:
                self.reply(502, {"error": "backend_unavailable"})

        def do_POST(self):
            if self.path not in ("/v1/actions/unchecked", "/v1/actions/conditional", "/v1/actions/replica-guarded"):
                self.reply(404, {"error": "not_found"})
                return
            if not self.authorized():
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                self.reply(415, {"error": "json_required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 2048:
                    self.reply(413, {"error": "invalid_size"})
                    return
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("body must be a JSON object")
                # Only fixed action target, fixed namespace and 3 replicas. No user-provided resource paths.
                if self.path == "/v1/actions/conditional":
                    if set(data) != {"expected_resource_version"} or not isinstance(data["expected_resource_version"], str):
                        raise ValueError("one expected_resource_version string required")
                    rv = data["expected_resource_version"]
                    if not rv.isdigit() or len(rv) > 32:
                        raise ValueError("invalid resource version")
                    state = lab.scale(3, resource_version=rv)
                elif self.path == "/v1/actions/replica-guarded":
                    if data != {"expected_replicas": 2}:
                        raise ValueError("expected_replicas must be 2")
                    state = lab.scale(3, current_replicas=2)
                else:
                    if data != {}:
                        raise ValueError("unchecked action accepts only empty JSON object")
                    state = lab.scale(3)
                self.reply(200, {"disposition": "applied", "native_kubernetes": True, **state})
            except (ValueError, json.JSONDecodeError) as exc:
                self.reply(400, {"error": "bad_request", "detail": str(exc)[:180]})
            except KubeError as exc:
                if is_precondition_conflict(exc):
                    try:
                        current = lab.state()
                    except Exception:
                        self.reply(502, {"error": "unable_to_verify_rejection"})
                        return
                    self.reply(409, {"disposition": "stale_precondition", "native_kubernetes": True, **current})
                else:
                    self.reply(502, {"error": "kubernetes_request_failed", "detail": exc.detail[:180]})
            except Exception:
                self.reply(502, {"error": "backend_unavailable"})

    return Handler


def main(argv=None):
    p = argparse.ArgumentParser(description="127.0.0.1-only HTTPS-tunnel adapter; disposable demo only")
    p.add_argument("--context", required=True)
    p.add_argument("--port", type=int, default=8877)
    args = p.parse_args(argv)
    token = os.environ.get("EDLAB_TOKEN", "")
    if len(token) < 32:
        p.error("EDLAB_TOKEN must be a random secret of at least 32 characters")
    if not (1024 <= args.port <= 65535):
        p.error("choose a nonprivileged TCP port")
    lab = KubeLab(args.context)
    lab.assert_owned()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), handler_factory(lab, token))
    print(f"Gateway bound to 127.0.0.1:{args.port}; context={args.context}; namespace=edlab-cas-lab", flush=True)
    print("Disposable lab only. End this process and any tunnel after experiments.", flush=True)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()

if __name__ == "__main__":
    main()
