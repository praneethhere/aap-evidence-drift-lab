"""Threaded HTTP/SQLite lab target with atomic conditional writes.

Local development demonstration ONLY. Not a production service or auth boundary.
Binds to 127.0.0.1; expose temporarily using a development tunnel only.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hmac
import json
import os
from pathlib import Path
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

TARGET_ID = "lab:service-demo"
MAX_BODY = 8192


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 8000")
    return connection


@contextmanager
def open_db(path: Path):
    db = connect(path)
    try:
        yield db
    finally:
        db.close()


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_db(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS target (id TEXT PRIMARY KEY, version INTEGER NOT NULL, status TEXT NOT NULL, action_count INTEGER NOT NULL, external_count INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS completed (event_id TEXT PRIMARY KEY, resulting_version INTEGER NOT NULL, target_id TEXT NOT NULL, desired_status TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO target VALUES (?,1,'degraded',0,0)", (TARGET_ID,))


def state(db: sqlite3.Connection) -> dict:
    return dict(db.execute("SELECT id AS target_id, version, status, action_count, external_count FROM target WHERE id=?", (TARGET_ID,)).fetchone())


def reset(path: Path) -> dict:
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE target SET version=1, status='degraded', action_count=0, external_count=0 WHERE id=?", (TARGET_ID,))
        db.execute("DELETE FROM completed")
        output = state(db)
        db.execute("COMMIT")
    return output


def inject(path: Path, new_status: str) -> dict:
    if new_status not in {"degraded", "healthy"}:
        raise ValueError("status must be degraded or healthy")
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE target SET version=version+1, status=?, external_count=external_count+1 WHERE id=?", (new_status, TARGET_ID))
        output = state(db)
        db.execute("COMMIT")
    return {"disposition": "external_change", **output}


def action(path: Path, request: dict, conditional: bool) -> tuple[int, dict]:
    target_id = request.get("target_id")
    event_id = request.get("event_id")
    desired = request.get("desired_status")
    if target_id != TARGET_ID or not isinstance(event_id, str) or not (1 <= len(event_id) <= 128) or desired not in {"healthy", "degraded"}:
        return 400, {"disposition": "invalid_request", "reason": "target_id, event_id, or desired_status invalid"}
    if conditional:
        # Ansible/Jinja may serialize an integer field as a digit string.
        raw_version = request.get("expected_version")
        if type(raw_version) is int:
            expected_version = raw_version
        elif isinstance(raw_version, str) and raw_version.isascii() and raw_version.isdecimal():
            expected_version = int(raw_version)
        else:
            expected_version = -1
        if expected_version < 1 or request.get("expected_status") not in {"healthy", "degraded"}:
            return 400, {"disposition": "invalid_request", "reason": "expected_version/status required for conditional action"}
    with open_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT resulting_version, target_id, desired_status FROM completed WHERE event_id=?", (event_id,)).fetchone()
        if previous is not None:
            current = state(db)
            db.execute("COMMIT")
            if previous["target_id"] != target_id or previous["desired_status"] != desired:
                return 409, {"disposition": "event_id_conflict", "event_id": event_id}
            return 200, {"disposition": "deduplicated", "event_id": event_id, **current}
        observed = state(db)
        if conditional and (observed["version"] != expected_version or observed["status"] != request["expected_status"]):
            db.execute("COMMIT")
            return 409, {"disposition": "stale_evidence", "expected_version": expected_version, "actual_version": observed["version"], "actual_status": observed["status"], "action_count": observed["action_count"]}
        db.execute("UPDATE target SET version=version+1, status=?, action_count=action_count+1 WHERE id=?", (desired, TARGET_ID))
        new_state = state(db)
        db.execute("INSERT INTO completed VALUES (?,?,?,?)", (event_id, new_state["version"], target_id, desired))
        db.execute("COMMIT")
        return 200, {"disposition": "applied", "mode": "conditional" if conditional else "unchecked", "event_id": event_id, **new_state}


def make_handler(path: Path, token: str):
    class LabHandler(BaseHTTPRequestHandler):
        server_version = "EvidenceDriftLab/0.2"

        def log_message(self, fmt, *args):
            # Avoid accidentally emitting request auth headers or body.
            super().log_message(fmt, *args)

        def respond(self, status_code: int, payload: dict) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self) -> bool:
            candidate = self.headers.get("Authorization", "")
            if not hmac.compare_digest(candidate, f"Bearer {token}"):
                self.respond(401, {"error": "authentication_required"})
                return False
            return True

        def do_GET(self):
            name = urlsplit(self.path).path
            if name == "/health":
                self.respond(200, {"ok": True, "service": "evidence-drift-target", "schema_version": 1})
                return
            if not self.authorized():
                return
            if name == "/v1/state":
                with open_db(path) as db:
                    self.respond(200, state(db))
            else:
                self.respond(404, {"error": "not_found"})

        def do_POST(self):
            if not self.authorized():
                return
            name = urlsplit(self.path).path
            size = self.headers.get("Content-Length", "")
            try:
                length = int(size)
                if not (0 < length <= MAX_BODY):
                    raise ValueError("body size invalid")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("object required")
            except (ValueError, json.JSONDecodeError):
                self.respond(400, {"error": "invalid_json_or_body_size"})
                return
            try:
                if name == "/v1/reset":
                    self.respond(200, reset(path))
                elif name == "/v1/inject":
                    self.respond(200, inject(path, payload.get("status")))
                elif name == "/v1/actions/unchecked":
                    status_code, result = action(path, payload, False)
                    self.respond(status_code, result)
                elif name == "/v1/actions/conditional":
                    status_code, result = action(path, payload, True)
                    self.respond(status_code, result)
                else:
                    self.respond(404, {"error": "not_found"})
            except (sqlite3.Error, ValueError):
                self.respond(400, {"error": "invalid_operation"})

    return LabHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(".edlab-local/state.sqlite3"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    secret = os.environ.get("EDLAB_TOKEN", "")
    if len(secret) < 24:
        parser.error("Set EDLAB_TOKEN to a random secret of at least 24 characters")
    initialize(args.db)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.db, secret))
    print(f"Lab target listening on http://127.0.0.1:{args.port}; DB={args.db}", flush=True)
    print("Only /health is public. Protected endpoints require Bearer token.", flush=True)
    try:
        httpd.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
