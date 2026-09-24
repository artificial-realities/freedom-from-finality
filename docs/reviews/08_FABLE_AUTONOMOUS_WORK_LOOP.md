# Prompt 08 — Fable Autonomous Batched Work Loop

**Recommended model:** the strongest Claude Opus-tier coding model available in Fable, with high reasoning effort  
**Usage:** give this same prompt to a new Fable session each time. Each invocation completes at most one coherent batch, records durable progress, and stops.

## Mission

Continue the project safely from the authoritative final decision record. Determine the next permitted work, complete one coherent batch, validate it, record exactly what happened, and stop so a fresh session can continue from persistent project state.

This prompt supersedes the execution workflow in the older Prompt 06 and Prompt 07 files. Those files may remain as historical context, but they are not authorization and their candidate work lists are not authoritative.

## Authoritative decision source

Read this file in full before doing anything else:

`docs/reviews/05_consolidated_decision_record.md`

If the exact path differs, locate the file with the exact basename `05_consolidated_decision_record.md`. Do not substitute an earlier decision record.

The final decision record is immutable evidence. Never delete completed rows, rewrite owner answers, change statuses, or use it as a progress checklist. Completion state belongs in the separate progress ledger defined below.

Interpret statuses strictly:

- `APPROVED`: eligible for implementation only after all dependencies are satisfied.
- `EXPERIMENT_REQUIRED`: production behavior may not be implemented or enabled. Only the experiment and evaluation infrastructure explicitly permitted by the record may be prepared or run.
- `DEFERRED`: do nothing.
- `REJECTED`: do nothing and do not run rejected experiments.

No inference, convenience, attractive adjacent improvement, old review, or previous prompt may broaden this authorization.

## Persistent progress files

Use these files to coordinate across sessions:

1. `docs/reviews/08_IMPLEMENTATION_PROGRESS.md`
2. `docs/reviews/implementation_batches/BATCH_NNN.md`
3. `docs/reviews/08_DECISION_UPDATE_PROPOSALS.md`, created only when an experiment has produced a result requiring owner review.

If the progress ledger does not exist, create it before editing product code. Initialize it from all decision rows in the final record without changing their owner statuses. Record the decision-record filename and SHA-256 at the top.

The ledger must track, for every decision ID:

- owner status from the final record;
- execution state: `NOT_STARTED`, `IN_PROGRESS`, `COMPLETED`, `BLOCKED`, `RESULT_READY`, or `NOT_APPLICABLE`;
- dependencies;
- batch/report reference;
- validation evidence;
- short note explaining any block.

Also track experiments, GPU spend against the authorized cap, and the latest completed batch number. Treat the repository and test evidence as authoritative when a ledger entry is stale, but repair discrepancies explicitly rather than silently overwriting history.

## Start-of-session procedure

1. Read repository contributor and agent instructions.
2. Inspect repository status and preserve all unrelated user changes.
3. Read the final decision record completely.
4. Read the progress ledger and every batch report it references for unfinished or recently completed work.
5. Inspect relevant current code, tests, configs, story blueprints, documentation, logs, and benchmark records. Reports may be stale; code is authoritative for current behavior.
6. Reconcile the ledger with the decision record and repository. If a previous batch claims completion without the required validation, mark it `BLOCKED` or `IN_PROGRESS` and explain why.
7. Select exactly one next batch using the rules below.

## Selecting the next batch

Choose work in the roadmap order from the final decision record.

### Implementation batch

Select the earliest coherent group of `APPROVED` decisions whose dependencies are satisfied and whose execution state is not `COMPLETED`.

A coherent batch should:

- concern one subsystem or one tightly coupled contract;
- share implementation and validation boundaries;
- normally contain no more than eight decision IDs;
- be small enough to review and roll back independently;
- leave the repository internally consistent when finished;
- not mix unrelated state semantics, schema migrations, media/runtime behavior, and documentation merely to increase throughput.

More than eight IDs are allowed only for mechanical documentation or cleanup that shares one validation boundary and has no semantic coupling risk. Explain the exception in the batch report.

### Experiment batch

Select an experiment only when:

- its exact experiment is required by the final record;
- its approved prerequisites and measurement infrastructure are complete;
- running it does not enable experiment-gated behavior in production;
- the required environment, evaluator availability, credentials, and remaining authorized budget are known.

Use only the experiment definitions and success criteria in the final record. Do not revive removed or rejected experiments, including the prompt-enhancer experiment. Do not expand into a combinatorial sweep.

