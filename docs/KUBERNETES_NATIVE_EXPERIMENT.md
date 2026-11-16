# Native Kubernetes scale experiment: protocol and evidence standard

## Research question

Can an AAP-orchestrated, idempotent **desired-state** update overwrite a newer infrastructure decision despite a successful human approval and precheck, and does Kubernetes' existing resourceVersion precondition prevent this specific stale update?

This is an applied engineering *demonstration*, not an assertion of a new algorithm. Existing Kubernetes scale and API mechanisms are the intervention, not an invented control.

## Experimental controls and exact claims

| Case | Precondition | Controlled sequence | Expected | Strongest supported inference |
|---|---|---|---|---|
| A No drift | resourceVersion | capture 2 → guard to 3 | 3, applied | Guarded update can succeed when evidence is current |
| B Stale idempotent | none | capture 2, precheck pass → external set 4 → scale 3 | 3 | A still-idempotent desired-state request can overwrite newer intent |
| C Stale version | original resourceVersion | same as B but scale with captured version | 4, conflict | Native version check prevents the specific overwrite |
| D Replica-count guard | expected replicas 2 | capture 2 → external set 4 → conditional scale 3 | 4, rejected | A value check can reject a changed current value |
| E ABA count-only | expected replicas 2 | 2 → 4 → 2 → count-guarded scale 3 | 3, applied | Matching current value does not imply unchanged history |
| F ABA version | original resourceVersion | 2 → 4 → 2 → version-guarded scale 3 | 2, rejected | ResourceVersion can detect history changes masked by equal values |

Expected observations are **not results** until confirmed by the user's real cluster run. Test AAP pair B/C separately with capture, approval, precheck, hold, and external change.

## Core validity threats

1. Kubernetes controllers can independently modify the Deployment's resourceVersion (including status updates). A spurious conflict is not necessarily evidence of an operator's intervening decision; wait for stable resource versions and record external changes explicitly.
2. Kubernetes may reconcile desired replicas asynchronously. The matrix checks `spec.replicas`, **not** actual ready Pods or service availability.
3. Both external change and AAP action use a single purpose-built test Deployment. Do not generalize frequency or reliability to unrelated workloads.
4. The version string is an opaque concurrency token, not an approval signature, authorization capability, or perpetual guarantee.
5. This test intentionally demonstrates a *stale-intent* issue. The unchecked scaling command is not a defective Ansible command, nor is Kubernetes' semantics a newly discovered bug.
6. For the tunnel variant, the AAP controller sends requests to a restricted local adapter that runs `kubectl`. Do not present this as direct AAP-to-Kubernetes API access.
7. An optional AAP-only postcondition verifies a final observed replica count, not the original authorization justification or continued operational health.

## Required evidence for public technical claims

- Sanitized cluster version, Kubernetes context (redact internal names), exact code commit SHA, deterministic 6-case JSON result, API errors, and observed final replica counts.
- Real AAP workflows in the sandbox: template/job IDs, status and stdout for capture, passing precheck, approval holds, action, and final verification. Confirm final workflow statuses for both.
- Matched control and treatment: same capture (2), same precheck, same controlled external step (4), single action-mode difference.
- Independent reproduction: publish only commands, manifests, source, and non-sensitive logs. Never publish PATs, kubeconfigs, custom credential values, temporary URLs linked to live services, or employer identifiers.
- Reevaluate whether this adds practical value to the engineering audience; do not claim a novel algorithm or production outcome.

## Practical takeaway for engineers

Distinguish *desired-state convergence* (idempotence) from *authorization to apply a previously approved intent*. Use target-supported conditional updates, interpret conflict as a reason to reconsider the plan, and recognize that even a successfully passing check can become stale before the next action. An application may need a different guard or domain-specific authorization if Kubernetes resourceVersion is not available for the underlying API.
