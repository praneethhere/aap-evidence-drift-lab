# Native AAP/OpenShift approval experiment — recorded lab evidence

AAP workflow job IDs below are local to one AAP instance. They are labels, not
links. Playbook names and output markers prefixed `W5A`/`W5B` are the author's
internal milestone labels. They are left unchanged so the published files match
the pinned revision.

## What the workflow does

Six playbooks under `ansible/aap_native/` (`20_capture.yml` through
`25_verify_second.yml`) run as one AAP workflow with two approval nodes.
Files `00`–`06` in the same directory are earlier diagnostic probes and are not
part of this workflow.

1. **`20_capture`** reads the Deployment. It checks the name, namespace, research
   label, and a zero-replica baseline. It then exports the UID,
   `resourceVersion`, `generation`, and the proposed value (1 replica) as
   workflow artifacts (`set_stats`).
2. **Approval 1.**
3. **`21_action_first`** sends a JSON merge patch that includes the captured
   `metadata.resourceVersion`. The API server applies it only if the object has
   not changed since capture.
   - If the patch fails, a `rescue` block treats it as a conflict only when the
     failing task is the patch **and** the module error contains HTTP code
     `409`, reason `Conflict`, and `Failed to patch object:`. Only then does it
     set `ev1_native_conflict_409` and fail the job on purpose so that the
     workflow takes the failure edge. Any other error fails the job without
     setting the flag.
4. **Success edge → `22_verify_first`**: a separate read-back job requires the
   same UID, `spec.replicas == 1`, and `generation == captured + 1`.
5. **Failure edge → `23_recapture`**: stops unless the 409 flag is set, the UID
   is unchanged, and `resourceVersion` differs from the captured value. It then
   exports fresh evidence under new keys.
6. **Approval 2.**
7. **`24_action_second`** sends a conditional patch with the new
   `resourceVersion`.
8. **`25_verify_second`** runs the same read-back checks against the new evidence.

As configured for these runs, the workflow had 8 nodes and 7 edges. Only
`21_action_first` had a failure edge. A [sanitized topology](AAP_NATIVE_WORKFLOW_TOPOLOGY.json)
provides the node identifiers, link conditions, and playbook mapping extracted
from the experiment's local provisioning source. It is a **documentation artifact**,
not an importable AAP template or a live controller export; credential configuration,
workflow creation logic, and approver CLI remain unpublished.

## Recorded runs

| Scenario | AAP workflow job | Terminal status | Recorded observation |
| --- | ---: | --- | --- |
| R1: no change | 93 | successful | Conditional write 0 → 1 desired replicas; read-back passed |
| R2a: change, second approval denied | 99 | failed | API-server 409 classified; recapture read 2 desired replicas; approval 2 denied; no further write attempted |
| R2b: change, second approval granted | 105 | successful | API-server 409 classified; recapture read 2 desired replicas; approval 2 granted; conditional write 2 → 1; read-back passed |

In R2a and R2b, the author deliberately scaled the Deployment from 0 to 2
desired replicas outside AAP while approval 1 was pending. Following R2a's
denial, a subsequent read by the local status CLI reported 2 desired replicas
at generation 10; that read is not part of the published evidence. After each
run, the author restored the Deployment to 0 desired replicas using a
version-checked `oc scale` command. The cleanup tooling is not published.

## Basis for these results

- **Source revision:**
  [`ac14dd54a57c39f8999217a6c982f01d49545c38`](https://github.com/praneethhere/aap-evidence-drift-lab/tree/ac14dd54a57c39f8999217a6c982f01d49545c38).
  All 11 Ansible execution jobs (R1: 3, R2a: 3, R2b: 5) report this
  revision in AAP job metadata. None are missing a revision and none report
  a different one. Approval nodes have separate records.
- **Runtime, as recorded by the author:** AAP 2.7, with `kubernetes.core`
  6.4.0 in the execution environment used by the job templates. No execution
  environment definition or collection requirements file is published.
- **Self-check:** the author queried the AAP API to inspect workflow topology,
  child-job statuses, named task events, and approval status changes, and saved
  a private audit summary. Neither the audit output nor underlying job records
  are published, so readers cannot independently verify these runs from this
  repository.
- The patch and read tasks use `no_log: true`. Job output therefore records the
  classification markers, not the raw API responses.

## What this does not show

- **Approval visibility.** The approver saw the captured evidence through a
  custom CLI, then approved through the AAP API. Whether AAP's own approval
  interface can show that evidence was not tested.
- **Approval binding.** The write is bound to the captured object state. The
  approval decision is not bound to the evidence: AAP approves a workflow node,
  not a specific `resourceVersion`.
- **Access control.** This lab did not test separation of duties, or whether the
  action job templates could be launched outside the workflow with hand-supplied
  evidence variables.
- **Cause of a conflict.** `resourceVersion` also changes for reasons unrelated
  to a competing decision, such as status or metadata updates. A 409 is a
  conservative "object changed" signal, not proof of a conflicting intent.
- **One recapture only.** `24_action_second` has no conflict classification. If
  the object changes again during approval 2, the second write is rejected and
  the workflow fails without recapturing.
- **Read-back limits.** The verify jobs are separate jobs using the same
  OpenShift API server, not an independent observer. The local provisioner
  assigned a reader credential to capture/recapture/verify jobs and a writer
  credential to action jobs; that configuration and its RBAC are not published.
  Verification checks UID, desired replicas, and a one-step `generation` change
  at read time, not readiness, availability, later state, or who made the change.
- **Generality.** These are three manually staged runs, not a production
  incident or a measure of how often conflicts happen. They use established
  Kubernetes optimistic concurrency and documented AAP workflow features. They
  do not show a new algorithm, an AAP vulnerability, or an upstream fix.

## Reproduction status

These runs cannot yet be reproduced from public materials. Although the
[workflow topology](AAP_NATIVE_WORKFLOW_TOPOLOGY.json) is now published, an
importable controller template, credential and RBAC setup, and the
approval-evidence CLI are not. The namespace and Deployment name are hard-coded
in the playbooks. No second operator has reproduced the setup. Do not describe
this as a one-command reproduction or as production-ready.

## Terminology and source provenance

The historical pinned commit message uses the shorthand *evidence-bound*. In
this lab that describes a **version-conditional write based on previously
captured evidence**, not evidence-bound authorization by the human approver.
The source commit is retained unchanged because all 11 historical AAP execution
job records identify that revision. The workflow was tested with temporary
service-account credentials, but authorization to launch component job templates
outside the approval workflow was not tested.
