"""Unit and contract tests using an in-memory fake kubectl runner.

These are NOT substitutes for actually running Kubernetes or AAP.
"""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from k8s_native.cluster_lab import KubeLab, KubeError, NS, DEPLOY, LABEL, OWNER, run_matrix, is_precondition_conflict, capture_stable_precheck
from k8s_native.gateway import handler_factory


class FakeKubectl:
    def __init__(self):
        self.ns = False
        self.dep = False
        self.rv = 10
        self.replicas = 2
        self.log = []

    def __call__(self, cmd, input_text=None):
        self.log.append((list(cmd), input_text))
        if cmd[:3] != ["kubectl", "--context", "kind-edlab-scale"]:
            raise AssertionError("kubectl context should be explicit")
        a = cmd[3:]
        if a[:2] == ["-n", NS]:
            a = a[2:]
        if a[:4] == ["config", "get-contexts", "-o", "name"]:
            return "kind-edlab-scale\n"
        if a == ["version", "-o", "json"]:
            return json.dumps({"serverVersion": {"gitVersion": "v1.fake"}})
        if a[:2] == ["get", "namespace"]:
            if not self.ns:
                raise KubeError(cmd, 1, "NotFound")
            return json.dumps({"metadata": {"labels": {LABEL: OWNER}}})
        if a[:2] == ["get", "deployment"]:
            if not self.dep:
                raise KubeError(cmd, 1, "NotFound")
            return json.dumps({"metadata": {"labels": {LABEL: OWNER}}})
        if a[:2] == ["create", "-f"]:
            obj = json.loads(input_text)
            if obj["kind"] == "Namespace":
                self.ns = True
            if obj["kind"] == "Deployment":
                self.dep = True
            return "created\n"
        if a[:2] == ["get", "--raw"]:
            return json.dumps({"metadata": {"resourceVersion": str(self.rv), "uid": "uid-demo"},
                               "spec": {"replicas": self.replicas}})
        if a and a[0] == "scale":
            desired = int(next(x.split("=", 1)[1] for x in a if x.startswith("--replicas=")))
            version = next((x.split("=", 1)[1] for x in a if x.startswith("--resource-version=")), None)
            expected = next((int(x.split("=", 1)[1]) for x in a if x.startswith("--current-replicas=")), None)
            if version is not None and int(version) != self.rv:
                raise KubeError(cmd, 1, "Error from server (Conflict): precondition resourceVersion does not match")
            if expected is not None and expected != self.replicas:
                raise KubeError(cmd, 1, "Precondition failed: expected replicas do not match")
            if desired != self.replicas:
                self.rv += 1
            self.replicas = desired
            return "scaled\n"
        if a[:2] == ["delete", "deployment"]:
            self.dep = False
            return "deleted\n"
        raise AssertionError(f"unhandled mocked kubectl invocation: {cmd}")


class OneShotNoInjectionStatusChurn(FakeKubectl):
    """Simulates the API changing resourceVersion on a status-only update."""
    def __init__(self):
        super().__init__()
        self.force_one_conflict = True

    def __call__(self, cmd, input_text=None):
        if (self.force_one_conflict and "scale" in cmd and
                any(x.startswith("--resource-version=") for x in cmd)):
            self.rv += 1  # No change to spec.replicas, only the object version.
            self.force_one_conflict = False
        return super().__call__(cmd, input_text)


