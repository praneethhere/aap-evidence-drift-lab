# When an approved scale does not happen: an AAP routing field guide

**Audience:** Platform engineers and automation architects who already build approval-based workflows in Red Hat Ansible Automation Platform (AAP) and manage Kubernetes or Red Hat OpenShift Deployments.

**Scope:** An operational teaching pattern derived from controlled laboratory experiments. It is not an installable production workflow or a new concurrency protocol.

## 1. Predict the outcome before examining the job status

A change starts with zero desired replicas. The proposed change requests one. While approval is pending, another actor sets the desired count to two. Applying one now means a scale-down, not the originally proposed scale-up.

| Mechanism | What to inspect | Why it matters |
| --- | --- | --- |
| Unconditional desired-state update | Current intent before issuing the write | Idempotence alone does not preserve newer intent. |
| `kubernetes.core.k8s_scale` with a stale precondition | Warning, `changed`, and a separate target read-back | A skipped mutation can complete the task successfully. |
| `kubernetes.core.k8s` merge patch carrying the captured `metadata.resourceVersion` | API response, classified error, and target read-back | A stale version can reject the write at the API server. The workflow must decide how to handle that rejection. |

The pinned local Ansible control uses `kubernetes.core` 6.4.0. Its `k8s_scale` precondition is checked on a fetched object; the subsequent scale PATCH does not carry that version. Do not treat this client-side check as an atomic write guard. The interval between check and write was not experimentally exercised here.

## 2. Route by the outcome, not by job color

| Observation | Suggested routing | Evidence or boundary |
| --- | --- | --- |
| Fresh write succeeds; read-back satisfies the intended postcondition | Complete as verified at read time | Author-reported AAP jobs #146 and #93; distinct series. |
| Scale job succeeds but skips a stale precondition; read-back differs from the intended state | Fail verification; stop or enter a separately defined recovery path | Author-reported AAP job #155: `changed=false`, desired count 2, failed verification. |
| A version-bound patch is rejected with a specifically classified API 409 conflict | Recapture state and reconsider the requested operation | Author-reported native AAP jobs #99 and #105; separate pinned local/CI patch control. |
| New approval denied or expires | Stop; do not issue a second write | Denial observed in #99; expiry handling is a design recommendation, not a tested result. |
| New approval granted | Attempt a new conditional write using the newly captured version; verify | Author-reported #105. |
| Unexpected error, ambiguous response, or unavailable read-back | Fail or investigate explicitly; do not claim verified success or blindly retry | Recommended fail-closed policy; not an additional measured trial. |
| `changed=false` but read-back already matches | Distinguish an already-satisfied state from an applied mutation; retain approval and audit context | Design recommendation, not a measured case. |

A generic failed task is not proof of a version conflict. Route only a specifically identified conflict to recapture; preserve other errors for investigation. A second conflict should stop or enter a separately bounded, explicitly authorized recovery policy. The recorded native workflow does not demonstrate unlimited retries.

## 3. Build the workflow around three contracts

1. **Authorization:** record the proposed operation and the decision to approve it. Include cluster/context, namespace, kind, name, UID, captured desired replicas, intended replicas, and opaque `resourceVersion`. Show the before/after intent to the approver; do not silently refresh evidence after approval.
2. **Freshness:** carry the captured version to a target-supported conditional mutation. Do not substitute a separate GET comparison for enforcement at the API write. Treat `resourceVersion` as opaque; even unrelated updates can invalidate it.
3. **Outcome:** route action completion through a separate read-back. Verify target identity and the intended desired-replica value. Apply any generation or additional business predicates appropriate to the operation. If the read fails or the assertion fails, do not report verified completion.

Conceptual topology (recommended composition; the two historical AAP series were separate workflows):

```text
capture -> approval -> conditional action -> read-back -> verified / failed
                               |
                       classified conflict
                               v
                   recapture -> new approval
                                  |       |
                                deny    grant
                                  |       v
                                 stop   conditional action -> read-back
```

Keep denial/timeout and unexpected-error routes terminal unless there is a defined escalation policy. For legacy `k8s_scale` workflows, connect successful execution to an assertion job rather than treating its success edge as completion. The historical native lab uses a separate read-back against the same API server, not an independent external observer. Its checks include UID, desired count and a one-step generation change at read time; that is not proof of who performed a write.

## 4. Adaptation checklist

- Identify the exact collection/module version and inspect its precondition and error semantics. Record the execution environment and dependency versions.
- Restrict writes to authorized targets and give the verifier read-only access where practical. Validate target identity and ensure the displayed intent corresponds to the captured evidence; the lab does not establish cryptographic approval binding or separation of duties.
- In a disposable namespace, exercise a fresh write, a deliberately stale write, a denied renewed approval and an assertion failure. Verify no further mutation occurs on denial. Inspect actual action results and target read-back, not only workflow status.
- Consider controllers such as HPA and GitOps, external actors, timeouts and ambiguous writes before using this pattern in a real environment. These cases are not validated by the lab.
- Desired replica count is not Pod readiness, availability, durable correctness, or an audit attribution of the mutation. Add appropriate readiness and operational checks for the actual service.

## 5. Evidence to inspect before adapting

- [Summit evidence matrix](SUMMIT_2027_EVIDENCE_MATRIX.md): inventory, experiment boundaries, and W5C execution-environment provenance.
- [W5C controller receipt](w5c_controller_receipt.json): author-projected fresh #146 and stale #155 outcomes; source logs private, exact runtime collection version unknown. Historical configured-image metadata is not runtime attestation.
- [Local Ansible A/B control](../experiments/ansible-scale-routing-ab/REVIEWER_README.md): two fresh and two stale cases; stale scale exited 0, stale patch conflict was classified and deliberately made to fail with exit 2. Both stale cases retained two desired replicas.
- [GitHub-hosted control execution](https://github.com/praneethhere/aap-evidence-drift-lab/actions/runs/45236307503): separate hosted execution of the repository-authored four-case harness. It is not an AAP/OpenShift run or independent human replication.
- [Native AAP playbooks](../ansible/aap_native/): conditional mutation and bounded recapture/reapproval pattern for jobs #93/#99/#105. Historical records and full setup are not public; approval decisions were submitted by the author through the AAP API after viewing evidence via a custom CLI.
- [Ansible upstream PR #1294](https://github.com/ansible-collections/kubernetes.core/pull/1294): documentation clarification of the scale precondition mismatch outcome, submitted and open at the time of writing. No runtime change or maintainer endorsement is implied.
- [Kubernetes API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/): resource versions and update conflict semantics.

The AAP outcomes are author-reported and the local/CI controls are a separate experiment series. The guide is a proposed practitioner adaptation, not a tested end-to-end production design. No observed frequency, customer impact, new vulnerability, or independently reproduced AAP result is claimed.
