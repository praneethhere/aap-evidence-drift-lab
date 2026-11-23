# Red Hat Summit 2027 — Ansible/Kubernetes local A/B control (author-reported)

**Scope:** Four cases in one author-run, disposable local `kind` cluster, pinned to `kubernetes.core` **6.4.0** and Kubernetes **v1.37.0** (see the machine-generated receipt for the exact runtime version). No Red Hat Ansible Automation Platform controller, Red Hat OpenShift cluster, approval node, AAP failure edge or independent reproducibility test was used here.

## Observed outcomes

| Case | Ansible process exit | Final desired replicas | Interpretation |
| --- | ---: | ---: | --- |
| Fresh `k8s_scale` | 0 | 1 | Requested update applied |
| Fresh `kubernetes.core.k8s` conditional patch | 0 | 1 | Requested update applied |
| Stale `k8s_scale` | 0 | 2 | Task returned successfully without applying stale scale |
| Stale version-bound merge patch | 2 | 2 | API `409 Conflict` classified inside rescue; Ansible task then deliberately failed to expose a workflow-style failure signal |

**Trust boundary.** The author executed the run and locally checked the retained private Ansible logs against the hashes recorded in the original receipt. The private logs and exact resource versions are not public. Public outcome summaries cannot be independently verified from raw logs; the test runner and its test suite are published so another operator can attempt independent reproduction. A public receipt is evidence of the author's reported execution, not third-party verification.

**Safety & interpretation.** The stale-scale outcome is a *client-side version check*, not a guaranteed atomic precondition. The version-bound patch uses the Kubernetes API resource version precondition. These four observations do **not** establish a product defect, general race safety, TOCTOU immunity, an AAP workflow branch, an OpenShift outcome, or production readiness. Replica values are desired spec values, not workload readiness. The runner requests deletion of its own namespace; this record does not confirm deletion completion.

## Reproduction

The source and offline tests are included under `source/`. On a disposable machine, provision a local kind cluster named `summit-drift` and context `kind-summit-drift`, install Python, PyYAML, Jinja2, Ansible with `ansible-galaxy`, the Kubernetes Python client and collection dependencies, `kubectl`, and `kind`. **This artifact does not include a pinned kind node-image digest or complete Python/Ansible dependency lockfile.** From the experiment directory, verify the release manifest with `shasum -a 256 -c SHA256SUMS`. Then in `source/`, run:

```bash
python3 -m unittest discover -s tests -v
python3 routing_ab.py preflight
python3 routing_ab.py prepare
python3 routing_ab.py run --confirm 'RUN ISOLATED KIND A/B'
```

This is a potentially mutating local laboratory script and should **never** be run against a shared, production, or remote cluster. Pinned collection source is checked by SHA-256 before execution. The source depends on locally installed prerequisites; reproducibility on another person's machine has **not yet been demonstrated**.

The experiments on native AAP/OpenShift workflows 93, 99, 105 are separate, earlier author-reported results. Do **not** combine these local control cases with those workflow executions as though they were a single system demonstration.

## Provenance and experiment iteration

An earlier V1 four-case run completed three cases, while the stale-patch classification remained INCOMPLETE. A separate read-only diagnostic and an isolated patch-stale run confirmed a Kubernetes module message containing JSON `code: 409` and `reason: Conflict`. V2 tightened the classifier to normalize all whitespace and verify the actual failing task name; a **new** four-case V2 run then passed all cases. V1 was never retroactively relabeled PASS. The diagnostic and V1 private logs are not part of this public release.

**Public-release status:** Author-reported observations and source are published for review and potential replication, but the private logs remain unpublished. The private-receipt SHA-256 in `receipt.json` identifies the retained author-side receipt; it is not independent verification.
