# Summit 2027 — isolated local Ansible/Kubernetes A/B control, V2

**V2 change:** The original V1's strict conflict predicate strips only regular spaces from the native Kubernetes error message. The separate diagnostic confirmed the message can contain nested JSON markers with intervening newlines. V2 uses the diagnostic-validated `regex_replace('\\s+', '')` whitespace normalization and verifies `ansible_failed_task.name` is the actual stale patch task. It **still requires** the native module prefix plus JSON `code:409` and `reason:Conflict`, preserves `no_log`, and intentionally fails the Ansible job after a properly classified 409. It does not force any case to pass.

**Why a new run?** Original V1 evidence is immutable and remains 3 PASS / 1 INCOMPLETE. The diagnostic is a separate 409-confirmed run. V2 subsequently produced a **separate** four-case author-reported PASS receipt; see `../receipt.json`. That does not retroactively change the V1 result.

**Safety scope:** Uses only local `kind-summit-drift` after a read-only identity/CA check, with a fresh, run-owned namespace. The local namespace is the only mutation target. No AAP, OpenShift, production access, or GitHub writes. Existing V1 and Gate8 diagnostic directories remain unchanged. The `prepare` command installs `kubernetes.core` **6.4.0** in a separate local collection directory and checks the `k8s_scale.py` SHA-256; `run` refuses a mismatched collection. Existing installed collections are not overwritten. Any mismatch or unexpected outcome fails closed. Private logs are not publication-cleared.

## Requirements and integrity check

Use a **disposable, locally controlled** kind cluster named `summit-drift` with kubectl context `kind-summit-drift`, Docker, Python 3, PyYAML, Jinja2, Ansible (`ansible-playbook` and `ansible-galaxy`), the Kubernetes Python client and collection dependencies, `kubectl`, and `kind`. The original author used Kubernetes v1.37.0. A fully pinned kind node-image digest and complete Python/Ansible dependency lockfile are **not provided**; therefore an exact-environment reproduction is not guaranteed. The runner requires a loopback kind API endpoint and matching kind CA, and refuses another cluster.

**From the experiment directory** (`experiments/ansible-scale-routing-ab`), check the six content hashes:

```bash
shasum -a 256 -c SHA256SUMS
```

**Then from the `source` directory**, run offline tests, local-cluster preflight, and isolated-collection preparation:

```bash
cd source
python3 -m unittest discover -s tests -v
python3 routing_ab.py preflight
python3 routing_ab.py prepare
```

The preflight is read-only. `prepare` installs a pinned collection **only into an isolated local scratch directory**; it does not change the cluster. Do not run if you do not control the disposable kind environment.

## Step 2: explicit four-case experiment on existing local kind

```bash
python3 routing_ab.py run --confirm 'RUN ISOLATED KIND A/B'
```

The explicit run creates a fresh run-owned namespace, stages four deployments, tests fresh/stale behavior, and requests deletion only for the owned namespace. Expect a private receipt under `~/Downloads/summit-scale-ab-private/results/` with a **new** run-specific folder; this receipt is NOT yet approved for publication.

## Expected outcome and limitations

* `scale-fresh`: task 0 / desired replicas 1
* `patch-fresh`: task 0 / desired replicas 1
* `scale-stale`: task 0 / desired replicas 2 (skipped modification)
* `patch-stale`: task nonzero / desired replicas 2 / strict classified conflict markers; workflow-style intentional failure

The output is evidence of *local Ansible task behavior against kind*, **not** a live Red Hat Ansible Automation Platform failure edge. It is one author-run experiment on Kubernetes v1.37.0 unless a different version is detected and reported. Neither AAP approval binding nor independent third-party replication is established. An API-level conflict proves this single write's `resourceVersion` was stale; it does not establish a software defect or prevent all TOCTOU hazards.

The source scope, namespace safeguards, clean-up limitations and stop conditions are inherited from the original V1. The original V1 run and separate diagnostic are documented in the parent `REVIEWER_README.md`; their private logs and original package are not included here.
