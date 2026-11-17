# Kubernetes-native scaling: observed six-case matrix

## Scope and provenance

Observed in a **single controlled run** of a disposable local `kind` Kubernetes **v1.37.0** cluster (`kind-edlab-scale`), namespace `edlab-cas-lab`, Deployment `replica-intent`. Reported from the experimenter's terminal output on **2026-11-16 05:17:13 UTC**. The generated local JSON is `~/Documents/aap-edlab-private-results/k8s-native-matrix-v3.json`; this Markdown summary does **not** contain the cluster object UID or any credentials. The test runner was invoked as `python3 -m k8s_native.cluster_lab --context kind-edlab-scale run-matrix --output ...`.

All 58 local automated tests passed before the matrix (local test counts do **not** establish AAP integration). The matrix then printed all six completed cases and corresponding Kubernetes observations.

| Case | Captured desired replicas (version) | Deliberate external step | Final desired replicas | Observed outcome |
| --- | --- | --- | --- | --- |
| No-drift guard control | 2 (`2218`) | None | 3 (`2238`) | Guarded action applied |
| Stale idempotent overwrite | 2 (`2259`) | Scale to 4 (`2283`) | 3 (`2294`) | Older desired-state value overwrote newer value |
| Stale resource-version guard | 2 (`2324`) | Scale to 4 (`2344`) | 4 (`2344`) | Stale action rejected; newer count preserved |
| Stale replica-count guard | 2 (`2377`) | Scale to 4 (`2398`) | 4 (`2398`) | Value mismatch rejected |
| ABA count-only guard | 2 (`2431`) | 2 → 4 (`2452`) → 2 (`2465`) | 3 (`2476`) | Count-only guard accepted an action despite intervening history |
| ABA resource-version guard | 2 (`2509`) | 2 → 4 (`2530`) → 2 (`2542`) | 2 (`2542`) | Version guard rejected after history changed |

The stale-version case reported `error: Expected resource version to be 2324, was 2344`; the replica-count case reported `error: Expected replicas to be 2, was 4`. The ABA version case reported `error: Expected resource version to be 2509, was 2542`. Those are **kubectl precondition errors**, not captured proof of an API-server HTTP 409 response. The optional restricted AAP gateway translates recognized precondition errors into **gateway HTTP 409** responses.

## Interpretation and limitations

- **Proven in the local run:** An idempotent desired-replica update could overwrite the later declared desired count, while the guarded commands refused the specific stale request in this staged sequence. Matching a prior value after an ABA sequence did not establish unchanged history.
- **Not established:** Real-world frequency, production availability, actual ready-replica count, customer outcome, a novel concurrency algorithm, or an AAP-specific result. The matrix ran locally against Kubernetes; **the two later AAP-integrated runs are separately recorded in [AAP_K8S_EVIDENCE_APPENDIX.md](AAP_K8S_EVIDENCE_APPENDIX.md)**.
- Kubernetes controllers can also advance versions via status changes, producing false-positive freshness failures. The harness records or rejects such unexpected changes during capture and the no-drift control. Guards ensure conservative rejection, not permanent correctness or business-level authorization.
- The underlying `kubectl scale --resource-version` implementation validates the fetched Scale object and submits the version-bearing update. The observed error may arise before a write is sent to the server. Do **not** call these errors observed HTTP 409s from Kubernetes.
- The local CLI matrix and the later AAP runs are separate evidence sources. The AAP results and provenance limitations are summarized in [the paired evidence appendix](AAP_K8S_EVIDENCE_APPENDIX.md); neither set of runs measures production reliability.

## Subsequent AAP integration

Two completed, separately staged AAP Developer Sandbox workflows used a restricted HTTP-to-`kubectl` adapter against the same type of disposable Deployment. The unchecked path finished with 3 desired replicas after an externally injected change to 4; the guarded path retained 4 and recorded the adapter's `409` / `stale_precondition` event. These are **not extra cases from the six-case local matrix**; see [the sanitized AAP evidence appendix](AAP_K8S_EVIDENCE_APPENDIX.md) for chronology and qualifications.

## References

- Official `kubectl scale` preconditions: https://kubernetes.io/docs/reference/kubectl/generated/kubectl_scale/
- Kubernetes Scale API and resourceVersion concepts: https://kubernetes.io/docs/reference/using-api/api-concepts/
- Local experiment protocol: [KUBERNETES_NATIVE_EXPERIMENT.md](KUBERNETES_NATIVE_EXPERIMENT.md)
