# AAP native Kubernetes lab: API-only execution protocol

**Status:** The local Kubernetes matrix is observed; live AAP-to-gateway execution **not yet verified**. The commands below perform AAP REST API operations from the experimenter's own Mac. ChatGPT does not have private access to the AAP sandbox or the Mac cluster. Never share `AAP_TOKEN`, `EDLAB_TOKEN`, kubeconfig, tunnel credentials, or session cookies.

## 1. Publish only the source needed for AAP project sync

From `~/Documents/aap-edlab-clean-migration/clean`, after unzipping this patch, inspect all of the intended files and avoid `git add .`:

```sh
git status --short
git add k8s_native/__init__.py k8s_native/cluster_lab.py k8s_native/gateway.py \
  scripts/__init__.py scripts/aap_controller.py \
  ansible/aap_sandbox/09_k8s_capture.yml \
  ansible/aap_sandbox/10_k8s_precheck.yml \
  ansible/aap_sandbox/11_k8s_unchecked.yml \
  ansible/aap_sandbox/12_k8s_conditional.yml \
  ansible/aap_sandbox/13_k8s_verify.yml \
  tests/test_k8s_native.py tests/test_aap_controller.py \
  docs/KUBERNETES_NATIVE_EXPERIMENT.md docs/KUBERNETES_NATIVE_RUNBOOK.md \
  docs/KUBERNETES_NATIVE_RESULTS.md docs/AAP_NATIVE_API_EXECUTION.md

git diff --cached --check
git diff --cached --name-only
```

**Review the staged file list, and confirm no internal proposal files or secrets are staged.** Only then run:

```sh
git commit -m 'Add observed native Kubernetes scale experiment and AAP API workflows'
git push origin main
```

## 2. Start the restricted gateway (Mac terminal A)

```sh
cd ~/Documents/aap-edlab-clean-migration/clean
export EDLAB_TOKEN="$(cat ~/.edlab_token)"
python3 -m k8s_native.cluster_lab --context kind-edlab-scale reset
python3 -m k8s_native.gateway --context kind-edlab-scale --port 8877
```

The gateway uses only `127.0.0.1:8877`, the fixed lab Deployment, and the bearer token. Do not expose the Kubernetes API or kubeconfig through the tunnel. An existing AAP custom credential must inject **the same token** into job executions as environment variable `EDLAB_TOKEN`.

## 3. Test the gateway and open a temporary tunnel (terminal B)

```sh
curl -fsS -H "Authorization: Bearer $(cat ~/.edlab_token)" http://127.0.0.1:8877/v1/state
cloudflared tunnel --url http://127.0.0.1:8877
```

Use the temporary **HTTPS** `.trycloudflare.com` origin shown in the terminal for `--gateway-url` below. It must target **port 8877**, not the older SQLite target at 8765. Keep gateway and tunnel running during AAP jobs; stop both immediately afterward. This is a disposable synthetic lab only.

## 4. Read-only AAP discovery (terminal C)

Set the actual AAP *platform gateway origin*, e.g. `https://<your-aap-host>` with no `/api` path. Use an existing API token or create a scoped PAT through your own authenticated AAP account; never send a token to ChatGPT.

```sh
cd ~/Documents/aap-edlab-clean-migration/clean
export AAP_BASE_URL='https://REPLACE-WITH-YOUR-AAP-GATEWAY'
read -s AAP_TOKEN
export AAP_TOKEN
python3 scripts/aap_controller.py doctor
```

`doctor` should list resource IDs. Identify the existing project **Evidence Drift - Sandbox Lab**, inventory **Evidence Drift - Synthetic Inventory**, credential **Evidence Drift - Local API**, and organization **Default**. Check that the credential injects `EDLAB_TOKEN` and matches the local token. Keep numeric IDs in your terminal; do not copy raw authentication information into chat.

## 5. Sync and plan (API calls, no UI provisioning)

Use the IDs returned by `doctor` in place of the uppercase placeholders. Insert the tunnel HTTPS origin in `--gateway-url`. Project sync and provisioning are AAP mutations; dry-run provisioning makes no changes.

```sh
python3 scripts/aap_controller.py sync-project --project-id PROJECT_ID

python3 scripts/aap_controller.py provision \
  --project-id PROJECT_ID --inventory-id INVENTORY_ID \
  --credential-id CREDENTIAL_ID --organization-id ORGANIZATION_ID \
  --gateway-url https://REPLACE-WITH-NEW-TUNNEL.example
```

Verify the project sync **finished successfully** and its source revision includes playbooks `09`–`13`, then provision:

```sh
python3 scripts/aap_controller.py provision \
  --project-id PROJECT_ID --inventory-id INVENTORY_ID \
  --credential-id CREDENTIAL_ID --organization-id ORGANIZATION_ID \
  --gateway-url https://REPLACE-WITH-NEW-TUNNEL.example --apply
```

This creates five job templates and two matched six-node workflow templates (capture → approval → precheck → experimental hold → action → verifier). AAP endpoint and RBAC compatibility have only been mocked locally; if a call fails, stop and inspect the HTTP status and live endpoint schema rather than changing permissions indiscriminately.

## 6. Run the matched pair with API approvals

For the **unchecked** workflow ID returned from provision:

```sh
python3 -m k8s_native.cluster_lab --context kind-edlab-scale reset
python3 scripts/aap_controller.py launch --workflow-id UNCHECKED_WORKFLOW_ID
python3 scripts/aap_controller.py status --workflow-job-id RUN_ID
python3 scripts/aap_controller.py approvals --workflow-job-id RUN_ID
python3 scripts/aap_controller.py approve --approval-id FIRST_PENDING_APPROVAL_ID
```

Wait for the precheck to pass and for `experimental_hold` to be pending. **Only then** inject a fresh external change:

```sh
python3 -m k8s_native.cluster_lab --context kind-edlab-scale inject
python3 -m k8s_native.cluster_lab --context kind-edlab-scale state
python3 scripts/aap_controller.py approve --approval-id HOLD_APPROVAL_ID
python3 scripts/aap_controller.py status --workflow-job-id RUN_ID
```

Expected: AAP's unchecked action changes desired replicas from 4 to 3; verifier passes on the observed final value.

Repeat on a **fresh reset**, with the **guarded workflow** ID. Capture → first approval → successful precheck → hold → local injection to 4 → second approval. Expected: guarded action receives **HTTP 409 from the restricted gateway**, whose response represents a `kubectl` precondition rejection; final desired replicas remain 4 and AAP's verification passes.

**Never preapprove a hold before the external injection**: that would invalidate the matched experimental sequence. Do not claim these expected results were observed until the AAP job IDs, final statuses, and final Kubernetes states are recorded.

## 7. Evidence review and shutdown

Save non-sensitive job template and workflow identifiers, source revision, statuses, sanitized stdout and final desired replica counts locally. Verify the actual AAP sandbox version separately. Do not publish raw logs without reviewing credential material, tunnel hosts, and private account details.

After testing, end `cloudflared` and the local gateway. To clear local AAP token without writing it to disk:

```sh
unset AAP_TOKEN EDLAB_TOKEN AAP_BASE_URL
```

## Interpretation

The native Kubernetes matrix is already observed. The AAP study tests *orchestration, evidence propagation, approval timing, and outcomes* using the Kubernetes adapter. AAP does not itself implement the Scale resourceVersion safeguard; the `kubectl` scale code and Kubernetes API do. The gateway is **not** the Kubernetes API server. In particular, `HTTP 409` in the AAP demonstration is an **adapter response**, and is not proof that the Kubernetes API server returned 409.
