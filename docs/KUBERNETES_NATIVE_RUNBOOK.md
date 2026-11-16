# Reproducing the Kubernetes-native scale precondition experiment

**Evidence status (November 16, 2026):** The 58-test local suite passed, and **all six native Kubernetes v1.37.0 matrix scenarios completed successfully** on the experimenter's disposable `kind` cluster. See [the observed six-case results](KUBERNETES_NATIVE_RESULTS.md). **AAP-orchestrated Kubernetes workflow runs are not yet verified.**

## Scope

The experiment demonstrates that an ordinary idempotent desired-state operation can overwrite a newer scaling decision. Its control uses the existing `kubectl scale --resource-version` or `--current-replicas` preconditions. An ABA scenario tests what happens when the replica count changes and later returns to the approved value.

This is a teaching/engineering harness, not an allegation of a defect or a new concurrency algorithm. AAP is an optional orchestration layer; Kubernetes implements the tested scale precondition.

## Local Kubernetes setup

Install `kubectl` and `kind` using their official instructions, start the Docker runtime, and create a **disposable test cluster**. The following example uses the context `kind-edlab-scale`:

```bash
kind create cluster --name edlab-scale --wait 120s
python3 -m k8s_native.cluster_lab --context kind-edlab-scale doctor
python3 -m k8s_native.cluster_lab --context kind-edlab-scale init
python3 -m k8s_native.cluster_lab --context kind-edlab-scale run-matrix --output /tmp/edlab-k8s-native-results.json
```

The script modifies only `namespace/edlab-cas-lab` and `deployment/replica-intent`, and refuses preexisting resources that lack the expected ownership label. The Deployment uses `registry.k8s.io/pause:3.10` and small CPU and memory requests. Scaling affects desired replicas and creates or removes disposable Pods; **no workload availability or production safety is inferred**.

`run-matrix` exercises six cases: unchanged guarded control, stale unchecked overwrite, stale version rejection, stale replica-count rejection, an ABA count-only acceptance, and an ABA version rejection. See [the experimental protocol](KUBERNETES_NATIVE_EXPERIMENT.md) for the expected observations and validity limits.

ResourceVersions can change for reasons unrelated to the injected operator update, including controller status updates. A precondition conflict alone does not establish the cause of the change. In runner fix v2, the reset waits for **five consecutive equal Scale observations**, and capture/precheck is retried **only before the external injection** when the version changes but replica count remains 2. The no-drift control can also retry status-only conflicts before any deliberate intervention. Unexpected changes, replaced Deployments, and persistent controller churn are still errors; retry events are saved in the result JSON. Once the external change has been injected, the runner never retries the stale action with a new version, because that would invalidate the experiment.


### Runner fix v3: real kubectl precondition wording

A live `kubectl scale --resource-version` command was observed to exit 1 with
`error: Expected resource version to be 1430, was 1455`. Fix v2 mistakenly
classified only `resourceVersion` (no space) and other formats as a guard
conflict, causing the matrix to abort. Fix v3 recognizes the precise
`Expected resource version to be X, was Y` and `Expected replicas to be X, was Y`
precondition messages **only for guarded scale commands**. Other failures,
including RBAC denial, network errors, and invalid targets, remain fatal.

The matrix prints the current case label to stderr before each case. A
conflict during the **no-injection** control can be retried up to six times,
with observed version differences recorded; if Kubernetes keeps changing it
still aborts. Conflicts **after** intentional injection are never retried with
fresh evidence. A `kubectl` client-side precondition failure is not itself
proof of an HTTP 409 response from the API server; the restricted AAP adapter
maps recognized guard conflicts to an HTTP 409 response of its own.

A precondition error alone **does not establish** whether a version change was caused by a controller or the experiment. The successful six-case run is described in [KUBERNETES_NATIVE_RESULTS.md](KUBERNETES_NATIVE_RESULTS.md), alongside its limits. Do not infer an AAP result from the local matrix.

## AAP bridge architecture

AAP executes playbooks through its normal job and workflow engine. The `k8s_native.gateway` process is a **narrow adapter**, not a Kubernetes API proxy. It binds to `127.0.0.1` on the test machine, requires an injected bearer secret, accepts only fixed paths, and can only request scaling the one owned Deployment to 3 replicas. External injection (changing to 4) is performed locally, outside the gateway.

For temporary remote sandbox access, an HTTPS development tunnel can forward requests to the **adapter only**. Never tunnel the Kubernetes API server, kubeconfig, or privileged service-account credentials. Do not use the adapter on production or shared clusters; stop the adapter and tunnel immediately after controlled testing.

The adapter's safe job endpoints are:

| Endpoint | Operation |
|---|---|
| `GET /health` | Unauthenticated liveness probe, no target state |
| `GET /v1/state` | Authenticated read of the Deployment's Kubernetes Scale subresource |
| `POST /v1/actions/unchecked` | Desired scale to 3 with no original-version precondition |
| `POST /v1/actions/conditional` | Desired scale to 3 with captured `resourceVersion` precondition |
| `POST /v1/actions/replica-guarded` | Desired scale to 3 with expected replica count 2 |

All POST operations require the bearer token. Arbitrary Kubernetes API calls, arbitrary resource names and arbitrary replica values are not supported.

## AAP REST orchestration

The five AAP playbooks are numbered `09`–`13` in `ansible/aap_sandbox/`. They capture Scale subresource values, check the captured state, perform the unchecked or guarded action, and verify the final observed state. The graph for each comparison is:

`capture → approval → precheck → experimental hold → action → final verification`

`scripts/aap_controller.py` uses the AAP 2.7 platform-gateway API at `/api/controller/v2/`; both the gateway origin and authorization token are supplied through environment variables `AAP_BASE_URL` and `AAP_TOKEN`. A preexisting AAP custom credential injects `EDLAB_TOKEN` into the job's environment. Never put these secrets in repository variables or paste them into issue reports.

After pushing these playbooks to the AAP project's Git repository and syncing the project, run `python3 scripts/aap_controller.py doctor` to discover project, inventory, credential and organization IDs available to the token. `provision` only plans changes unless passed `--apply`; it creates uniquely named job templates and two workflow templates. `launch`, `status`, `approvals`, and `approve` support API-only operation of the experiment. Approvals remain explicit user actions and are not autoapproved.

If an AAP endpoint's validation or permissions differ from the expected schema, stop; inspect that version's API/OPTIONS contract rather than bypassing security checks. **The REST provisioning has only been tested against a local fake AAP object model, not a live controller.**

For the paired AAP study, reset target to 2 before each run. Launch workflow, explicitly approve the first node, verify precheck passed, pause at the second approval, externally change target to 4 via the local `inject` command, and explicitly approve the hold. The unchecked job should return target to 3. The guarded job should observe an expected Kubernetes-native precondition conflict and preserve 4. The guarded playbook treats an expected rejection as a passing *defensive test outcome*, and the verifier checks the final state.

## Cleanup

```bash
python3 -m k8s_native.cluster_lab --context kind-edlab-scale cleanup-deployment
```

This removes only the owned Deployment, leaving the namespace. To remove a disposable `kind` cluster that you created exclusively for this lab: `kind delete cluster --name edlab-scale`. Never delete shared or production clusters.

## Source references

- https://kubernetes.io/docs/reference/kubectl/generated/kubectl_scale/
- https://kubernetes.io/docs/reference/using-api/api-concepts/
- https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/upgrade-con_upgrade_api_changes_27
- https://github.com/ansible/awx/blob/devel/docs/workflow.md
