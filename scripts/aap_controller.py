"""AAP platform-gateway REST client for test-only workflow provisioning.

Only the 'provision --apply', 'launch', and 'approve' commands write to AAP.
No authentication material is printed or saved. AAP 2.7 uses the platform
gateway and /api/controller/v2/, not direct component endpoints.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "/api/controller/v2/"
PLAYBOOKS = {
    "capture": ("EDK - Kubernetes capture", "ansible/aap_sandbox/09_k8s_capture.yml"),
    "precheck": ("EDK - Kubernetes precheck", "ansible/aap_sandbox/10_k8s_precheck.yml"),
    "unchecked": ("EDK - Kubernetes unchecked scale", "ansible/aap_sandbox/11_k8s_unchecked.yml"),
    "guarded": ("EDK - Kubernetes guarded scale", "ansible/aap_sandbox/12_k8s_conditional.yml"),
    "verify": ("EDK - Kubernetes final verification", "ansible/aap_sandbox/13_k8s_verify.yml"),
}
WORKFLOWS = {
    "unchecked": "EDK - Unchecked idempotent scale comparison",
    "guarded": "EDK - Native guarded scale comparison",
}

class APIError(RuntimeError):
    pass

class ControllerAPI:
    def __init__(self, base_url, token, transport=None):
        url = urllib.parse.urlparse(base_url)
        if url.scheme != "https" or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise ValueError("AAP_BASE_URL must be the HTTPS platform gateway origin, not a component URL")
        if not token:
            raise ValueError("AAP_TOKEN must be set locally in the shell")
        self.base = f"https://{url.netloc}{API_ROOT}"
        self.token = token
        self.transport = transport or urllib.request.urlopen

    def call(self, method, endpoint, payload=None):
        if endpoint.startswith("http") or ".." in endpoint:
            raise ValueError("Only relative controller endpoints accepted")
        url = self.base + endpoint.lstrip("/")
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            response = self.transport(req, timeout=25)
            with response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            # Avoid printing returned controller HTML or potentially sensitive data.
            raise APIError(f"AAP {method} {endpoint.split('?')[0]} returned HTTP {exc.code}; inspect account RBAC/API schema") from exc
        except urllib.error.URLError as exc:
            raise APIError(f"AAP platform gateway unavailable: {exc.reason}") from exc

    def list(self, resource, **params):
        query = "?" + urllib.parse.urlencode(params) if params else ""
        data = self.call("GET", resource.rstrip("/") + "/" + query)
        if not isinstance(data, dict) or "results" not in data:
            raise APIError(f"Unexpected list schema for {resource}")
        return data["results"]

    def exact(self, resource, name):
        matching = [x for x in self.list(resource, name=name) if x.get("name") == name]
        if len(matching) > 1:
            raise APIError(f"More than one object named {name!r}; supply unique lab names")
        return matching[0] if matching else None


def ensure_template(api, name, playbook, *, project, inventory, credential, gateway_url, apply):
    existing = api.exact("job_templates", name)
    if existing:
        if existing.get("playbook") != playbook or int(existing.get("project") or 0) != int(project):
            raise APIError(f"Conflicting existing job template {name!r}; refusing to repurpose")
        if apply:
            try:
                prior = json.loads(existing.get("extra_vars") or "{}")
            except (TypeError, ValueError):
                prior = {}
            if prior.get("lab_api_url") != gateway_url:
                api.call("PATCH", f"job_templates/{existing['id']}/",
                         {"extra_vars": json.dumps({"lab_api_url": gateway_url})})
                print(f"UPDATED HTTPS tunnel URL for template id={existing['id']}")
            credentials = api.call("GET", f"job_templates/{existing['id']}/credentials/").get("results", [])
            if credential not in {c.get("id") for c in credentials}:
                api.call("POST", f"job_templates/{existing['id']}/credentials/", {"id": credential})
        return existing["id"]
    if not apply:
        print(f"PLAN create job template {name} -> {playbook}")
        return None
    body = {"name": name, "job_type": "run", "inventory": inventory, "project": project,
            "playbook": playbook, "verbosity": 0, "extra_vars": json.dumps({"lab_api_url": gateway_url})}
    created = api.call("POST", "job_templates/", body)
    template_id = created["id"]
    api.call("POST", f"job_templates/{template_id}/credentials/", {"id": credential})
    print(f"CREATED job template {name}, id={template_id}")
    return template_id


def ensure_workflow(api, mode, template_ids, *, organization, inventory, apply):
    name = WORKFLOWS[mode]
    found = api.exact("workflow_job_templates", name)
    if found:
        print(f"EXISTS workflow {name}, id={found['id']}; review graph before using")
        return found["id"]
    if not apply:
        print(f"PLAN create {name}: capture -> approval -> precheck -> hold -> {mode} -> verify")
        return None
    wf = api.call("POST", "workflow_job_templates/", {"name": name, "organization": organization,
                                                        "inventory": inventory, "ask_variables_on_launch": False})
    workflow_id = wf["id"]
    chain = [("capture", "job", template_ids["capture"]),
             ("approve_capture", "approval", None),
             ("precheck", "job", template_ids["precheck"]),
             ("experimental_hold", "approval", None),
             ("action", "job", template_ids[mode]),
             ("verify", "job", template_ids["verify"])]
    node_ids = []
    for identifier, node_type, job_template_id in chain:
        node_body = {"workflow_job_template": workflow_id, "identifier": identifier}
        if node_type == "job":
            node_body["unified_job_template"] = job_template_id
        node = api.call("POST", "workflow_job_template_nodes/", node_body)
        node_id = node["id"]
        if node_type == "approval":
            api.call("POST", f"workflow_job_template_nodes/{node_id}/create_approval_template/",
                     {"name": f"EDK {identifier} - {mode}", "timeout": 7200})
        node_ids.append(node_id)
    for previous, following in zip(node_ids, node_ids[1:]):
        api.call("POST", f"workflow_job_template_nodes/{previous}/success_nodes/", {"id": following})
    print(f"CREATED workflow {name}, id={workflow_id}, 6 nodes")
    return workflow_id


def print_json(obj):
    print(json.dumps(obj, indent=2, sort_keys=True))


def main(argv=None):
    p = argparse.ArgumentParser(description="AAP 2.7 gateway API helper; token from AAP_TOKEN environment variable")
    c = p.add_subparsers(dest="command", required=True)
    c.add_parser("doctor")
    sync = c.add_parser("sync-project")
    sync.add_argument("--project-id", type=int, required=True)
    cp = c.add_parser("provision")
    for arg in ("project-id", "inventory-id", "credential-id", "organization-id"):
        cp.add_argument("--" + arg, type=int, required=True)
    cp.add_argument("--gateway-url", required=True, help="HTTPS tunnel URL for restricted Kubernetes gateway")
    cp.add_argument("--apply", action="store_true", help="Create AAP objects; default only plans changes")
    launch = c.add_parser("launch")
    launch.add_argument("--workflow-id", type=int, required=True)
    status = c.add_parser("status")
    status.add_argument("--workflow-job-id", type=int, required=True)
    approve = c.add_parser("approve")
    approve.add_argument("--approval-id", type=int, required=True)
    approvals = c.add_parser("approvals")
    approvals.add_argument("--workflow-job-id", type=int, required=True)
    args = p.parse_args(argv)
    try:
        api = ControllerAPI(os.environ.get("AAP_BASE_URL", ""), os.environ.get("AAP_TOKEN", ""))
        if args.command == "doctor":
            ping = api.call("GET", "ping/")
            print_json({"controller_ping": ping, "projects": [{"id": x["id"], "name": x["name"]}
                    for x in api.list("projects")], "inventories": [{"id": x["id"], "name": x["name"]}
                    for x in api.list("inventories")], "credentials": [{"id": x["id"], "name": x["name"]}
                    for x in api.list("credentials")], "organizations": [{"id": x["id"], "name": x["name"]}
                    for x in api.list("organizations")]})
        elif args.command == "sync-project":
            print_json(api.call("POST", f"projects/{args.project_id}/update/", {}))
        elif args.command == "provision":
            if not (args.gateway_url.startswith("https://") and "/" not in args.gateway_url.removeprefix("https://").rstrip("/")):
                raise ValueError("--gateway-url must be HTTPS origin without a path")
            ids = {k: ensure_template(api, name, path, project=args.project_id, inventory=args.inventory_id,
                                      credential=args.credential_id, gateway_url=args.gateway_url.rstrip("/"), apply=args.apply)
                   for k, (name, path) in PLAYBOOKS.items()}
            for mode in ("unchecked", "guarded"):
                ensure_workflow(api, mode, ids, organization=args.organization_id,
                                inventory=args.inventory_id, apply=args.apply)
            if not args.apply:
                print("DRY RUN ONLY. Re-run with --apply after project sync and verifying permissions.")
        elif args.command == "launch":
            print_json(api.call("POST", f"workflow_job_templates/{args.workflow_id}/launch/", {}))
        elif args.command == "status":
            job = api.call("GET", f"workflow_jobs/{args.workflow_job_id}/")
            nodes = api.list(f"workflow_jobs/{args.workflow_job_id}/workflow_nodes")
            print_json({"workflow_job_id": job.get("id"), "status": job.get("status"),
                        "nodes": [{"identifier": x.get("identifier"), "id": x.get("id"),
                                   "job": x.get("job"), "type": x.get("job_type")}
                                  for x in nodes]})
        elif args.command == "approvals":
            nodes = api.list(f"workflow_jobs/{args.workflow_job_id}/workflow_nodes")
            items = []
            for node in nodes:
                if node.get("job_type") == "workflow_approval" and node.get("job"):
                    approval = api.call("GET", f"workflow_approvals/{node['job']}/")
                    items.append({"identifier": node.get("identifier"), "approval_id": approval.get("id"),
                                  "status": approval.get("status")})
            print_json(items)
        elif args.command == "approve":
            print_json(api.call("POST", f"workflow_approvals/{args.approval_id}/approve/", {}))
        return 0
    except (ValueError, APIError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
