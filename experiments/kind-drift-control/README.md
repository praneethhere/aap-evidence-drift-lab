# Summit 2027 — controlled overwrite vs. version-conditional write

## Publication evidence status — V4 author-run PASS (2026-11-23 UTC)

The operator executed the V4 runner against a **preflight-verified local** `kind-summit-drift` cluster. Terminal output reported all **19 offline unit tests passed**, real preflight `NO_CHANGES_MADE`, and runtime `KIND_CONTROL_EXPERIMENT=PASS`. The newly uploaded sanitized V4 receipt (`evidence/local_control_receipts_v4.json`) was independently parsed for internal consistency against the V4 test matrix. The original Mac-side Kubernetes execution itself was **not independently witnessed or reproduced**; source-to-execution integrity is supported by the terminal transcript, not a signed build attestation.

Observed V4 cases: guarded/no drift HTTP 200 (0→1); unguarded/after drift HTTP 200 (0→2→1); guarded/stale HTTP 409 `Conflict` (replicas remain 2); recaptured/fresh HTTP 200 (2→1). All case readbacks retained the captured Deployment UID. The fresh-version control recorded 9 polls, 2 intervening resource-version changes, and a required 1.5-second quiet window.

V4 adds real-kind endpoint/CA verification, UID continuity checks, unique output directories, and explicit `PASS`/`INCOMPLETE` states. The distinct V3 author-run receipt is retained unchanged as `evidence/local_control_receipts_v3.json` for historical comparison; it **must not** be attributed to the V4 source.

This is a local Kubernetes API control—not an Ansible Automation Platform workflow execution, independent replication, OpenShift trial, or production safety guarantee.

## Earlier V3 observation — separate local kind run on November 23, 2026 UTC

One operator executed this v3 harness on a disposable local `kind-summit-drift` cluster.
The V3 sanitized receipt in `evidence/local_control_receipts_v3.json` records all four expected cases:
guarded/no drift HTTP 200 (0→1); unguarded/after drift HTTP 200 (0→2→1);
guarded/stale HTTP 409 (desired replicas stayed 2); recaptured/fresh HTTP 200 (2→1).
See `EVIDENCE_AUDIT.md` for the receipt checks and explicit limitations.
**This is one author-run Kubernetes experiment, not independent reproduction or an AAP workflow execution.**

**Status:** v3 addresses a fresh-read conflict observed during v2. On November 22, 2026, v2 produced observed local Kubernetes results for the first three cases, but its immediate recapture-and-write also returned HTTP 409. The cause is not independently verified; background Deployment updates are a plausible explanation. This version waits for a quiet `resourceVersion` before making ONE new conditional write and refuses to automatically retry a failed write.

This is an independent, safely scoped *Kubernetes API control experiment* for the AAP/OpenShift talk. It tests the **counterfactual** previously stated only conceptually: if a Deployment was approved to change from 0 to 1 replicas, but the desired state changed to 2 during approval, an **unguarded** write to 1 can silently reverse the intervening decision. A conditional write using the **stale** `metadata.resourceVersion` should receive a real HTTP **409 Conflict**. A fresh conditional write should succeed.

This is **NOT** a full Red Hat Ansible Automation Platform experiment. It **does not** test AAP workflow nodes, approval identities, artifacts, failure routing, credentials or controller version. These four local cases are distinct from the author-reported historical AAP runs 93, 99 and 105 and private W5K results.

## Required preflight

On the user's own Mac, with `kind`, `kubectl`, Docker running, and Python 3 installed:

```bash
kind create cluster --name summit-drift
kubectl config use-context kind-summit-drift
python3 kind_drift_control.py
```

The V4 script **refuses to execute unless** the current context is `kind-summit-drift`, the endpoint is loopback HTTPS, `kind get clusters` lists `summit-drift`, **and the resolved endpoint and TLS CA match the actual kubeconfig returned by `kind get kubeconfig --name summit-drift`**. The kubeconfig is processed in memory only; secrets are not written to receipts. The preflight creates nothing. If a `summit-drift` cluster already exists, inspect it; do not recreate or overwrite it blindly.

## Run against only that disposable local kind cluster

```bash
python3 kind_drift_control.py --run --out "$HOME/Downloads/summit-public-control-results"
```

The runner creates one new, uniquely named namespace and three uniquely named Deployments with the project's research ownership label. It then runs four cases:

