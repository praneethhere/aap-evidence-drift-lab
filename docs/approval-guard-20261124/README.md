# Choosing approval guards: AAP/OpenShift and hosted Kubernetes experiments

Author: Praneeth Kodumagulla. Experiments dated 2026-11-24. AI-assisted implementation and evidence packaging. All resources were synthetic, isolated laboratory Deployments; no production incident, Red Hat endorsement, independent reproduction, or new concurrency algorithm is claimed.

## Native AAP/OpenShift: three trials

[Redacted receipt projection](native-guard-receipts.json) retains typed patches, state transitions, action outcomes, and explicit limitations from an offline audit of author-retained private controller artifacts and readbacks. It is not a signed controller export. The [typed implementation revision](https://github.com/praneethhere/aap-evidence-drift-lab/commit/2115d1275e970389560682e19adc1104abde92f5) is public. The capture environment reported Ansible core 2.16.19 and kubernetes.core 6.4.0. The first fresh trial failed because numeric tests were constructed with strings; the corrected run succeeded. The original failed-run manifest (11 entries) was verified during the private audit; the manifest and private receipts are not included here.

| Case | Guard | Observed outcome | Synthetic-policy verdict |
|---|---|---|---|
| Fresh | UID + replicas + generation + current tier | Applied, 0 to 1 | Correct acceptance |
| Tier standard -> restricted -> standard | Same current-value guard | Applied, 0 to 1 | False acceptance |
| Same tier history, separate Deployment | UID + captured resourceVersion | Failed 422, replicas stayed 0 | Correct nonapplication |

The oracle is a deliberately history-sensitive policy: any intervening tier transition invalidates the captured approval premise, even if restored. For example, an organization could require a fresh review after a restriction incident is cleared. This is an illustrative requirement, not AAP default behavior or a claim about any real organization. The author intentionally approved stale B/C **after** observing the intervening changes to exercise the downstream guard. No independent approver or automated AAP decision is demonstrated. The patch is the constructed patch, not observed HTTP wire bytes. The 422 is consistent with a mismatched captured version but is not uniquely diagnostic of a failed test. Readback concerns desired replicas, not running or ready Pods or long-term state.

## Separate hosted kind comparison

- [Original 28 trial rows](pilot-results.json): 11 applied (7 correct, 4 false acceptances); 17 generic 422 nonapplications were classified UNKNOWN by the original classifier. A subsequent author-side audit of those 17 reported mismatched tested fields and unchanged relevant state in every case. Do not retroactively turn UNKNOWN into a uniquely identified failed-test cause.
- [Boundary 12 trial rows](boundary-results.json): 12/12 recorded outcome predictions matched. Four fresh controls; benign annotation produced three false rejections; tier ABA produced three false acceptances. The boundary classifier records nonapplication with unchanged state and 422 rather than inferring the precise server-side failure cause. This second experiment explicitly excludes benign annotations from policy-invalidating events and treats any tier transition as invalidating; do not pool the original and boundary policy classifications as a single statistical sample.
- A negative control used invalid replicas (-1), yielding HTTP 422 without a failed freshness test. A second control labeled `missing-path` also produced 422, but its actual server error concerned a numeric label value that could not be decoded as a string. It is **not** evidence of an isolated missing-path error.

These are author-supplied rows from successful private hosted workflow executions on disposable kind v1.37.0, not native AAP trials or publicly accessible original job logs. Original script fingerprints and run IDs are recorded with each table. The scripts and workflow recipes are retained below for replication; workflow recipes are inert text files, not active Actions in this repository. This packet does not include hosted per-trial raw module messages or state snapshots.

### Reproduce safely

Read [pilot workflow recipe](guard-pilot.yml.txt) and [boundary workflow recipe](guard-boundary.yml.txt) for the original pinned tool/dependency setup and kind node image digest. Use a clean Linux x86-64 runner with Docker and a disposable cluster named `summit-drift` only. Never run these recipes against a production Kubernetes context. The workflow recipes assume the corresponding script is at repository root; adapt the working directory or use the listed steps manually in an isolated scratch repository. The scripts enforce local kind scope and pinned kubernetes.core 6.4.0 and create an owned test namespace. Run the appropriate script with `--collections <isolated collection directory> --confirm 'RUN LOCAL GUARD PILOT'`. See the exact scripts [pilot](summit_approval_guard_pilot.py) and [boundary](summit_approval_guard_boundary.py). Reproduction is invited, not asserted to have been performed by a third party. The action references in the original recipes are version tags rather than immutable commit pins, and pip transitive dependencies are not fully locked in this packet.

## Operational decision framework

| Approval contract | Candidate guard | Cost and limitation |
|---|---|---|
| Any intervening object modification invalidates approval | UID + captured resourceVersion | Conservative; benign churn can require unnecessary reapproval. Treat resourceVersion as opaque, not a numeric sequence. |
| Only selected current values matter | UID + explicitly typed field tests | Avoids irrelevant field churn; cannot detect ABA. Use only if policy expressly allows endpoint equality. |
| Certain intervening events invalidate approval, unrelated churn does not | A protected, monotonic policy epoch bound to the approved action, checked atomically at write | Design direction only, not implemented or validated here. Must cover all relevant writers and transitions, including deletion/recreation; a mutable label or endpoint hash alone is insufficient. |

Capture the approved action and its premise; explicitly bind the guard to that evidence. Validate JSON types and distinguish a failed write from successful no-op. On rejection or ambiguity, inspect the current state and actual error, stop or recapture/reapprove according to policy; do not silently retry against fresh state under stale approval. Verify the intended postcondition separately. Bound reapproval loops and consider concurrent controllers, HPA, GitOps and RBAC/admission constraints. No universal best guard is claimed. See [RFC 6902](https://www.rfc-editor.org/rfc/rfc6902.html) and [Kubernetes API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/).