Experiment-only variants must remain isolated in evaluation code, temporary configurations, or off-by-default flags. Save prompts, seeds, outputs, hardware, code revision, configs, stage timings, evaluator data, failures, and costs.

If an experiment completes, write its result and recommended disposition to `08_DECISION_UPDATE_PROPOSALS.md`, mark the row `RESULT_READY` in the ledger, and stop with `OWNER_REVIEW_REQUIRED`. Do not change the owner status in the final decision record and do not implement the winning production arm until the owner approves it.

### No available batch

If nothing is currently executable:

- identify the exact blocking decisions, experiments, credentials, infrastructure, owner reviews, or failed validations;
- update the ledger;
- make no unrelated changes;
- stop with `BLOCKED` or `ALL_CURRENTLY_AUTHORIZED_WORK_COMPLETE` as appropriate.

## Before editing

For the selected batch:

1. Assign the next batch number.
2. Create the batch report with a short plan before modifying product code.
3. List decision IDs, dependencies, exact files/contracts likely affected, migrations, risks, rollback approach, and required validation.
4. Capture relevant baseline behavior and test results.
5. Mark only the selected IDs `IN_PROGRESS` in the ledger.

If the planned change conflicts with the final record, depends on a non-approved production decision, or cannot leave a coherent intermediate system, stop before editing and report the conflict.

## Implementation rules

- Implement only the selected batch.
- Preserve story-agnostic behavior. Do not encode Lego-specific assumptions in generic code.
- Keep one source of truth for each setting and piece of state.
- Keep migrations deliberate and backward-compatible where practical.
- Update prompts, schemas, validation, tests, configs, and documentation together when they form one contract.
- Remove legacy behavior only when its removal is approved.
- Add targeted tests for normal behavior, boundary cases, and failure paths.
- Preserve or improve observability required by the final record.
- Do not modify unrelated user work.
- Do not use destructive Git operations to recover from a failed attempt.
- Do not push, deploy, publish, or exceed the authorized GPU budget.
- Never claim a GPU, browser, listener, performance, or live-run validation that was not actually performed.

If a problem can be solved safely inside the selected batch, solve it. If it requires broader authority or another decision row, record the block and stop instead of silently expanding scope.

## Validation

Run the narrowest relevant tests during development, followed by every validation required by the selected decision rows. Run the full local test suite when the batch can affect shared behavior.

As applicable, include:

- static, type, formatting, and lint checks used by the repository;
- config-load and migration tests;
- schema and prompt-contract tests;
- state transition, rollback, resume, and intervention timing tests;
- deterministic replay and seed checks;
- synthetic/local audio or media checks;
- benchmark-tooling checks;
- recorded GPU or live tests only when the environment and authorization exist.

A batch is `COMPLETED` only if its required validation passes. If a validation cannot run, do not mark the batch complete merely because implementation finished. Mark it `BLOCKED`, state precisely what remains, and leave the code in a coherent reviewable state.

## End-of-batch records

Finish `docs/reviews/implementation_batches/BATCH_NNN.md` with:

- batch purpose and decision IDs;
- files changed;
- behavior and contract changes;
- migrations and compatibility notes;
- tests and validations run, with exact commands and results;
- benchmarks or explicit `not run` reasons;
- deviations from the plan;
- known risks and rollback instructions;
- unresolved blockers;
- recommended next batch;
- suggested commit message;
- commit hash if a commit was explicitly permitted and created.

Then update the progress ledger atomically:

- mark validated IDs `COMPLETED`;
- mark experiment decisions `RESULT_READY`, never `COMPLETED`, until owner approval;
- mark unresolved work `BLOCKED` or leave it `IN_PROGRESS` with an exact explanation;
- record validation and batch-report references;
- record experiment/GPU spend.

Do not remove completed decisions from either the ledger or the final record. History must remain auditable.

## Required final response

Begin with exactly one of:

- `BATCH_COMPLETED`
- `OWNER_REVIEW_REQUIRED`
- `BLOCKED`
- `ALL_CURRENTLY_AUTHORIZED_WORK_COMPLETE`

Then report concisely:

1. batch number and decision IDs;
2. what changed;
3. validation results;
4. progress-ledger and report paths;
5. blockers or owner decisions required;
6. the recommended next action.

Stop after this single batch. Do not begin the next batch in the same invocation. A new Fable session will receive this same prompt and continue from the persistent ledger.
