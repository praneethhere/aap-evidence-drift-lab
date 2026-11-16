"""Local API contract tests; does NOT contact AAP."""
import json
import unittest
from scripts.aap_controller import ControllerAPI, APIError, PLAYBOOKS, WORKFLOWS, ensure_template, ensure_workflow


class FakeAAP:
    def __init__(self):
        self.requests = []
        self.resources = {"job_templates": {}, "workflow_job_templates": {}}
        self.objects = {}
        self.sequence = 50

    def exact(self, resource, name):
        return self.resources[resource].get(name)

    def call(self, method, endpoint, body=None):
        self.requests.append((method, endpoint, body))
        if method == "POST" and endpoint in ("job_templates/", "workflow_job_templates/"):
            self.sequence += 1
            obj = {**body, "id": self.sequence}
            self.resources[endpoint.rstrip("/")][obj["name"]] = obj
            return obj
        if method == "POST" and endpoint == "workflow_job_template_nodes/":
            self.sequence += 1
            return {**body, "id": self.sequence}
        if method == "POST":
            return {}
        if method == "GET" and endpoint.endswith("/credentials/"):
            return {"results": []}
        return {}


class AAPProvisionTests(unittest.TestCase):
    def test_controller_requires_gateway_https(self):
        with self.assertRaises(ValueError):
            ControllerAPI("http://aap.test", "not-a-real-token")
        with self.assertRaises(ValueError):
            ControllerAPI("https://aap.test/path", "not-a-real-token")
        with self.assertRaises(ValueError):
            ControllerAPI("https://aap.test", "")

    def test_expected_aap_27_root(self):
        api = ControllerAPI("https://gateway.example.test", "placeholder-token")
        self.assertEqual(api.base, "https://gateway.example.test/api/controller/v2/")

    def test_dry_run_provisions_nothing(self):
        api = FakeAAP()
        for k, (name, path) in PLAYBOOKS.items():
            self.assertIsNone(ensure_template(api, name, path, project=1, inventory=2,
                                              credential=3, gateway_url="https://test.example", apply=False))
        self.assertIsNone(ensure_workflow(api, "guarded", {}, organization=4, inventory=2, apply=False))
        self.assertEqual(api.requests, [])

    def test_workflows_have_six_nodes_and_two_approvals(self):
        api = FakeAAP()
        ids = {}
        for key, (name, path) in PLAYBOOKS.items():
            ids[key] = ensure_template(api, name, path, project=1, inventory=2,
                                       credential=3, gateway_url="https://lab.example", apply=True)
        self.assertEqual(len(ids), 5)
        for mode in WORKFLOWS:
            ensure_workflow(api, mode, ids, organization=4, inventory=2, apply=True)
        nodes = [r for r in api.requests if r[1] == "workflow_job_template_nodes/"]
        approvals = [r for r in api.requests if "create_approval_template/" in r[1]]
        links = [r for r in api.requests if "success_nodes/" in r[1]]
        self.assertEqual(len(nodes), 12)
        self.assertEqual(len(approvals), 4)
        self.assertEqual(len(links), 10)
        self.assertEqual({n[2]["identifier"] for n in nodes},
                         {"capture", "approve_capture", "precheck", "experimental_hold", "action", "verify"})

    def test_created_job_templates_use_correct_playbooks(self):
        api = FakeAAP()
        name, playbook = PLAYBOOKS["guarded"]
        ensure_template(api, name, playbook, project=11, inventory=12, credential=13,
                        gateway_url="https://lab.example", apply=True)
        request = next(x for x in api.requests if x[1] == "job_templates/")
        self.assertEqual(request[2]["playbook"], playbook)
        self.assertEqual(json.loads(request[2]["extra_vars"])["lab_api_url"], "https://lab.example")
        self.assertTrue(any(x[1].endswith("/credentials/") and x[2]["id"] == 13 for x in api.requests))

if __name__ == "__main__":
    unittest.main()
