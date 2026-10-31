# Design notes

## What goes wrong when an agent writes

Agents that only read are cheap to be wrong about. Agents that write to shared
systems fail in a small number of repeatable ways:

| Failure | Typical cause |
| --- | --- |
| Malformed record | The agent copied displayed values back as written values (a multi-select shown as one string, a formula column, a name where a contact is required). |
| Orphaned records | Trial and error against the real sheet: each rejected or half-valid attempt that did succeed leaves a blank or duplicate row behind. |
| Silent overwrite | An update replaces values nobody looked at, with no record of the old ones. |
| Unapproved action | The agent decides to delete, notify, or reassign something no person agreed to. |
| Approved one thing, ran another | The plan shown to the approver and the plan executed are not the same object. |
| No way back | A multi-step change fails half way and nothing records what was already applied. |
| Unreviewable history | The log exists but can be edited, so it cannot settle a dispute. |

The mock service in `agent_tool_safety/sheets/mock.py` reproduces the quirks behind
the first two rows: errors that do not name the column, a multi-select that reports
itself as text, contact columns with an allow-list, formula columns that reject
writes, and no copy-row call.

## Mechanisms and why each exists

### Risk tiers

Tools are registered with one of `READ`, `WRITE_REVERSIBLE`, `WRITE_IRREVERSIBLE`,
`EXTERNAL_NOTIFY`. The tier belongs to the tool, not to the agent's description of
what it is doing. A write tool without a preview, or a reversible tool without an
undo, fails closed at dry-run time (`ToolSpec.validate`).

### Dry run before any write

`Executor.dry_run` calls each tool's `preview`, which must have no side effects, and
returns before/after for every call. Calls that would be rejected show up as
`WOULD FAIL` here, before the real system is touched. Only a clean dry run is
recorded, and the policy refuses to execute a write plan without one. A dry run
authorizes one execution; it is consumed afterwards.

### Approval bound to the plan hash

A plan is canonical JSON hashed with SHA-256. `Policy.issue_token` signs
`(plan_hash, approver, expiry)` with HMAC. At execution the plan is re-hashed and
compared, so editing any argument after approval (including by mutating a shared
dict) changes the hash and the approval no longer applies. Tokens expire, cannot be
edited (signature), and are single use. Approving is meant to be a human-side call:
the agent-facing surface is `dry_run` and `execute`, and nothing in this repo
enforces that boundary (see below).

The default policy requires approval for every non-read tier. A relaxed policy can
waive approval for reversible writes but never the dry run.

### Hash-chained audit log

Each JSONL entry includes the previous entry's hash. Editing, reordering, or
removing an entry other than the last breaks the chain, and `verify()` reports the
first bad entry. Removing the last entries is only detectable against a head hash
kept somewhere else, which is why `verify(expected_head=...)` exists. The log also
stores each call's undo data, and `Executor.undo` refuses to run from a log that
does not verify, because forged undo data is a write primitive.

Refused executions are logged too: an attempt to run an unapproved plan is
evidence, not noise.

### Undo, and partial failure

Each call is logged with its undo data immediately after it succeeds. Execution
stops at the first error. Because the log is written call by call, a run that
stops half way still has an exact record of what to reverse, and `undo` replays it
newest first. Undo skips calls already undone, so an interrupted undo can resume.
Irreversible calls are reported, never silently skipped.

### Classify before writing

The service will not say which column it rejects, so the decision is made first,
from column metadata and the existing rows:

- formula columns are skipped
- a column that reports TEXT but lists options, where existing values are
  comma-joined options, is treated as a multi-select and written as a list
- contact columns take only values from an allow-list; anything else is left
  blank and flagged
- a name in a person-like text column (Owner, Reviewer) is flagged for a human
  instead of guessed at
- anything unexpected is flagged rather than coerced

The output is a per-column decision table that a person can read.

### One disposable row for diagnosis

If a write is still rejected, the usual agent behavior is to retry against the real
sheet with guesses, leaving a trail of rows. `diagnose_payload` instead creates one
test row, bisects the payload's columns with updates to that row only, and deletes
it in a `finally` block. Probes grow with the logarithm of the column count for one
bad column. `orphan_audit` is the safety net for earlier damage: it lists rows
created in a window that are mostly blank, duplicate an earlier row, or have an
error-valued formula, and leaves the decision to a person.

## What this does not cover

- Authentication and authorization. Anyone who can call `Policy.issue_token` can
  approve; there is no identity system behind `approver`.
- Concurrency across processes. State (dry-run hashes, spent tokens) is in memory
  in one process. The audit log is a single-writer file with no locking.
- State drift between dry run and execution. The dry run is not a lock: if the
  sheet changes after approval, a call can fail mid-run (covered by undo, not
  prevented).
- A crash between applying a call and logging it. The log is written after each
  call, so that one call could be missing from the undo record.
- Real API behavior: rate limits, partial server-side failures, eventual
  consistency, and the quirks of any specific product. The mock is a model of
  common failure shapes, not a clone.
- Preview fidelity for multi-step plans. Each call is previewed against the
  current state, so a call that depends on an earlier call's output cannot be
  previewed accurately.
- Thresholds such as `BLANK_RATIO` and the person-column pattern are illustrative.
