"""Isolated Kubernetes scale/version experiment using kubectl and the real API.

All writes target a labelled Deployment in a dedicated lab namespace.
No kubeconfig, service-account token, or Kubernetes API server is exposed.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

NS = "edlab-cas-lab"
DEPLOY = "replica-intent"
LABEL = "app.kubernetes.io/managed-by"
OWNER = "evidence-drift-lab"
IMAGE = "registry.k8s.io/pause:3.10"

class KubeError(RuntimeError):
    def __init__(self, command, code, detail):
        self.command = list(command)
        self.code = code
        self.detail = detail
        super().__init__(f"kubectl exit={code}: {detail[:700]}")


def subprocess_runner(cmd, input_text=None):
    result = subprocess.run(cmd, input=input_text, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=35, check=False)
    if result.returncode:
        raise KubeError(cmd, result.returncode, result.stderr or result.stdout)
    return result.stdout


@dataclass
class KubeLab:
    context: str
    runner: Callable = subprocess_runner

    def __post_init__(self):
        if not self.context or not self.context.strip():
            raise ValueError("Explicit --context is required")

    def kubectl(self, *args, input_text=None, namespace=True):
        cmd = ["kubectl", "--context", self.context]
        if namespace:
            cmd += ["-n", NS]
        cmd += list(args)
        return self.runner(cmd, input_text)

    def obj(self, *args, namespace=True):
        return json.loads(self.kubectl(*args, namespace=namespace))

    def check_context(self):
        known = self.kubectl("config", "get-contexts", "-o", "name", namespace=False).splitlines()
        if self.context not in known:
            raise RuntimeError(f"Context {self.context!r} is not in kubeconfig. Available: {known}")
        version = self.obj("version", "-o", "json", namespace=False)
        return {"context": self.context, "server_version": version.get("serverVersion", {})}

    def _get_ns(self):
        try:
            return self.obj("get", "namespace", NS, "-o", "json", namespace=False)
        except KubeError as exc:
            if "NotFound" in exc.detail or "not found" in exc.detail:
                return None
            raise

    def _get_deployment(self):
        try:
            return self.obj("get", "deployment", DEPLOY, "-o", "json")
        except KubeError as exc:
            if "NotFound" in exc.detail or "not found" in exc.detail:
                return None
            raise

    def assert_owned(self):
        ns = self._get_ns()
        if not ns or ns.get("metadata", {}).get("labels", {}).get(LABEL) != OWNER:
            raise RuntimeError("Dedicated lab namespace missing or not labelled as owned by this lab; run init")
        deploy = self._get_deployment()
        if not deploy or deploy.get("metadata", {}).get("labels", {}).get(LABEL) != OWNER:
            raise RuntimeError("Lab deployment missing or ownership label mismatch; run init")

    def init(self):
        self.check_context()
        ns = self._get_ns()
        if ns is None:
            manifest = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS, "labels": {LABEL: OWNER}}}
            self.kubectl("create", "-f", "-", input_text=json.dumps(manifest), namespace=False)
        elif ns.get("metadata", {}).get("labels", {}).get(LABEL) != OWNER:
            raise RuntimeError("Namespace already exists without lab ownership label. Refusing to modify it.")
        dep = self._get_deployment()
        if dep is None:
            manifest = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": DEPLOY, "namespace": NS, "labels": {LABEL: OWNER}},
                        "spec": {"replicas": 2, "selector": {"matchLabels": {"app": DEPLOY}},
                                 "template": {"metadata": {"labels": {"app": DEPLOY}},
                                              "spec": {"containers": [{"name": "pause", "image": IMAGE,
                                                                        "resources": {"requests": {"cpu": "10m", "memory": "10Mi"},
                                                                                      "limits": {"cpu": "50m", "memory": "32Mi"}}}]}}}}
            self.kubectl("create", "-f", "-", input_text=json.dumps(manifest))
        elif dep.get("metadata", {}).get("labels", {}).get(LABEL) != OWNER:
            raise RuntimeError("Deployment already exists but is not owned by this lab. Refusing to modify it.")
        self.assert_owned()
        return self.state()

    def state(self):
        self.assert_owned()
        path = f"/apis/apps/v1/namespaces/{NS}/deployments/{DEPLOY}/scale"
        body = self.obj("get", "--raw", path)
        meta = body.get("metadata", {})
        rv = str(meta.get("resourceVersion", ""))
        uid = str(meta.get("uid", ""))
        replicas = body.get("spec", {}).get("replicas")
        if not rv or not isinstance(replicas, int):
            raise RuntimeError("Scale API omitted resourceVersion or integer spec.replicas")
        return {"context": self.context, "namespace": NS, "deployment": DEPLOY,
                "resource_version": rv, "uid": uid, "replicas": replicas}

    def scale(self, replicas: int, *, resource_version=None, current_replicas=None):
        self.assert_owned()
        if replicas not in (2, 3, 4):
            raise ValueError("Only replicas 2, 3 or 4 allowed in this isolated lab")
        args = ["scale", f"deployment/{DEPLOY}", f"--replicas={replicas}"]
        if resource_version is not None:
            if not str(resource_version).isdigit():
                raise ValueError("resource_version must be numeric and originate from Scale API")
            args.append(f"--resource-version={resource_version}")
        if current_replicas is not None:
            if current_replicas not in (2, 3, 4):
                raise ValueError("unsupported precondition")
            args.append(f"--current-replicas={current_replicas}")
        self.kubectl(*args)
        return self.state()

    def settle(self, replicas, attempts=60, sleep=0.5, stable_samples=5):
        """Require several matching Scale reads, not just one quiet 0.5s interval.

        Deployment/ReplicaSet controllers can update resourceVersion after a
        scale request returns. Stability is only an observation, NOT a guarantee
        that the version will remain unchanged after capture.
        """
        previous_version = None
        consecutive = 0
        last = None
        for attempt in range(attempts):
            last = self.state()
            if last["replicas"] == replicas and last["resource_version"] == previous_version:
                consecutive += 1
            elif last["replicas"] == replicas:
                consecutive = 1
            else:
                consecutive = 0
            if consecutive >= stable_samples:
                return last
            previous_version = last["resource_version"]
            if attempt < attempts - 1:
                time.sleep(sleep)
        raise RuntimeError(
            f"Scale API did not settle at {replicas} replicas after {attempts} samples; "
            f"last observed state={last}. Check deployment controller/rollout health."
        )

    def reset(self):
        # Avoid unnecessary writes when already at the initial desired size.
        if self.state()["replicas"] != 2:
            self.scale(2)
        return self.settle(2)

    def inject(self):
        return self.scale(4)

    def delete_deployment(self):
        self.assert_owned()
        self.kubectl("delete", "deployment", DEPLOY, "--wait=true")
        return {"deleted_deployment": DEPLOY, "namespace_preserved": NS}


def is_precondition_conflict(error):
    """Recognize only a guarded *scale* command's optimistic-concurrency failure.

    kubectl emits a CLIENT-side precondition failure like:
      error: Expected resource version to be 1430, was 1455
    This is not necessarily an HTTP 409 response from the Kubernetes API server.
    Never treat network, RBAC or arbitrary API errors as an expected conflict.
    """
    if not isinstance(error, KubeError) or "scale" not in error.command:
        return False
    if not any(arg.startswith(("--resource-version=", "--current-replicas="))
               for arg in error.command):
        return False
    detail = error.detail.lower()
    if re.search(r"\bexpected (?:resource version|replicas) to be [0-9]+, was [0-9]+\b", detail):
        return True
    if "precondition" in detail and ("does not match" in detail or "do not match" in detail):
        return True
    if "conflict" in detail and ("has been modified" in detail or "resourceversion" in detail
                                  or "resource version" in detail or "precondition" in detail):
        return True
    return False


def capture_stable_precheck(lab, reset, *, attempts=8, wait=0.5):
    """Capture and precheck before any intentional intervention.

    Kubernetes reconciliation may advance resourceVersion while spec.replicas
    stays at 2. Such a mismatch is NOT evidence of our injected drift. Retry
    the clean setup with a fresh capture, and preserve each discarded version
    pair in the output. Never retry an action after a deliberate intervention.
    """
    background_version_changes = []
    for attempt in range(attempts):
        capture = reset()
        check = lab.state()
        if capture["replicas"] != 2 or check["replicas"] != 2:
            raise RuntimeError("Precheck expected 2 replicas before deliberate injection; "
                               f"capture={capture}, precheck={check}")
        if capture["uid"] != check["uid"]:
            raise RuntimeError("Target UID changed during setup; aborting rather than comparing a replaced Deployment")
        if capture["resource_version"] == check["resource_version"]:
            return capture, check, background_version_changes
        background_version_changes.append({
            "captured_version": capture["resource_version"],
            "precheck_version": check["resource_version"],
            "replicas": 2,
            "cause": "undetermined (no intentional injection yet)",
        })
        if attempt < attempts - 1 and wait:
            time.sleep(wait)
    raise RuntimeError(
        f"Before external injection, resourceVersion changed across all {attempts} "
        "capture/precheck attempts while replicas stayed at 2. "
        "This might be controller reconciliation or another writer. "
        f"Observed pairs: {background_version_changes}. "
        "Inspect the Deployment and its rollout; do not interpret this as a controlled race."
    )


def run_matrix(lab, *, settle=True):
    """Controlled matched cases; captures raw resource versions and final spec, not availability."""
    results = {"schema": "edlab-kubernetes-scale-v1", "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "context": lab.context, "namespace": NS, "deployment": DEPLOY, "cases": []}

    def reset():
        return lab.reset() if settle else lab.scale(2)

    def mark_case(name):
        print(f"[edlab] matrix case: {name}", file=sys.stderr, flush=True)

    mark_case("0: no-drift-guard-control")
    # 0. Expected clean guarded update, no external change. A real Deployment
    # can reconcile status after capture; retry *only* this no-injection control.
    no_drift_conflicts = []
    for attempt in range(6):
        start, precheck, background = capture_stable_precheck(
            lab, reset, wait=0.5 if settle else 0
        )
        try:
            applied = lab.scale(3, resource_version=start["resource_version"])
            break
        except KubeError as exc:
            if not is_precondition_conflict(exc):
                raise
            after = lab.state()
            if after["replicas"] != 2 or after["uid"] != start["uid"]:
                raise RuntimeError("Unexpected external change during clean control; aborting") from exc
            no_drift_conflicts.append({"captured_version": start["resource_version"],
                                       "observed_version": after["resource_version"],
                                       "error_excerpt": exc.detail[:250],
                                       "classification": "precondition failed without deliberate injection"})
            if settle:
                time.sleep(0.5)
    else:
        raise RuntimeError("Guarded no-drift control repeatedly encountered version "
                           f"changes before action: {no_drift_conflicts}")
    if applied["replicas"] != 3:
        raise AssertionError("Guarded no-drift control did not reach 3")
    results["cases"].append({"name": "no-drift-guard-control", "capture": start,
                             "precheck": precheck, "final": applied, "outcome": "applied",
                             "precheck_ambient_changes": background,
                             "no_drift_retry_conflicts": no_drift_conflicts})

    mark_case("1: stale-idempotent-overwrite")
    # 1. An ordinary idempotent desired-state scaling action can overwrite newer intent.
    start, precheck, background = capture_stable_precheck(
        lab, reset, wait=0.5 if settle else 0
    )
    intervened = lab.inject()
    if intervened["replicas"] != 4 or intervened["resource_version"] == start["resource_version"]:
        raise AssertionError("External change did not actually advance target version")
    overwritten = lab.scale(3)
    if overwritten["replicas"] != 3:
        raise AssertionError("Unchecked update did not overwrite newer 4-replica intent")
    results["cases"].append({"name": "stale-idempotent-overwrite", "capture": start,
                             "precheck": precheck, "external": intervened, "final": overwritten,
                             "outcome": "overwrote-newer-intent", "precheck_ambient_changes": background})

    mark_case("2: stale-version-guarded")
    # 2. Version guard prevents the same stale update, enforced by Kubernetes itself.
    start, precheck, background = capture_stable_precheck(
        lab, reset, wait=0.5 if settle else 0
    )
    intervened = lab.inject()
    try:
        lab.scale(3, resource_version=start["resource_version"])
    except KubeError as exc:
        if not is_precondition_conflict(exc):
            raise
        guard_detail = exc.detail[:350]
    else:
        raise AssertionError("Kubernetes unexpectedly accepted stale-version scale")
    protected = lab.state()
    if protected["replicas"] != 4:
        raise AssertionError("Protected target changed replicas despite guard rejection")
    results["cases"].append({"name": "stale-version-guarded", "capture": start,
                             "precheck": precheck, "external": intervened, "final": protected,
                             "outcome": "rejected-no-overwrite", "error_excerpt": guard_detail,
                             "precheck_ambient_changes": background})

    mark_case("3: stale-replica-count-guarded")
    # 3. Alternate built-in safeguard: explicit expected replica count.
    start = reset()
    intervened = lab.inject()
    try:
        lab.scale(3, current_replicas=2)
    except KubeError as exc:
        if not is_precondition_conflict(exc):
            raise
        guard_detail = exc.detail[:350]
    else:
        raise AssertionError("Kubernetes unexpectedly accepted stale current-replicas precondition")
    protected = lab.state()
    if protected["replicas"] != 4:
        raise AssertionError("Replica-count guard failed to protect changed target")
    results["cases"].append({"name": "stale-replica-count-guarded", "capture": start,
                             "external": intervened, "final": protected,
                             "outcome": "rejected-no-overwrite", "error_excerpt": guard_detail})

    mark_case("4: aba-replica-value-check-passes")
    # 4. ABA: value returns to 2 but resourceVersion has changed. A value-only
    # predicate does NOT protect the original authorization decision.
    start = reset()
    intermediate = lab.inject()  # 2 -> 4
    returned = lab.scale(2)     # 4 -> 2
    if returned["replicas"] != 2 or returned["resource_version"] == start["resource_version"]:
        raise AssertionError("ABA setup did not change and restore replica value")
    value_check_applied = lab.scale(3, current_replicas=2)
    if value_check_applied["replicas"] != 3:
        raise AssertionError("Replica-count guard did not accept the ABA state")
    results["cases"].append({"name": "aba-replica-value-check-passes", "capture": start,
                             "intermediate": intermediate, "returned": returned,
                             "final": value_check_applied, "outcome": "accepted-stale-value-match"})

    mark_case("5: aba-version-check-rejects")
    # 5. Same ABA history; version precondition catches it.
    start = reset()
    intermediate = lab.inject()
    returned = lab.scale(2)
    try:
        lab.scale(3, resource_version=start["resource_version"])
    except KubeError as exc:
        if not is_precondition_conflict(exc):
            raise
        guard_detail = exc.detail[:350]
    else:
        raise AssertionError("Version guard unexpectedly accepted an ABA-stale request")
    final = lab.state()
    if final["replicas"] != 2:
        raise AssertionError("Version guard did not preserve externally restored state")
    results["cases"].append({"name": "aba-version-check-rejects", "capture": start,
                             "intermediate": intermediate, "returned": returned, "final": final,
                             "outcome": "rejected-history-changed", "error_excerpt": guard_detail})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Native Kubernetes scale drift lab: isolated labelled resources only")
    parser.add_argument("--context", required=True, help="Explicit kubeconfig context; no implicit current-context writes")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "init", "state", "reset", "inject", "precheck", "cleanup-deployment"):
        commands.add_parser(name)
    actions = commands.add_parser("act")
    actions.add_argument("--mode", choices=("unchecked", "version-guarded", "replica-guarded"), required=True)
    actions.add_argument("--approved-version", default=None)
    matrix = commands.add_parser("run-matrix")
    matrix.add_argument("--output", default=None, help="Local JSON output path; do not commit unsanitized metadata")
    args = parser.parse_args(argv)
    lab = KubeLab(args.context)
    try:
        if args.command == "doctor":
            result = lab.check_context()
        elif args.command == "init":
            result = lab.init()
        elif args.command == "state":
            result = lab.state()
        elif args.command == "reset":
            result = lab.reset()
        elif args.command == "inject":
            result = lab.inject()
        elif args.command == "precheck":
            result = lab.state()
        elif args.command == "cleanup-deployment":
            result = lab.delete_deployment()
        elif args.command == "act":
            if args.mode == "version-guarded":
                if args.approved_version is None:
                    parser.error("version-guarded requires --approved-version from the capture")
                result = lab.scale(3, resource_version=args.approved_version)
            elif args.mode == "replica-guarded":
                result = lab.scale(3, current_replicas=2)
            else:
                result = lab.scale(3)
        else:
            result = run_matrix(lab)
            if args.output:
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (KubeError, RuntimeError, AssertionError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
