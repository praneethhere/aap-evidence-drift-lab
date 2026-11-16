# Evidence Drift Lab

A reproducible, **synthetic** experiment on stale automation decisions, human approvals, non-atomic prechecks, and version-conditional target mutations. The lab uses Red Hat Ansible Automation Platform (AAP) workflows to operate an independent Python/SQLite HTTP target.

The central question is: **does successful approval, execution, or postcondition verification establish that an action was still justified by the evidence originally captured?**

## Observed behavior

The following results were observed in the AAP Developer Sandbox using intentionally staged state changes. They are single controlled demonstrations, not measurements of production reliability.

| Scenario | Observed outcome |
| --- | --- |
| No state change after capture | Version-conditional request applied; final state `healthy`, version `2` |
| External change during approval | Version-conditional request rejected with HTTP `409`; automation action count remained `0` |
| External change followed by unchecked action | Request applied despite stale captured evidence; target advanced to version `3`, and the postcondition check passed |
| External change after a passing precheck | Unchecked action applied; matched version-conditional action was rejected with HTTP `409` |

The full recorded job references, observations, and qualifications are in [AAP experiment evidence](results/aap/EXPERIMENT_EVIDENCE_V3.md).

**Interpretation:** AAP orchestrated the workflows and approval holds. The target-side atomic version check was implemented in **this project's SQLite-backed API**. It is not an inherent guarantee of AAP. The successful unchecked postcondition checks compared target state with the action response; they did not re-establish whether the original approval evidence was still valid.

## Repository contents

- [`target/`](target/) — independent HTTP API, persistent SQLite-backed synthetic state, and a local CLI.
- [`tests/test_http_target.py`](tests/test_http_target.py) — local HTTP/SQLite integration tests.
- [`ansible/aap_sandbox/`](ansible/aap_sandbox/) — AAP playbooks for connectivity, capture, precheck, unchecked/conditional action, and verification.
- [`docs/PHASE2_VALIDATION_PLAN.md`](docs/PHASE2_VALIDATION_PLAN.md) — validation plan for the stateful target.
- [`results/aap/EXPERIMENT_EVIDENCE_V3.md`](results/aap/EXPERIMENT_EVIDENCE_V3.md) — observations from the AAP sandbox, with limitations.

## Run the local target

Use Python 3.10 or newer. From the repository root:

```sh
python3 -m unittest discover -s tests -v
```

Generate a token once for this disposable lab, then start the server:

```sh
umask 077
python3 -c 'import pathlib,secrets; p=pathlib.Path.home()/".edlab_token"; p.write_text(secrets.token_urlsafe(32)); p.chmod(0o600)'
export EDLAB_TOKEN="$(cat ~/.edlab_token)"
python3 -m target.server --db .edlab-local/state.sqlite3
```

In another terminal:

```sh
export EDLAB_TOKEN="$(cat ~/.edlab_token)"
python3 -m target.client state
```

The initial synthetic state is `version: 1`, `status: degraded`, and `action_count: 0`. To reset between **completed** experiments, run `python3 -m target.client reset`. Do not reset during an active AAP workflow or regenerate the token while the server is running.

## Reproduce the AAP comparison

Set up an isolated AAP project using this repository and the playbooks in `ansible/aap_sandbox/`. Use a disposable `localhost` inventory. Configure a **custom AAP credential** to inject the API token as the `EDLAB_TOKEN` environment variable. Supply the target's HTTPS base address as the non-secret job variable `lab_api_url`.

If the AAP sandbox cannot reach the local server directly, a temporary development tunnel may be used **only for disposable synthetic data**. The protected API endpoints require a bearer token; a quick tunnel is publicly reachable and provides no additional access-control boundary. Stop the tunnel after testing.

To compare the two action strategies, reset the target before each run and use the same order of operations:

1. Capture target state at version `1` and pause for human approval.
2. Approve, then complete the read-only precheck (it should pass at version `1`).
3. Hold the workflow before the action and execute `python3 -m target.client inject-healthy` locally. Confirm that state advances to version `2`, `healthy`, without an automation action.
4. Release the hold. For one run, execute the unchecked action; for the other, execute the version-conditional action.
5. Compare action job output and `python3 -m target.client state`. Under the recorded schedule, the unchecked path applied and reached version `3`, while the conditional path returned HTTP `409` with `action_count: 0`.

The approval holds make the timing deterministic; they do **not** establish a naturally occurring race frequency. In the conditional branch, the action job intentionally fails on HTTP `409`, so a downstream **success-only** verification node does not run.

## Limitations

- The target and version-enforcement mechanism are independently implemented, not production infrastructure or a finding of an AAP vulnerability.
- The observed AAP comparisons are controlled examples, not statistically powered benchmarks. Independent reproduction and sanitized log exports remain useful follow-up work.
- No AI agent, AAP MCP server, or OPA evaluation was included in the observed runs.
- A later state change can invalidate any postcondition read; the version predicate protects the specific conditional operation, not arbitrary downstream effects.
- Do not use this synthetic service, token handling, or public development tunnel pattern for production workloads.
