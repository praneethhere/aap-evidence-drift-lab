# Red Hat AAP sandbox: approval evidence drift experiments

**Scope:** This report covers only the initial inventory-variable approval experiment, not the later persistent-target mutation tests. See [live-target AAP evidence](EXPERIMENT_EVIDENCE_V3.md) for the subsequent experiments.

**Date:** 2026-11-15 (AAP interface)  
**Project:** `Evidence Drift - Sandbox Lab`  
**Workflow:** `Evidence Drift - Real AAP Experiment 01`  
**Repository:** https://github.com/praneethhere/aap-evidence-drift-lab  
**Input:** a single, enabled `localhost` in the `Evidence Drift - Synthetic Inventory`  
**Mechanism:** AAP workflow job templates, an approval node, and `ansible.builtin.set_stats` artifacts.

## Exact claim tested

An independent Ansible workflow can capture synthetic inventory metadata **before** human approval, transfer that snapshot to a downstream job, and reject execution at a pre-action assertion if the inventory values loaded by that downstream job differ from the snapshot.

This is **not** a claim of a Red Hat product defect or new security vulnerability. No mutating infrastructure action was attempted in these tests.

## Experiment A — unchanged-state control

- Workflow job: **8**
- Capture child job: **9**, success. Captured `lab_version: 1`, `lab_status: degraded`.
- Human approval: **11**, approved after capture.
- Verify child job: **12**, success.
- Downstream values: `approved_version: "1"`, `approved_status: "degraded"`, `job_start_version: 1`, `job_start_status: "degraded"`.
- Verify output: `PASS — synthetic evidence matches the approval-time snapshot.`
- Interpretation: Snapshot transfer worked, and the guard correctly allowed an unchanged-state control.

## Experiment B — intentionally changed inventory during approval

- Workflow job: **13**
- Capture job completed successfully; approval node was pending while the synthetic inventory was edited.
- Edited host variables were saved and displayed as `lab_version: 2`, `lab_status: healthy`.
- Verify child job: **16**, failed by design at the `Reject synthetic evidence that changed during approval` assertion.
- Downstream values: `approved_version: "1"`, `approved_status: "degraded"`, `job_start_version: 2`, `job_start_status: "healthy"`.
- Verify output: `EXPECTED DRIFT DETECTED — current synthetic state differs from approved evidence.`
- Interpretation: The pre-action assertion correctly rejected a changed-state case. This is a successful detection, though the AAP job status is `Failed`.

## Comparison

| Run | Pre-approval snapshot | Downstream inventory values | Guard result | Observed job status |
| --- | --- | --- | --- | --- |
| A: control | v1 / degraded | v1 / degraded | Accept | Success |
| B: drift | v1 / degraded | v2 / healthy | Reject | Failed (expected) |

## Limits and unresolved questions

1. These were **two controlled example executions**, not a statistical reliability benchmark. No performance, security, or general reliability improvement is established.
2. Both the status and version changed in Run B. Their individual effects were not isolated. The failing output shows that the version assertion was false; the status-only assertion remains untested in isolation.
3. The variables are AAP inventory metadata, **not a live, independently fetched target state**. They were loaded at downstream job startup.
4. The check is **non-atomic** with respect to any subsequent mutation; another change after the check could invalidate it.
5. No target-side compare-and-swap, conditional update, retry deduplication, or post-action state verification ran in AAP.
6. The comparison was implemented by our own playbook; these executions do not demonstrate a defect in AAP's native approval mechanism.
7. The synthetic state was manually edited for the experiment; this does not model a full real incident or AI system.
8. AAP job IDs and UI screenshots are locally verifiable artifacts but are **not independently reproducible public proof** without code, a protocol, and a runnable target.

## Historical follow-up ideas

- **Status-only drift:** keep version fixed, change only status, check that the status assertion fires.
- **Version-only drift:** keep status fixed, change only version, check that the version assertion fires.
- **Post-check race:** change target state after the last read but before mutation; quantify whether a non-atomic check can allow an inappropriate action.
- **Target-side enforcement:** introduce an isolated persistent API or stateful target with conditional update (e.g. expected version / ETag); compare unchecked action, pre-action check, and atomic guarded action.
- **Postcondition and retries:** verify actual target state after mutation and test duplicate events.

## Reproduction notes

1. Sync `main` from `https://github.com/praneethhere/aap-evidence-drift-lab` into an AAP project.
2. Create an inventory containing an enabled `localhost` with `ansible_connection: local`, `lab_version: 1`, `lab_status: degraded`.
3. Use `ansible/aap_sandbox/01_capture.yml` and `ansible/aap_sandbox/02_check.yml` in separate job templates.
4. Connect `Capture -> Approval (on success) -> Verify (on success)` in one workflow.
5. For A, leave the inventory unchanged while approval is pending; approve and observe the verify job.
6. For B, run the same workflow again, modify the inventory to version 2 / healthy **after capture** and **before approval**, then approve and observe the verify job.
7. Record the exact project revision, workflow/job IDs, timestamps, inventory changes, and both terminal outputs for each repetition.

### Relevant product documentation

- [AAP 2.7 workflows and `set_stats` artifact passing](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/develop-assembly_ug_controller_workflows)
- [AAP 2.7 approval nodes](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/develop-ref_controller_approval_nodes)

*Evidence transcribed from the AAP UI screenshots supplied during the experiment. This report is not a claim that tests were independently rerun from this document.*
