# Experiment 02 — AAP gate plan, controls, and falsification

**Evidence status (November 15, 2026):** The published repository passed 32 local tests from a fresh clone. The AAP sandbox demonstrated the paired unchecked and version-conditional workflows against the synthetic SQLite-backed target. See [observed AAP evidence](../results/aap/EXPERIMENT_EVIDENCE_V3.md). The AAP runs used controlled injections, not production workloads.

## Gate 0 — Network feasibility FIRST

- Prove Red Hat AAP can reach `/health` on the temporary HTTPS development tunnel via `ED - Probe live target`.
- If not, diagnose or switch hosting strategy. Do not try to proxy private employer endpoints or publish API secrets.
- Note that a Cloudflare Quick Tunnel is development-only, has no uptime SLA and changes address each run.

## Gate 1 — Secrets and templates after probe success

Create custom credential type in **Automation Execution → Infrastructure → Credential Types**.

Input configuration:

```yaml
fields:
  - id: edlab_api_token
    type: string
    label: Synthetic Lab API Token
    secret: true
required:
  - edlab_api_token
```

Injector configuration:

```yaml
env:
  EDLAB_TOKEN: '{{ edlab_api_token }}'
```

Create a Credential named `Evidence Drift - Local API`, using this type. Paste the token from local `~/.edlab_token` **only into AAP credential's secret field** (do not post it in chat, upload it, commit it, or place it in inventory). Use the same token loaded by the running Mac server. Attach the credential to each of the live target job templates. If the sandbox disallows custom credentials, stop and reassess; do not weaken protected endpoints for convenience.

Create the following job templates, all in **Evidence Drift - Sandbox Lab**, targeting the **Evidence Drift - Synthetic Inventory** with default execution environment, no machine credential, and Extra Variables `lab_api_url: https://<temporary-hostname>.trycloudflare.com`. Note the prior inventory's `lab_version` value does not affect these HTTP experiments.

- `ED - Live capture` → `ansible/aap_sandbox/04_capture_live_target.yml`
- `ED - Live precheck` → `ansible/aap_sandbox/05_precheck_live_target.yml`
- `ED - Live unchecked action` → `ansible/aap_sandbox/06_unchecked_action.yml`
- `ED - Live conditional action` → `ansible/aap_sandbox/07_conditional_action.yml`
- `ED - Live verify` → `ansible/aap_sandbox/08_verify_live_target.yml`

For isolated AAP tests, first run a single `ED - Live capture` template to verify access to `/v1/state`, and review its output for an HTTP 200. Do not attempt a write until this works.

## Gate 2 — Controlled paired experiments

Before each new workflow run, from a local Mac terminal with `EDLAB_TOKEN` available:

```bash
cd "$(git rev-parse --show-toplevel)"
export EDLAB_TOKEN="$(cat ~/.edlab_token)"
python3 -m target.client reset
python3 -m target.client state
```

**A — Baseline unchanged:** `Live capture` → approval → `Live conditional action` → `Live verify`. Approval release only after capture success. Expected conditional action HTTP 200, target status healthy, version 2, action_count 1; verify passes.

**B — Approval drift, conditional:** Reset; capture → pending approval → `python3 -m target.client inject-healthy` (changes v1/degraded → v2/healthy) → approve → conditional action. Expected HTTP 409 and action_count **0**. The AAP action job intentionally fails as defensive behavior. Save the server state separately (the action node won't launch the success-only verify step).

**C — Approval drift, unchecked:** Reset; identical capture/injection/approval sequence but use unchecked action. Expected HTTP 200 with action_count **1** and version 3, even though target was already healthy. This illustrates unnecessary remediation under stale evidence; not a new AAP vulnerability.

**D — Precheck-to-mutation race:** Reset; capture → approval → live precheck (PASS) → second operator-controlled approval barrier → inject-healthy → approve → unchecked action. Compare against paired conditional action after reset and same barrier. The second pause deliberately widens the time-of-check/time-of-use gap. Be transparent that it's a constructed race, not a prevalence estimate.

**E — Post-action regression:** Reset; capture → approval → conditional action (success) → second approval barrier → `python3 -m target.client inject-degraded` → approve → verify. Expected postcondition failure. Shows distinction between last known job success and later observed system state.

**Concurrency:** Local integration test `test_atomic_same_version_two_competing_events` submits two competing conditional events. Exactly one succeeds; the other conflicts (200 vs. 409). Only proves behavior for this SQLite service. Do not extrapolate to arbitrary cloud APIs.

**Run status:** Scenarios A–D were exercised in the AAP sandbox. Scenario E remains an unexecuted AAP follow-up. Expected results in this protocol should not be mistaken for observed job logs.

## Optional evidence checklist for future repetitions

- [ ] AAP sandbox version noted (2.7 documentation used, version not verified in UI)
- [ ] AAP probe job ID, time, and output
- [ ] Source commit hash and `python3 --version`
- [ ] Tunnel uptime and hostname redacted in any public screenshots (optional)
- [ ] Exact workflow graph (including success/failure dependencies)
- [ ] Capture job ID and evidence version/status
- [ ] Time of manual injection and resulting version/status
- [ ] Action job ID, HTTP code, disposition, action_count
- [ ] Final target state and verification outcome
- [ ] Reset between treatments; unique event ID each run
- [ ] At least 3 repetitions of each treatment, not only one screenshot
- [ ] Negative controls: missing token, wrong token, duplicate event, same-version new event
- [ ] Explicit limitations: synthetic SQLite target, tunnel, manually induced race, no external generalization
