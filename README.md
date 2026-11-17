# Evidence Drift Lab

**A reproducible engineering lab for stale decisions in approved automation.** It examines the gap between an approval/precheck and the eventual mutation of a target system. The primary demonstration now uses **real Kubernetes desired-replica updates** orchestrated through Red Hat Ansible Automation Platform (AAP) workflows. An earlier, intentionally synthetic Python/SQLite target remains available for comparison.

> **Question:** Can a successful approval, a passing precheck, and a successful final-state verifier coexist with an action that overwrites a newer infrastructure decision?

## Observed results (controlled lab, November 2026)

**Local Kubernetes matrix:** One six-case experiment ran on a disposable `kind` Kubernetes v1.37.0 cluster. The tests included a no-drift control, stale unchecked scaling, stale `resourceVersion` scaling, replica-count checking, and two ABA cases. The [observed matrix](docs/KUBERNETES_NATIVE_RESULTS.md) records the exact captured/final values and relevant `kubectl` error text.

**AAP-to-Kubernetes comparison:** In two separately staged AAP sandbox workflow runs, each captured **2 desired replicas**, passed a precheck, paused at a second human approval, and then received a deliberate external scale to **4**:

| AAP action after the deliberate change | Observation at final verification |
| --- | --- |
| Unchecked, idempotent `kubectl scale --replicas=3` through the restricted adapter | The later **4** was overwritten with **3** |
| Scale to 3 using the originally captured `--resource-version` | The stale request was **rejected**, preserving **4** |

The guarded action's AAP job event records the **restricted adapter's HTTP 409** with `disposition=stale_precondition`, while the final verifier records four desired replicas. The `409` is **not** an observed HTTP response from the Kubernetes API server. See the [sanitized evidence appendix](docs/AAP_K8S_EVIDENCE_APPENDIX.md) for AAP chronology, independently collected terminal state values, and remaining provenance limitations.

This is a **constructed, manually staged demonstration**—not an AAP vulnerability, a novel concurrency protocol, a production incident, a reliability-rate estimate, or a proof of Pod readiness or lasting correctness. The version guard is a Kubernetes mechanism exercised by our lab; AAP orchestrates the separate jobs and approval holds.

## Architecture and trust boundary

```text
AAP Developer Sandbox
  capture -> approval -> precheck -> experimental hold -> action -> verify
                                               |
                                       HTTPS requests
                                               v
                      temporary, token-protected lab gateway
                                      (localhost)
                                               |
                                        kubectl scale
                                               v
                       disposable kind Kubernetes Deployment
                              namespace: edlab-cas-lab
                              Deployment: replica-intent

External actor: local kubectl scale to 4 while AAP is held before its action
```

The gateway is a narrowly restricted adapter, **not** the Kubernetes API server, a general Kubernetes API proxy, or a production security component. It accepts only prescribed routes for the owned test Deployment and requires a bearer token for state and action endpoints. A development quick tunnel is publicly discoverable and is not an access-control boundary; stop it when finished.

## Reproduce the native Kubernetes experiment first

**Prerequisites:** Python 3.10+, Docker or another runtime supported by `kind`, `kind`, and `kubectl`. Use a **disposable** Kubernetes cluster and an explicit context; never run against a shared or production cluster.

```bash
python3 -m unittest discover -s tests -v
kind create cluster --name edlab-scale --wait 120s
python3 -m k8s_native.cluster_lab --context kind-edlab-scale doctor
python3 -m k8s_native.cluster_lab --context kind-edlab-scale init
python3 -m k8s_native.cluster_lab --context kind-edlab-scale run-matrix \
  --output /tmp/edlab-k8s-native-results.json
```

`run-matrix` runs six ordered test scenarios and aborts rather than silently refreshing evidence after a deliberate injected change. It compares **desired** replicas from the Kubernetes Scale subresource, not actual ready Pods. Unrelated controller/status updates can change `resourceVersion`, creating conservative rejection without an intentionally injected operator decision.

Use [the detailed native runbook](docs/KUBERNETES_NATIVE_RUNBOOK.md) and [experiment protocol](docs/KUBERNETES_NATIVE_EXPERIMENT.md) for setup, expected outputs, negative controls, resource ownership, and cleanup.

## Reproduce the optional AAP workflow integration

The five playbooks in [`ansible/aap_sandbox/09_k8s_capture.yml`](ansible/aap_sandbox/09_k8s_capture.yml) through [`13_k8s_verify.yml`](ansible/aap_sandbox/13_k8s_verify.yml) implement the six-node workflow shown above. The actual paired sandbox runs used this source, an AAP custom credential that injected `EDLAB_TOKEN`, a localhost-bound Kubernetes adapter exposed temporarily over HTTPS, and a dedicated localhost inventory.

