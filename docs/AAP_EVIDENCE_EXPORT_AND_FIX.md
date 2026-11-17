# AAP evidence export and helper maintenance (lab only)

This patch does **not** change the Kubernetes adapter, scaling behavior, live AAP workflow resources, or any previously recorded runs.

## Read-only evidence export

In the Mac clean repo with working `AAP_BASE_URL` and `AAP_TOKEN` environment variables:

```sh
python3 -m unittest discover -s tests -v
python3 -m scripts.export_aap_evidence \
  --unchecked-run 56 \
  --guarded-run 64 \
  --output ~/Documents/aap-edlab-private-results/aap-paired-evidence-v1.json
```

Do **not** commit the generated JSON, which is private runtime evidence. It contains allowlisted AAP job IDs, statuses, timestamps, selected `edk_*` artifact fields, and explicitly nonsecret debug messages. It neither stores bearer tokens nor fetches raw task responses or raw job stdout. AAP's own `no_log` suppression is preserved. If the program reports that the debug HTTP 409 is not found, inspect guarded action job 69's safe debug task before making that stronger claim; the successful guarded playbook does enforce the assertion internally.

The existing separately generated `aap-guarded-run64.json` and the six-case Kubernetes-native matrix remain separate evidence sources. The unchecked final 3-replica observation was recorded in console output; its final verifier task also provides a nonsecret observation event.

## Controller helper changes

- Default timeout for newly created approval templates is `0` (none). Existing AAP templates 35 and 38 were **already** patched live; this only corrects source code for future provisioning.
- `approvals --workflow-job-id <id>` uses the stable node identifiers rather than optional `job_type` (which the sandbox returned as null), and includes not-yet-started approvals with status `not_started`.
- Tests cover default timeout, null `job_type`, not-yet-started holds, safe redaction and evidence extraction.

This package is staged for review, not automatically committed or pushed. Use `git diff` and `git status` before publication.

## Evidence limitations

The guarded `409` is the **lab gateway's** HTTP response after classification of a `kubectl scale --resource-version` precondition rejection; do not claim to have observed HTTP 409 directly from the Kubernetes API server. The final verifier observes the Deployment's desired replica count at one instant, not pod readiness or permanent stability. The scenario is controlled and pedagogical, not evidence of a product vulnerability or algorithmic novelty. Further repeats and sensitivity analyses are recommended before statistical or research-generalization claims.
