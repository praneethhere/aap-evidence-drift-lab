# V4 observed receipt audit — 2026-11-23 UTC

**PROVEN (as an internally consistent author-reported local result):** schema v2, run status PASS, expected local kind scope, 4 cases, same Deployment UID before/after every case, and Kubernetes API HTTP statuses reported below. Actual execution occurred on the operator's Mac and was not independently witnessed.

| Case | Version / captured replicas | Intervening desired replicas | Recorded HTTP | Final desired replicas | UID preserved |
| --- | --- | --- | --- | --- | --- |
| guarded-no-drift | 24582 / 0 | — | 200 | 1 | Yes |
| unguarded-after-drift | 24596 / 0 | 2 | 200 | 1 | Yes |
| guarded-after-drift | 24629 / 0 | 2 | 409 Conflict | 2 | Yes |
| recapture-fresh-guarded-write | 24678 / 2 | — | 200 | 1 | Yes |

The recapture case reuses the `guarded-after-drift` Deployment UID. It required 9 polls and observed 2 version changes before a 1.5-second quiet interval; it sent one guarded PATCH, not an implicit retry loop. A quiet interval does **not** prove the next request is race-free. Deployment *desired replicas* are observed, not pod readiness.

**INFERRED:** an imperative AAP workflow lacking target-state preconditions can create the same general lost-update risk. **UNKNOWN:** independent reproduction, AAP identities/approvals, OpenShift-specific execution, production reliability, persistent readiness, and approval-to-evidence binding. **NOT CLAIMED:** a novel optimistic-concurrency algorithm, an AAP defect, or a real-world incident.

**Evidence provenance:** terminal transcript and uploaded sanitized JSON inspected by an assistant; the receipt itself has no independent operator signature or code-hash attestation. Reproducibility depends on installing an appropriate local kind/kubectl/Docker toolchain.

---

# Kind control experiment — evidence audit (2026-11-23 UTC)

Historical source: original V3 runner (prior candidate ZIP, checksum archived separately) and `evidence/local_control_receipts_v3.json`. **This kit ships V4 code; the preserved V3 receipt was NOT produced by V4.**
The original V3 run completed on a local `kind-summit-drift` cluster; all 11 V3 local harness unit tests passed in the operator's terminal. V4 has 19 passing offline tests reported by the operator; its separate successful runtime observation is documented below.
The author uploaded the result receipt for source-to-result inspection. No second operator has reproduced the run.

## Historical V3 observed outcomes

| Case | Captured state | Staged state | API outcome | Observed state |
| --- | --- | --- | --- | --- |
| Guarded, no drift | UID `7aad71c0…`, RV `21563`, replicas 0, generation 1 | — | HTTP 200 | replicas 1, generation 2; same UID |
| Unguarded, after drift | UID `6da0ad76…`, RV `21577`, replicas 0, generation 1 | RV `21581`, replicas 2, generation 2 | HTTP 200 | replicas 1, generation 3; same UID |
| Guarded, after drift | UID `fcd0b7ec…`, RV `21607`, replicas 0, generation 1 | RV `21611`, replicas 2, generation 2 | HTTP 409 `Conflict` | replicas 2, generation 2; same UID |
| Fresh capture and guard | SAME UID `fcd0b7ec…`, RV `21661`, replicas 2, generation 2 | — | HTTP 200 | replicas 1, generation 3; same UID |

The source shows JSON merge PATCH to the Kubernetes Deployment API, with a `metadata.resourceVersion`
precondition in guarded cases and without it in the unguarded case. The intervening
0→2 state is staged deliberately by the author. Three different Deployments are used for the
first three cases; the final case reuses the guarded-after-drift Deployment.

Fresh-guard stabilization recorded **9 polls**, **3 version changes**, and a **1.5-second** quiet-version requirement before exactly one new PATCH.
The receipt does not identify the actor responsible for those intermediate metadata updates.

## Assessment

- **PROVEN only about the *historical V3 author-reported observation*:** HTTP response codes, desired-spec replicas, UID continuity, and generation values in this uploaded receipt are internally consistent with v3 source; four prescribed assertions passed on the operator's machine.
- **INFERRED:** the same overwrite risk can arise in an imperative AAP approval workflow if a job uses a stale intended write without a version precondition.
- **UNKNOWN:** independent reproduction; AAP controller approvals, failure routing and human identity; production reliability; pod readiness; durability beyond immediate readback.
- **Not novel:** Kubernetes resourceVersion-based optimistic concurrency is an established API mechanism. This control is supporting evidence and educational reproduction, not a new algorithm or security flaw.

## Integrity notes

The public receipt includes only synthetic lab object UIDs, resource versions, cluster/namespace labels, HTTP codes and desired state; no identified employer data, credentials, hostnames or user identifiers were present in the inspected file.
This artifact should be treated as a publication candidate: perform a final disclosure review before pushing.
The older public AAP/OpenShift runs 93/99/105 and the private W5K research are **different experiments**.

## V4 publication gate

- Operator's V4 offline tests: **19/19 PASS** (terminal transcript, November 23 UTC).
- Operator's read-only preflight: `kind-summit-drift`, expected local kind endpoint and CA, `NO_CHANGES_MADE`.
- Operator's V4 runtime: **PASS**, with four cases in the separate V4 receipt; this document checks the receipt's internal consistency, not the physical Mac cluster independently.
- Before publication: re-check Git HEAD, run `git diff --check`, inspect the staged files and ensure no unrelated content.
- Never attribute V3 observations or private native-AAP observations to V4. No production claim or novelty claim.
