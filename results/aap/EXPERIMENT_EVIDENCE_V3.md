# Evidence Drift Lab — AAP integration evidence (November 15, 2026)

**Status:** Synthetic laboratory demonstrations observed in Red Hat Ansible Automation Platform Developer Sandbox, with an independently implemented Python/SQLite HTTP target. This is not a Red Hat product defect report, production benchmark, or a proof about arbitrary target APIs.

**Repository:** https://github.com/praneethhere/aap-evidence-drift-lab

## Experimental proposition

Approval and pre-action validation are temporally separated from the target mutation. A previously approved or checked observation can become stale. A target that supports an atomic expected-version predicate can refuse the stale mutation; a separate read-only precheck cannot guarantee the target remains unchanged between checking and acting.

## Direct observations from AAP screenshots and the local target client

| Scenario | Workflow/job references | Observations | Interpretation |
|---|---|---|---|
| Approval baseline | Workflow 3, approval 4, child job 5 | Approved workflow proceeded to successful Hello World job | AAP approval gating worked in the sandbox |
| Inventory evidence control | Workflow 8, capture 9, verify 12 | Captured version 1/degraded; verification read same values and passed | `set_stats`-based workflow evidence propagation demonstrated |
| Inventory evidence drift | Workflow 13, verification 16 | Capture version 1/degraded; changed inventory to version 2/healthy; verification failed as expected | Custom comparison detected changed inventory values between jobs |
| Remote service connectivity | Job 18; authenticated capture job 20 | AAP reached test service over HTTPS; authenticated capture read version 1/degraded | AAP to independent persistent lab service worked |
| Fresh conditional action | Workflow 21, action job 24, verification job 25 | HTTP 200, `disposition=applied`, action count 1, state version 2/healthy, postcondition passed | Custom target accepted a current version-bound request |
| Stale conditional action | Workflow 26, action job 29 | Manual external change to version 2; HTTP 409, `disposition=stale_evidence`, action count 0; local final version 2/healthy | Custom target refused a stale version-bound request |
| Stale unchecked action | Workflow 30, action job 33, verification job 34 | After external change to version 2, unchecked action returned `disposition=applied`, action count 1, final version 3/healthy; postcondition passed | Unchecked mutation proceeded despite the original evidence being stale; postcondition alone did not establish justification |
| Precheck-to-action race, unchecked | Workflow 35, precheck job 38, final verification 41 | Precheck passed at version 1/degraded; externally changed to version 2/healthy during controlled hold; final version 3/healthy, action count 1; postcondition passed | A successful non-atomic precheck did not protect the later unchecked action |
| **Matched precheck-to-action race, conditional** | **Workflow 42, conditional action job 47** | Same version 1 precheck and version 2 external change; HTTP 409 `stale_evidence`; action count **0**; independent local state version **2/healthy**, external count 1 | **Version-conditional target API blocked the late action under the matched injection schedule** |

## Narrow conclusion

Within these manually staged synthetic cases, the target-side version predicate (implemented in the lab API with a SQLite transaction) rejected stale requests that the unchecked action accepted. AAP orchestrated the jobs and manual approvals; it did **not** itself provide the atomic target-side compare-and-mutate semantics. The synthetic postcondition checker compared live state to results reported by the preceding action; its success did not show that the action remained warranted by the initial evidence.

## Limitations and controls still needed for rigorous broader claims

- One observed run per highlighted AAP condition, not a statistically powered comparison or a measurement of natural race prevalence.
- Manual fault injection and two explicit approval holds create deterministic timing; they are not production-level concurrency or latency measurements.
- The test target is a single SQLite-backed service under our control, not a cloud provider API, cluster operation, or enterprise production target.
- We did not test AI agents, AAP MCP, OPA policy evaluation, or alternate native policy strategies in these experiments.
- The current failed-job signal treats an expected HTTP 409 guard block as job failure; production workflows may instead require a separate expected-rejection branch.
- The postcondition read only assesses one instant, not enduring correctness; target-side compare-and-mutate only protects the checked mutation, not all later effects.
- The local test suite passed from a fresh public clone. Independent AAP workflow reproduction and sanitized raw job exports remain outstanding.
