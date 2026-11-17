"""Read-only, allowlisted AAP evidence export for a paired Kubernetes lab.

Requires existing AAP_BASE_URL and AAP_TOKEN. Does not call the Kubernetes
adapter, mutate AAP, fetch raw stdout, or include tokens in the JSON output.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from scripts.aap_controller import ControllerAPI

EXPECTED_NODES = ("capture", "approve_capture", "precheck",
                  "experimental_hold", "action", "verify")
APPROVAL_NODES = frozenset(("approve_capture", "experimental_hold"))
ARTIFACT_FIELDS = frozenset((
    "edk_approved_replicas", "edk_approved_version", "edk_approved_uid",
    "edk_action_kind", "edk_expected_replicas_after",
    "edk_action_reported_version",
))
SAFE_DEBUG_FIELDS = frozenset((
    "http_status", "disposition", "replicas", "resource_version",
    "path", "approved_version", "final_version", "final_replicas",
    "result", "limitation",
))
EVIDENCE_TASKS = frozenset((
    "Require stale precondition to be rejected in this injected test",
    "Report nonsecret rejection evidence",
    "Verify the final desired state and evidence advancement",
    "Report the observation with scope limitation",
))


def allowlisted_dict(data, allowed):
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in sorted(allowed) if key in data
            and isinstance(data[key], (str, int, float, bool, type(None)))}


def relevant_events(api, job_id):
    data = api.call("GET", f"jobs/{job_id}/job_events/?page_size=200")
    if data.get("next"):
        raise RuntimeError(f"Job {job_id}: >200 events; refusing partial export")
    selected = []
    for event in data.get("results", []):
        event_data = event.get("event_data") or {}
        task = event_data.get("task")
        if task not in EVIDENCE_TASKS or event.get("event") not in (
            "runner_on_ok", "runner_on_failed",
        ):
            continue
        result = event_data.get("res") or {}
        selected_event = {"event": event.get("event"), "task": task,
                          "counter": event.get("counter")}
        # Only the explicitly nonsecret debug message, not stdout or raw res.
        debug = allowlisted_dict(result.get("msg"), SAFE_DEBUG_FIELDS)
        if debug:
            selected_event["nonsecret_debug"] = debug
        selected.append(selected_event)
    return selected


def export_run(api, run_id):
    wf = api.call("GET", f"workflow_jobs/{run_id}/")
    nodes = api.call("GET", f"workflow_jobs/{run_id}/workflow_nodes/?page_size=100")
    if nodes.get("next"):
        raise RuntimeError("Unexpected workflow pagination")
    index = {x.get("identifier"): x for x in nodes.get("results", [])}
    if set(index) != set(EXPECTED_NODES):
        raise RuntimeError(f"Run {run_id}: unexpected workflow graph: {set(index)}")
    if wf.get("status") != "successful":
        raise RuntimeError(f"Run {run_id}: workflow not successful")
    result = {"workflow_job_id": run_id, "status": wf["status"],
              "created": wf.get("created"), "started": wf.get("started"),
              "finished": wf.get("finished"), "steps": {}}
    for name in EXPECTED_NODES:
        job_id = index[name].get("job")
        if not job_id:
            raise RuntimeError(f"Run {run_id}: missing {name} job")
        kind = "workflow_approvals" if name in APPROVAL_NODES else "jobs"
        job = api.call("GET", f"{kind}/{job_id}/")
        data = {"id": job_id, "status": job.get("status"),
                "started": job.get("started"), "finished": job.get("finished")}
        if kind == "jobs":
            data["artifacts"] = allowlisted_dict(job.get("artifacts"), ARTIFACT_FIELDS)
            if name in ("action", "verify"):
                data["relevant_events"] = relevant_events(api, job_id)
        else:
            data["timed_out"] = job.get("timed_out")
        result["steps"][name] = data
    return result


def validate_pair(unchecked, guarded):
    """Validate evidence from the fixed six-node lab, not generic AAP behavior.

    Raises ValueError on missing, inconsistent, or temporally impossible
    observations. Does not claim to independently observe Kubernetes drift.
    """
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def as_int(value, label):
        try:
            return int(value)
        except (ValueError, TypeError):
            raise ValueError(f"{label}: missing or invalid integer") from None

    def parse_time(value, label):
        require(isinstance(value, str), f"{label}: missing UTC timestamp")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"{label}: invalid UTC timestamp") from None
        require(parsed.tzinfo is not None, f"{label}: missing timezone")
        return parsed

    for label, case, expected_final in (
        ("unchecked", unchecked, 3), ("guarded", guarded, 4)
    ):
        steps = case.get("steps", {})
        require(set(steps) == set(EXPECTED_NODES), f"{label}: unexpected steps")
        require(case.get("status") == "successful", f"{label}: workflow failed")
        previous_end = parse_time(case.get("started"), f"{label}.started")
        workflow_end = parse_time(case.get("finished"), f"{label}.finished")
        for step in EXPECTED_NODES:
            item = steps[step]
            require(item.get("status") == "successful", f"{label}.{step}: not successful")
            started = parse_time(item.get("started"), f"{label}.{step}.started")
            finished = parse_time(item.get("finished"), f"{label}.{step}.finished")
            require(previous_end <= started <= finished <= workflow_end,
                    f"{label}.{step}: timestamps out of order")
            previous_end = finished
            if step in APPROVAL_NODES:
                require(item.get("timed_out") is False,
                        f"{label}.{step}: timed out or not recorded")

        captured = steps["capture"].get("artifacts") or {}
        action = steps["action"].get("artifacts") or {}
        require(as_int(captured.get("edk_approved_replicas"), f"{label}.capture.replicas") == 2,
                f"{label}: unexpected captured replicas")
        approved_version = captured.get("edk_approved_version")
        require(isinstance(approved_version, str) and approved_version.isdigit(),
                f"{label}: no captured resourceVersion")
        require(isinstance(captured.get("edk_approved_uid"), str) and
                bool(captured["edk_approved_uid"]), f"{label}: missing target UID")
        require(action.get("edk_action_kind") == label,
                f"{label}: wrong action kind")
        require(as_int(action.get("edk_expected_replicas_after"), f"{label}.action.expected") == expected_final,
                f"{label}: action expected outcome mismatch")

        verifier_events = [ev for ev in steps["verify"].get("relevant_events", [])
                           if ev.get("task") == "Report the observation with scope limitation"
                           and ev.get("event") == "runner_on_ok"]
        require(len(verifier_events) == 1,
                f"{label}: expected exactly one successful final verifier observation")
        observed = verifier_events[0].get("nonsecret_debug") or {}
        require(observed.get("path") == label, f"{label}: verifier path mismatch")
        require(str(observed.get("approved_version")) == approved_version,
                f"{label}: verifier did not retain original evidence")
        require(as_int(observed.get("final_replicas"), f"{label}.verify.replicas") == expected_final,
                f"{label}: actual final replica observation disagrees")
        require(str(observed.get("final_version", "")).isdigit() and
                str(observed["final_version"]) != approved_version,
                f"{label}: verifier resourceVersion did not advance")
        require(observed.get("result") == "final_state_matches_expected_value_at_observation_instant",
                f"{label}: unexpected verifier disposition")
        require(observed.get("limitation") == "does_not_establish_stable_readiness_or_future_correctness",
                f"{label}: scope limitation not preserved")

    rejection = [ev for ev in guarded["steps"]["action"].get("relevant_events", [])
                 if ev.get("task") == "Report nonsecret rejection evidence"
                 and ev.get("event") == "runner_on_ok"]
    def is_rejection(ev):
        d = ev.get("nonsecret_debug") or {}
        try:
            return (int(d.get("http_status", -1)) == 409
                    and d.get("disposition") == "stale_precondition"
                    and int(d.get("replicas", -1)) == 4)
        except (ValueError, TypeError):
            return False
    has_409 = any(is_rejection(ev) for ev in rejection)
    return {
        "expected_artifacts_verified": True,
        "step_chronology_verified": True,
        "verifier_observations_verified": True,
        "guarded_409_debug_directly_observed": has_409,
        "disclaimer": "AAP job events and artifacts only. Independent Kubernetes snapshots are in the user's separate run logs; this exporter makes no direct cluster observation.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unchecked-run", type=int, required=True)
    parser.add_argument("--guarded-run", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output already exists; choose a new filename (no overwrite)")
    api = ControllerAPI(os.environ["AAP_BASE_URL"], os.environ["AAP_TOKEN"])
    unchecked = export_run(api, args.unchecked_run)
    guarded = export_run(api, args.guarded_run)
    checks = validate_pair(unchecked, guarded)
    record = {"schema": "edlab-aap-paired-evidence-v1",
              "exported_utc": datetime.now(timezone.utc).isoformat(),
              "source": "live read-only AAP gateway controller API",
              "unchecked": unchecked, "guarded": guarded, "checks": checks}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    args.output.chmod(0o600)
    print("EVIDENCE EXPORTED:", args.output)
    print("UNCHECKED:", unchecked["workflow_job_id"], unchecked["status"])
    print("GUARDED:", guarded["workflow_job_id"], guarded["status"])
    print("EXPECTED ARTIFACTS:", checks["expected_artifacts_verified"])
    print("GUARDED ADAPTER 409 DEBUG IN AAP EVENTS:", checks["guarded_409_debug_directly_observed"])
    if not checks["guarded_409_debug_directly_observed"]:
        print("WARNING: 409 was not independently found in the selected AAP debug event; preserve and inspect job 69 stdout before claiming the logged HTTP status")


if __name__ == "__main__":
    main()