1. Prepare the disposable native cluster and start the restricted adapter on `127.0.0.1:8877` using a **dedicated disposable lab token** (create it once, do not replace it mid-run):

   ```bash
   umask 077
   python3 - <<'PYTOKEN'
   from pathlib import Path
   import secrets
   path = Path.home() / ".edlab_token"
   if not path.exists():
       path.write_text(secrets.token_urlsafe(32))
   path.chmod(0o600)
   PYTOKEN
   export EDLAB_TOKEN="$(cat ~/.edlab_token)"
   python3 -m k8s_native.gateway --context kind-edlab-scale --port 8877
   ```

   Inject the matching `EDLAB_TOKEN` through local environment/custom AAP credentials, never through Git or public job extra variables.
2. Give the AAP sandbox temporary HTTPS access **only to the restricted adapter**, not to Kubernetes API, kubeconfig, or the Docker host. Verify the tunnel and leave it running throughout the experiment.
3. Sync this repository as an AAP project. Use the **gateway-issued AAP token** (`AAP_TOKEN`, distinct from `EDLAB_TOKEN`) for `python3 scripts/aap_controller.py doctor` and API-based project/workflow setup.
4. Use `scripts/aap_controller.py provision` **without `--apply` first**. Review its plan and actual AAP resource permissions; `--apply` creates or updates scoped lab templates. See [`docs/AAP_NATIVE_API_EXECUTION.md`](docs/AAP_NATIVE_API_EXECUTION.md) for the specific API sequence.
5. For each workflow **separately**, reset desired replicas to 2; capture, approve the first hold, ensure precheck succeeds with original evidence, stop at the second hold, deliberately scale to 4 **outside AAP**, and only then approve that hold. Inspect both the AAP job events and the actual Kubernetes Scale subresource. **Never automatically rerun after a partially completed experiment.**

The standalone local matrix does **not** require AAP, a token, or a public tunnel. The AAP comparison uses human-directed injections so it does not measure spontaneous race frequency. Read the [AAP evidence-export instructions](docs/AAP_EVIDENCE_EXPORT_AND_FIX.md) before inspecting or sharing job records. The private AAP export is **not** committed to this repository.

## What is and is not established

**Established in the recorded lab runs:** An unchecked desired-state scale can overwrite a later external desired-replica value even after approval and a passing precheck. For the paired guarded run, the version-precondition path returned the adapter's classified rejection and the observed desired replicas remained four. The count-only ABA control also shows why matching a single value is not equivalent to unchanged intervening history.

**Not established:** General race frequency; safe scaling of a production workload; readiness/availability; durable correctness after the verifier's read; cryptographic approval authorization; immunity to unrelated `resourceVersion` changes; or a defect in Red Hat, Kubernetes, or `kubectl`.

Practical lesson: **idempotence is not the same as authorization freshness**. Use target-supported conditional operations where applicable, treat conflicts as a reason to re-evaluate intent, and distinguish a final state check from proof that the earlier approval still applied at action time.

## Other repository materials

- [`k8s_native/`](k8s_native/) — scoped cluster harness and restricted adapter.
- [`scripts/aap_controller.py`](scripts/aap_controller.py) — AAP REST workflow provisioning and approval controls (no automatic approval).
- [`scripts/export_aap_evidence.py`](scripts/export_aap_evidence.py) — read-only, allowlisted AAP event export and paired consistency checks.
- [`target/`](target/) — earlier SQLite-backed synthetic HTTP service.
- [`results/aap/EXPERIMENT_EVIDENCE_V3.md`](results/aap/EXPERIMENT_EVIDENCE_V3.md) — separate older SQLite/AAP observations; **not** native Kubernetes results.
- [`docs/KUBERNETES_NATIVE_RESULTS.md`](docs/KUBERNETES_NATIVE_RESULTS.md) — single live six-case native matrix.
- [`docs/AAP_K8S_EVIDENCE_APPENDIX.md`](docs/AAP_K8S_EVIDENCE_APPENDIX.md) — publication-safe AAP/native evidence map and limitations.

**Source references:** [Kubernetes `kubectl scale` preconditions](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_scale/), [Kubernetes API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/), and [AAP 2.7 approval nodes](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/develop-ref_controller_approval_nodes).

**Safety:** This is a disposable research/teaching lab. Do not publish AAP bearer tokens, `EDLAB_TOKEN`, kubeconfigs, Cloudflare quick-tunnel URLs, employer data, or raw controller job logs. Do not use this adapter for production infrastructure.