class RealKubectlMessageTests(unittest.TestCase):
    def _error(self, message, flag="--resource-version=1430"):
        return KubeError(["kubectl", "--context", "kind-edlab-scale", "-n", NS,
                          "scale", f"deployment/{DEPLOY}", "--replicas=3", flag], 1, message)

    def test_exact_real_kubectl_resource_version_message(self):
        self.assertTrue(is_precondition_conflict(self._error(
            "error: Expected resource version to be 1430, was 1455")))

    def test_exact_replica_count_message(self):
        self.assertTrue(is_precondition_conflict(self._error(
            "error: Expected replicas to be 2, was 4", flag="--current-replicas=2")))

    def test_unguarded_and_non_scale_errors_are_not_expected_conflicts(self):
        self.assertFalse(is_precondition_conflict(KubeError(
            ["kubectl", "--context", "kind-edlab-scale", "-n", NS, "scale",
             f"deployment/{DEPLOY}", "--replicas=3"], 1,
            "error: Expected resource version to be 1430, was 1455")))
        self.assertFalse(is_precondition_conflict(KubeError(
            ["kubectl", "--context", "kind-edlab-scale", "get", "pods", "--resource-version=1430"],
            1, "error: Expected resource version to be 1430, was 1455")))

    def test_rbac_network_and_auth_failures_are_not_expected_conflicts(self):
        for msg in ["Error from server (Forbidden): User cannot update deployments/scale",
                    "Unable to connect to the server: connection refused",
                    "error: the server doesn't have a resource type 'deployment'",
                    "request timeout: context deadline exceeded"]:
            with self.subTest(msg=msg):
                self.assertFalse(is_precondition_conflict(self._error(msg)))

    def test_control_retries_real_kubectl_version_message(self):
        class RealMessageChurn(OneShotNoInjectionStatusChurn):
            def __call__(self, cmd, input_text=None):
                try:
                    return super().__call__(cmd, input_text)
                except KubeError as exc:
                    if "resourceVersion does not match" in exc.detail:
                        raise KubeError(cmd, 1, f"error: Expected resource version to be 10, was {self.rv}") from exc
                    raise
        fake = RealMessageChurn()
        lab = KubeLab("kind-edlab-scale", runner=fake)
        lab.init()
        results = run_matrix(lab, settle=False)
        self.assertEqual(len(results["cases"]), 6)
        self.assertEqual(len(results["cases"][0]["no_drift_retry_conflicts"]), 1)
        self.assertEqual(results["cases"][2]["outcome"], "rejected-no-overwrite")


class AmbientVersionChurnTests(unittest.TestCase):
    def test_capture_precheck_recaptures_only_before_injection(self):
        class OneChange:
            rv = 10
            changed = False
            def reset(self):
                return {"replicas": 2, "resource_version": str(self.rv), "uid": "stable-uid"}
            def state(self):
                if not self.changed:
                    self.rv += 1  # Controller-style status update, not an injection.
                    self.changed = True
                return {"replicas": 2, "resource_version": str(self.rv), "uid": "stable-uid"}

        example = OneChange()
        capture, checked, changes = capture_stable_precheck(example, example.reset, wait=0)
        self.assertEqual(capture["resource_version"], "11")
        self.assertEqual(checked["resource_version"], "11")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["captured_version"], "10")

    def test_rejects_unstable_capture_instead_of_claiming_experimental_drift(self):
        class RepeatedChange:
            rv = 10
            def reset(self):
                return {"replicas": 2, "resource_version": str(self.rv), "uid": "stable-uid"}
            def state(self):
                self.rv += 1
                return {"replicas": 2, "resource_version": str(self.rv), "uid": "stable-uid"}

        example = RepeatedChange()
        with self.assertRaisesRegex(RuntimeError, "Before external injection"):
            capture_stable_precheck(example, example.reset, attempts=3, wait=0)

    def test_control_retries_one_background_status_conflict(self):
        fake = OneShotNoInjectionStatusChurn()
        lab = KubeLab("kind-edlab-scale", runner=fake)
        lab.init()
        observed = run_matrix(lab, settle=False)
        self.assertEqual(len(observed["cases"][0]["no_drift_retry_conflicts"]), 1)
        self.assertEqual(observed["cases"][0]["outcome"], "applied")
        self.assertEqual(observed["cases"][2]["outcome"], "rejected-no-overwrite")


class ScaleLabTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKubectl()
        self.lab = KubeLab("kind-edlab-scale", runner=self.fake)
        self.lab.init()

    def test_init_creates_only_scoped_resources(self):
        self.assertTrue(self.fake.ns)
        self.assertTrue(self.fake.dep)
        self.assertEqual(self.lab.state()["replicas"], 2)
        all_created = [json.loads(body) for c, body in self.fake.log if body and "create" in c]
        self.assertEqual({x["kind"] for x in all_created}, {"Namespace", "Deployment"})

    def test_paired_experiment_on_mocked_kubectl(self):
        outcome = run_matrix(self.lab, settle=False)
        self.assertEqual([c["outcome"] for c in outcome["cases"]],
                         ["applied", "overwrote-newer-intent", "rejected-no-overwrite", "rejected-no-overwrite", "accepted-stale-value-match", "rejected-history-changed"])
        self.assertEqual([c["final"]["replicas"] for c in outcome["cases"]], [3, 3, 4, 4, 3, 2])
        self.assertEqual(self.fake.replicas, 2)
        self.assertTrue(any("--resource-version=" in " ".join(args) for args, _ in self.fake.log))
        self.assertTrue(any("--current-replicas=2" in args for args, _ in self.fake.log))

    def test_guarded_without_drift_applies(self):
        state = self.lab.state()
        updated = self.lab.scale(3, resource_version=state["resource_version"])
        self.assertEqual(updated["replicas"], 3)

    def test_stale_version_rejected(self):
        original = self.lab.state()
        self.lab.inject()
        with self.assertRaises(KubeError) as error:
            self.lab.scale(3, resource_version=original["resource_version"])
        self.assertTrue(is_precondition_conflict(error.exception))
        self.assertEqual(self.lab.state()["replicas"], 4)

    def test_invalid_context_rejected(self):
        with self.assertRaises(ValueError):
            KubeLab("", runner=self.fake)

    def test_out_of_scope_replica_rejected(self):
        with self.assertRaises(ValueError):
            self.lab.scale(500)

    def test_no_context_override(self):
        self.lab.scale(3)
        self.assertTrue(all(a[2] == "kind-edlab-scale" for a, _ in self.fake.log))

    def test_cleanup_preserves_namespace(self):
        self.lab.delete_deployment()
        self.assertTrue(self.fake.ns)
        self.assertFalse(self.fake.dep)


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeKubectl()
        cls.lab = KubeLab("kind-edlab-scale", runner=cls.fake)
        cls.lab.init()
        cls.token = "X" * 40
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(cls.lab, cls.token))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, path, data=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}{path}",
                                     data=json.dumps(data).encode() if data is not None else None,
                                     headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=4) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def setUp(self):
        self.lab.reset()

    def test_gateway_auth_required(self):
        code, _ = self.request("/v1/state")
        self.assertEqual(code, 401)
        code, response = self.request("/v1/state", token=self.token)
        self.assertEqual(code, 200)
        self.assertEqual(response["replicas"], 2)

    def test_gateway_native_conditional_rejects_stale(self):
        _, before = self.request("/v1/state", token=self.token)
        self.lab.inject()
        code, response = self.request("/v1/actions/conditional",
                                      {"expected_resource_version": before["resource_version"]}, token=self.token)
        self.assertEqual(code, 409)
        self.assertEqual(response["replicas"], 4)
        self.assertEqual(self.lab.state()["replicas"], 4)

    def test_gateway_unchecked_overwrites(self):
        self.lab.inject()
        code, response = self.request("/v1/actions/unchecked", {}, token=self.token)
        self.assertEqual(code, 200)
        self.assertEqual(response["replicas"], 3)

    def test_gateway_rejects_arbitrary_resource(self):
        code, _ = self.request("/v1/actions/unchecked", {"namespace": "default"}, token=self.token)
        self.assertEqual(code, 400)
        self.assertEqual(self.lab.state()["replicas"], 2)

    def test_gateway_rejects_unauthorized_mutation(self):
        code, _ = self.request("/v1/actions/unchecked", {}, token="bad")
        self.assertEqual(code, 401)
        self.assertEqual(self.lab.state()["replicas"], 2)

if __name__ == "__main__":
    unittest.main()