1. **Guard/no drift**: 0→1 with captured resourceVersion; expect HTTP 200.
2. **No guard/drift**: start 0, deliberately set 2, then set 1 without a version precondition; expect HTTP 200 and an observed 2→1 overwrite.
3. **Guard/drift**: start 0, deliberately set 2, then send 0-era version with write to 1; expect real HTTP 409 and observed desired replicas still 2.
4. **Recapture/guard**: wait until a Deployment resourceVersion has been stable for at least 1.5 seconds, read the version and conditionally set 2→1 **once**; expect HTTP 200. A version race can still cause 409; the runner records that failure and stops without retrying blindly.

Intervening drift is intentionally staged **without** a `resourceVersion` precondition, as an independent external state change would be; the guarded experimental PATCH still uses the original stale version. This prevents background Deployment status updates from inadvertently blocking drift setup. All PATCH operations use the Kubernetes API via `kubectl proxy`, preserving actual response status codes. The receipts now include the drift-staging HTTP status. The runner writes only sanitized state summaries to a unique namespace-specific `local_control_receipts.json`. It never copies credentials, raw API error messages or environment variables into the report. No implicit cleanup is performed; the namespace remains for inspection. A failure after namespace creation creates an INCOMPLETE receipt and exits nonzero. Results use a unique namespace-specific folder. A preflight failure creates no resources or receipt. In exceptional filesystem failures, receipt writing may also fail.

## Safe cleanup

After reviewing the exact `LAB_NAMESPACE=` reported by the tool, run:

```bash
python3 kind_drift_control.py --cleanup 'PASTE_LAB_NAMESPACE_HERE'
```

It checks the exact local kind context, namespace naming pattern and research ownership label before deletion. **Never paste an unrelated namespace.** The lab cluster itself remains.

## Evidence classification

- `PROVEN` **only after real successful execution**: those exact API results and desired-spec observations on this disposable local kind cluster.
- `INFERRED`: implications for imperative AAP job steps; the script does **not** execute AAP.
- `UNKNOWN`: native AAP workflow repeatability, human approval separation, provenance, production behavior, readiness, availability, long-term stability or independent replication.
- **The V4 operator output and uploaded receipt have been inspected. No independent replication has occurred.**

No employer environments, credentials, clusters, incidents or proprietary data are involved. Do not run this script with a production kubeconfig/context.

## Version 2 correction

The original script sent a conditional `PATCH` to stage the drift itself. That staging write could conflict with asynchronous Deployment controller updates and stop the experiment prematurely. Version 2 uses an unconditional staging write, verifies the changed replicas, UID and version, and separately tests guarded behavior using the *old* captured version. This is a harness correction, **not** a new experiment result. The original failed namespace remains untouched.

## Version 3: timing diagnosis and no blind retry

The observed v2 run reported success for the first three cases (guard/no drift HTTP 200; unguarded overwrite after drift HTTP 200; guarded stale write HTTP 409), but a newly captured `resourceVersion` also returned HTTP 409 in the last case. The first explanation to test is whether the Deployment controller changed metadata/status between GET and PATCH; this is not proven without inspecting the receipt and subsequent object version.

The v3 runner polls the same labeled Deployment until it observes a 1.5-second quiet `resourceVersion`, retains the UID and desired-replica checks, and attempts the version-guarded write **once**. It records how many polls/version changes occurred. If that one write fails, the runner saves the HTTP reason and captured/observed versions, marks the experiment incomplete and stops. This timing control is **not** an AAP approval mechanism, a safety guarantee, or evidence of independent human authorization.

Read the old v2 failure details before drawing causal conclusions. The v3 source is distinct from the v2 original and does not rewrite its receipts. A new run creates a new research namespace; old namespaces remain for inspection and manual cleanup.

## V4 hardening boundaries

- The preflight compares the selected context's real server/CA to the installed local kind cluster; this assumes trusted local CLI binaries and an uncompromised workstation. It is not a security boundary against a malicious local administrator.
- Guarded JSON merge PATCH uses an atomic `metadata.resourceVersion` precondition; **UID continuity is checked by readback, not atomically bound in the PATCH**. Never reuse this lab code as a production safety controller.
- V4's 19 tests are offline mocks, and V4 has one separately reported local kind runtime PASS. Neither establishes independent repetition, AAP workflow correctness, or OpenShift compatibility.
