# AAP native Kubernetes lab: API-only execution protocol

**Evidence status:** The native six-case matrix and two separate AAP-to-Kubernetes workflow runs **have been observed** in the disposable lab (November 2026); see [the sanitized evidence appendix](AAP_K8S_EVIDENCE_APPENDIX.md). These instructions remain a reproduction protocol, not a claim that every independent environment will behave identically. The commands below perform AAP REST API operations from the operator's own workstation (the original run used macOS). ChatGPT does not have private access to the AAP sandbox or the Mac cluster. Never share `AAP_TOKEN`, `EDLAB_TOKEN`, kubeconfig, tunnel credentials, or session cookies.

## 1. Prepare a clean clone and the disposable cluster

The Kubernetes playbooks and AAP helper are **already published** in this repository. Do not create a new Git history or push source just to reproduce them. On a separate test machine, start from the current repository HEAD and record the commit SHA:

```sh
git clone https://github.com/praneethhere/aap-evidence-drift-lab.git
cd aap-evidence-drift-lab
git rev-parse HEAD
python3 -m unittest discover -s tests -v
```

Follow [the native cluster setup](KUBERNETES_NATIVE_RUNBOOK.md) to create and initialize an explicitly named `kind` cluster. Avoid production contexts. Sync the clone's revision into your own AAP project, or point an existing lab project at the repository. **Do not assume the historical AAP resource IDs from an earlier run apply to another sandbox.**

## 2. Start the restricted gateway (Mac terminal A)

```sh
cd /path/to/aap-evidence-drift-lab
export EDLAB_TOKEN="$(cat ~/.edlab_token)"
python3 -m k8s_native.cluster_lab --context kind-edlab-scale reset
python3 -m k8s_native.gateway --context kind-edlab-scale --port 8877
```

Create a dedicated `~/.edlab_token` securely once (see the [README](../README.md)); do not replace it during an active workflow. The gateway uses only `127.0.0.1:8877`, the fixed lab Deployment, and the bearer token. Do not expose the Kubernetes API or kubeconfig through the tunnel. An existing AAP custom credential must inject **the same token** into job executions as environment variable `EDLAB_TOKEN`.

## 3. Test the gateway and open a temporary tunnel (terminal B)

```sh
curl -fsS -H "Authorization: Bearer $(cat ~/.edlab_token)" http://127.0.0.1:8877/v1/state
cloudflared tunnel --url http://127.0.0.1:8877
```

Use the temporary **HTTPS** `.trycloudflare.com` origin shown in the terminal for `--gateway-url` below. It must target **port 8877**, not the older SQLite target at 8765. Keep gateway and tunnel running during AAP jobs; stop both immediately afterward. The experiment encountered several short-lived Quick Tunnel DNS failures, so verify the active host from **both the local terminal and an AAP capture canary** before launching a full workflow. This is a disposable synthetic lab only.

## 4. Read-only AAP discovery (terminal C)

Set the actual AAP *platform gateway origin*, e.g. `https://<your-aap-host>` with no `/api` path. Use an existing API token or create a scoped PAT through your own authenticated AAP account; never send a token to ChatGPT.

```sh
cd /path/to/aap-evidence-drift-lab
export AAP_BASE_URL='https://REPLACE-WITH-YOUR-AAP-GATEWAY'
read -s AAP_TOKEN
export AAP_TOKEN
python3 scripts/aap_controller.py doctor
```

`doctor` should list resource IDs. Provisioning assumes you have already created or selected a lab AAP project pointing to this Git repository, a dedicated localhost inventory, and a custom credential injecting `EDLAB_TOKEN` (the original run used names such as **Evidence Drift - Sandbox Lab**). The helper does not create these prerequisites. Check that the credential injects `EDLAB_TOKEN` and matches the local token. Keep numeric IDs in your terminal; do not copy raw authentication information into chat.

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

This creates five job templates and two matched six-node workflow templates (capture → approval → precheck → experimental hold → action → verifier). These endpoints were exercised successfully against one live AAP Developer Sandbox; independent installs and permissions may differ, so if a call fails, stop and inspect the HTTP status and live endpoint schema rather than changing permissions indiscriminately.

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

**Never preapprove a hold before the external injection**: that would invalidate the matched experimental sequence. These outcomes were previously observed in the two highlighted sandbox workflows. Independent reruns require **new** run IDs and their own checks; do not copy the previous results into a new run report.

## 7. Evidence review and shutdown

Save non-sensitive job template and workflow identifiers, source revision, statuses, sanitized stdout and final desired replica counts locally. Verify the actual AAP sandbox version separately. Do not publish raw logs without reviewing credential material, tunnel hosts, and private account details.

After testing, end `cloudflared` and the local gateway. To clear local AAP token without writing it to disk:

```sh
unset AAP_TOKEN EDLAB_TOKEN AAP_BASE_URL
```

## Interpretation

The native Kubernetes matrix and one paired AAP comparison are now observed. The AAP study tests *orchestration, evidence propagation, approval timing, and outcomes* using the Kubernetes adapter. AAP does not itself implement the Scale resourceVersion safeguard; the `kubectl` scale code and Kubernetes API do. The gateway is **not** the Kubernetes API server. In particular, `HTTP 409` in the AAP demonstration is an **adapter response**, and is not proof that the Kubernetes API server returned 409.
