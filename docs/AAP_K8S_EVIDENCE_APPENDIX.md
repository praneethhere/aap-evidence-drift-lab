# AAP–Kubernetes paired comparison: sanitized evidence appendix

**Status:** Two controlled real AAP workflow runs were completed in November 2026. This appendix summarizes *what was observed*, not production behavior or statistical repeatability. The raw, allowlisted AAP API export and externally observed Kubernetes snapshots are retained separately by the experimenter; this repository does not publish raw private runtime evidence, bearer tokens, the target UID, or temporary tunnel URLs.

## Claim and intervention

In both runs, AAP captured 2 desired replicas and a Kubernetes Scale `resourceVersion`, passed a live precheck, and waited at a second human-approval node. The experimenter then scaled **the same disposable Deployment** externally to 4 before releasing that hold. The **intended treatment difference** was the action policy. These were separate runs at different times and Kubernetes versions, not simultaneous randomized trials:

- **Unchecked:** `kubectl scale` to desired count 3 without the original version precondition.
- **Guarded:** `kubectl scale` to desired count 3 with the originally captured `--resource-version`.

The AAP workflows included `capture → approval → precheck → hold → action → verify`. They ran against a Kubernetes v1.37.0 `kind` lab through a restricted, bearer-authenticated HTTP adapter, **not** via direct controller-to-Kubernetes API access.

## Observed values across the two runs

| Item | Unchecked AAP workflow | Version-guarded AAP workflow |
| --- | --- | --- |
| Capture | 2 replicas, `resourceVersion=2605` | 2 replicas, `resourceVersion=46988` |
| Precheck | Successful | Successful |
| External operation while second hold pending | Desired replicas changed to 4, observed version `36634` | Desired replicas changed to 4, observed version `47617` |
| Action | Unchecked scale to 3 succeeded | Stale precondition rejected |
| Guarded adapter diagnostic | Not applicable | Adapter HTTP `409`, `stale_precondition`, observed 4 replicas |
| Final AAP verifier read | 3 desired replicas, version `36675` | 4 desired replicas, version `47633` |
| Workflow result | Successful | Successful — **expected rejection is an explicitly checked test success** |

The external state values above were **transcribed from the experimenter's contemporaneous terminal outputs**. They were independently read through both local `kubectl` and the restricted gateway before the approval was released, but **no authoritative UTC observation timestamp** was captured for each external state snapshot. The retained private AAP timestamps bound the intervention interval by the recorded script sequence; they do **not** directly timestamp the external Kubernetes write.

## Workflow ordering and evidence sources

The experimenter's **private** allowlisted AAP API export includes UTC start/finish timestamps for all six nodes in each workflow. It establishes the order `capture → first approval → successful precheck → second approval hold → action → verifier`. Both second holds completed successfully; neither timed out. The guarded action's nonsecret debug event recorded adapter HTTP `409`, `stale_precondition`, and observed 4 replicas. Each final verifier retained the original captured version.

The **separate external-change observations** came from the experimenter's terminal script, which checked that the hold was pending, scaled to 4, read that value through the restricted gateway, and *then* approved the hold. Those snapshots **did not include authoritative per-write UTC timestamps**. Their exact internal AAP job timestamps, job identifiers, target UID and raw export are deliberately not published in this repository. A private timestamped evidence appendix and cross-source consistency checker are retained for review.

These sources agree on captured version, final desired-replica value and guarded rejection; **the AAP export alone cannot independently timestamp or attribute the external Kubernetes write**. This boundary should remain explicit in talks or papers.

## Independent native Kubernetes control matrix

A separate live six-case `kind` matrix (not AAP-orchestrated) tested a no-drift version guard, stale unchecked overwrite, stale version rejection, stale count rejection, and two ABA paths. It also demonstrated that a count-only precondition can pass after **2 → 4 → 2**, whereas the old version precondition rejects that stale action. See [the native results](KUBERNETES_NATIVE_RESULTS.md). The matrix and the two AAP runs are **different observations**, not eight randomized or statistically independent repetitions of one treatment.

## Threats to validity and precise semantics

1. **Desired state only.** `spec.replicas` / the Scale subresource was checked; no claim about ready Pods, availability, or future convergence.
2. **Precondition location.** `kubectl scale --resource-version` validates the condition before attempting the scale and carries the version in its update. Observed `Expected resource version ...` errors may originate in `kubectl`; the AAP **adapter** maps a recognized precondition failure to its own HTTP `409`. Do not claim that the recorded `409` came directly from the Kubernetes API server.
3. **Ambient version churn.** Controller writes can change `resourceVersion` without a new external scaling decision. A version mismatch is conservative, not always evidence of malicious or meaningful intent change. Explicitly observed external scale to 4 is what ties these runs to the intervention.
4. **Controlled schedule.** The approval holds make the gap deterministic. This lab cannot estimate normal race probability, production failure frequency, or generality across workloads.
5. **Verification scope.** A successful final-state check reports a value at one instant; it does not prove the operation remained authorized, that the state stayed unchanged, or that no later actor intervened.
6. **Lab-only adapter.** The restricted gateway and temporary HTTPS tunnel are experiment infrastructure, not production architecture or a privileged native AAP feature.
7. **Repeated evidence is still limited.** The 68 passing tests (as of the maintenance update) contain local unit and mocked integration tests, *not* 68 live AAP trials. Two highlighted live AAP workflows were executed, one per mode.

## Public reproduction and evidence hygiene

Start with [the local native runbook](KUBERNETES_NATIVE_RUNBOOK.md), then use [AAP API workflow instructions](AAP_NATIVE_API_EXECUTION.md) for the optional integration. `scripts/export_aap_evidence.py` can generate a read-only, allowlisted controller record for runs owned by the operator; review that output before sharing. Do not commit raw exports, local snapshots with personal environment details, secret values, real tunnel origins, kubeconfig, screenshots of credentials, or private controller logs.

**Related authoritative references:** [Kubernetes `kubectl scale`](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_scale/), [Kubernetes API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/), [AAP workflow approval nodes](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/develop-ref_controller_approval_nodes).
