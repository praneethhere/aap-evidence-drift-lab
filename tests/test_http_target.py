import concurrent.futures
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

from target.server import TARGET_ID, initialize, make_handler


class TargetHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "lab.db"
        initialize(self.db)
        self.token = "this-is-test-only-key-with-ample-length"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.db, self.token))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, path, data=None, authorized=True):
        headers = {}
        if authorized:
            headers["Authorization"] = "Bearer " + self.token
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data is not None else None, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=3) as res:
                return res.status, json.load(res)
        except urllib.error.HTTPError as err:
            return err.code, json.load(err)

    def action_body(self, event_id="one", expected_version=1):
        return {"target_id": TARGET_ID, "event_id": event_id, "desired_status": "healthy", "expected_status": "degraded", "expected_version": expected_version}

    def test_health_unauthed(self):
        status, body = self.request("/health", authorized=False)
        self.assertEqual(status, 200)
        self.assertEqual(body["service"], "evidence-drift-target")

    def test_state_requires_auth(self):
        self.assertEqual(self.request("/v1/state", authorized=False)[0], 401)

    def test_initial_state(self):
        status, body = self.request("/v1/state")
        self.assertEqual((status, body["version"], body["status"], body["action_count"]), (200, 1, "degraded", 0))

    def test_reset(self):
        self.request("/v1/inject", {"status": "healthy"})
        status, body = self.request("/v1/reset", {})
        self.assertEqual((status, body["version"], body["status"]), (200, 1, "degraded"))

    def test_unchecked_stale_mutates(self):
        self.request("/v1/inject", {"status": "healthy"})
        status, body = self.request("/v1/actions/unchecked", self.action_body())
        self.assertEqual(status, 200)
        self.assertEqual(body["action_count"], 1)
        self.assertEqual(body["version"], 3)

    def test_conditional_stale_rejected(self):
        self.request("/v1/inject", {"status": "healthy"})
        status, body = self.request("/v1/actions/conditional", self.action_body())
        self.assertEqual(status, 409)
        self.assertEqual(body["disposition"], "stale_evidence")
        self.assertEqual(self.request("/v1/state")[1]["action_count"], 0)

    def test_conditional_success(self):
        status, body = self.request("/v1/actions/conditional", self.action_body())
        self.assertEqual((status, body["status"], body["action_count"], body["version"]), (200, "healthy", 1, 2))

    def test_digit_string_version_from_ansible(self):
        self.assertEqual(self.request("/v1/actions/conditional", self.action_body(expected_version="1"))[0], 200)

    def test_duplicate_event_with_different_payload_rejected(self):
        self.request("/v1/actions/conditional", self.action_body())
        reused = self.action_body()
        reused["desired_status"] = "degraded"
        status, output = self.request("/v1/actions/conditional", reused)
        self.assertEqual((status, output["disposition"]), (409, "event_id_conflict"))
        self.assertEqual(self.request("/v1/state")[1]["action_count"], 1)

    def test_duplicate_deduplicated(self):
        self.request("/v1/actions/conditional", self.action_body())
        status, body = self.request("/v1/actions/conditional", self.action_body())
        self.assertEqual((status, body["disposition"], body["action_count"]), (200, "deduplicated", 1))

    def test_external_change_can_break_postcondition(self):
        self.request("/v1/actions/conditional", self.action_body())
        self.request("/v1/inject", {"status": "degraded"})
        self.assertEqual(self.request("/v1/state")[1]["status"], "degraded")

    def test_precheck_alone_cannot_guard_later_mutation(self):
        before = self.request("/v1/state")[1]
        self.assertEqual(before["version"], 1)
        self.request("/v1/inject", {"status": "healthy"})
        status, after = self.request("/v1/actions/unchecked", self.action_body())
        self.assertEqual((status, after["action_count"]), (200, 1))

    def test_bad_request(self):
        self.assertEqual(self.request("/v1/actions/conditional", {"event_id": "x"})[0], 400)

    def test_atomic_same_version_two_competing_events(self):
        def try_act(event):
            return self.request("/v1/actions/conditional", self.action_body(event_id=event))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(try_act, ["competing-one", "competing-two"]))
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        self.assertEqual(self.request("/v1/state")[1]["action_count"], 1)


if __name__ == "__main__":
    unittest.main()
