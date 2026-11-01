# agent-tool-safety

[![tests](https://github.com/jaysonqin-dev/agent-tool-safety/actions/workflows/tests.yml/badge.svg)](https://github.com/jaysonqin-dev/agent-tool-safety/actions/workflows/tests.yml)

AI agents that can write to team systems such as spreadsheets and trackers tend to
fail in boring, costly ways: malformed or orphaned records left behind by
trial and error, silent overwrites, and actions nobody approved. This repository is
a small safety layer for tool-using agents, plus a mock of a work-management
spreadsheet API that has the validation quirks that make agent writes fail. It is
Python 3.9, standard library only, with no network access and no dependencies.

## What this demonstrates

- Risk-tiered tools: every tool is registered as read, reversible write,
  irreversible write, or external notification, and policy is decided from that tier.
- Mandatory dry run: a write plan cannot execute until a preview with no side
  effects has returned a before/after diff and found no failing call.
- Approval bound to the exact plan: the approval token is signed over the plan's
  SHA-256 hash, expires, and works once, so a plan edited after approval is refused.
- An append-only, hash-chained audit log that records undo data, detects edits,
  reordering and deletion, and is the only source `undo` will trust.
- A diagnosis procedure for generic API errors that uses one disposable row and
  always removes it, instead of probing the real sheet with guesses.

## Quickstart

```
python3 -m unittest discover -s tests
python3 examples/demo.py
```

No install step is needed. The suite has 138 tests and all pass (`Ran 138 tests ... OK`).

## Demo output

This is the real output of `python3 examples/demo.py`.

```
== 1. The naive copy fails, and the error does not say why ==
source row: {'Task': 'Draft test plan', 'Status': 'In Progress', 'Owner': 'farid@example.com', 'Reviewer': 'Priya Shah', 'Hours': 6, 'Areas': 'Design, QA', 'Summary': 'Draft test plan [In Progress]'}
add_row(source cells) -> Value is not supported for this column type

== 2. Classify every column before writing ==
column    kind                               action         note
Task      text                               copy           value: 'Draft test plan'
Status    picklist                           copy           value: 'In Progress'
Owner     contact                            blank !        not in the approved contact list; left blank
Reviewer  text                               needs human !  a name without a contact record; cannot be resolved safely
Hours     number                             copy           value: 6
Areas     multi-picklist (reported as TEXT)  copy           options plus matching values: treated as multi-select
Summary   formula                            skip           computed by the sheet; direct writes are rejected

== 3. Dry run: what would change ==
dry run of plan 1c56f0d04f5c (no changes made)
  [0] sheet.add_row (write_reversible) -> add_row sheet 1
        after  {'Task': 'Draft test plan', 'Status': 'In Progress', 'Hours': 6, 'Areas': ['Design', 'QA']}

== 4. Approval is bound to the exact plan ==
edited plan with the old token -> refused: ApprovalMismatch: token approves 1c56f0d04f5c but plan is 20bc76a6a72d
rows in sheet: 1

== 5. Approved execution ==
run run-1: ok, new row id 2
new row: {'Task': 'Draft test plan', 'Status': 'In Progress', 'Owner': None, 'Reviewer': None, 'Hours': 6, 'Areas': 'Design, QA', 'Summary': 'Draft test plan [In Progress]'}

== 6. Audit log verifies ==
verify: ok=True entries=6

== 7. Tampering is detected, and undo refuses to trust a bad log ==
verify after edit: ok=False (entry 3 content does not match its hash)
undo -> AuditIntegrityError

== 8. Undo restores the sheet ==
undone calls: [0]
sheet identical to before the run: True

== 9. Diagnose a rejected write with one disposable row ==
offending columns: ['Areas'] (5 probes)
rows before/after diagnosis: 1/1
orphan audit since diagnosis started: []
audit log verifies: True
```

## Failure modes, mechanisms, and the tests that prove them

| Failure mode | Mechanism | Test |
| --- | --- | --- |
| Displayed values copied back as written values are rejected | Classify every column before writing; multi-select detected from options and existing values | `test_naive_copy_of_the_displayed_row_is_rejected`, `test_disguised_multi_select_is_detected_and_written_as_a_list` |
| Formula column written to | Formula columns are always skipped | `test_random_schemas_never_produce_a_formula_write_or_a_rejected_payload` |
| Contact assigned outside the approved list | Allow-list; otherwise blank and flagged | `test_contact_outside_the_allow_list_is_left_blank_and_flagged` |
| Orphan rows from trial and error | One disposable row, deleted in `finally` | `test_uses_exactly_one_test_row_and_removes_it`, `test_cleans_up_when_the_bisection_raises_part_way` |
| Earlier leftovers go unnoticed | `orphan_audit` flags blank, duplicate and error-formula rows | `test_mostly_blank_row_is_flagged`, `test_duplicate_of_an_older_row_is_flagged_but_the_original_is_not`, `test_healthy_rows_are_not_flagged` |
| A write that would be rejected reaches the real system | Dry run previews and validates every call | `test_invalid_cell_is_caught_by_the_dry_run_and_nothing_is_written` |
| Silent overwrite | Dry-run before/after diff, plus undo data in the log | `test_update_dry_run_shows_old_and_new_values`, `test_update_undo_restores_a_disguised_multi_select_exactly` |
| Unapproved write or delete | Policy requires a token for every non-read tier | `test_execute_without_token_is_refused`, `test_delete_needs_approval_and_cannot_be_undone` |
| Approved one plan, ran another | Token signed over the plan hash | `test_token_for_plan_a_cannot_run_plan_b`, `test_plan_edited_after_approval_is_refused`, `test_editing_a_token_to_match_another_plan_breaks_the_signature` |
| Stale or replayed approval | Expiry and single use | `test_expired_token_is_refused`, `test_token_is_single_use` |
| Run fails half way | Each call logged with undo data; stop at first error | `test_partial_run_leaves_a_log_that_still_restores_everything` |
| Undo does not return the exact state | Undo replays logged data newest first | `test_undo_restores_the_exact_prior_state` |
| History edited after the fact | Hash chain over every entry | `test_editing_an_entry_is_detected`, `test_deleting_a_middle_entry_is_detected`, `test_reordering_entries_is_detected`, `test_random_tampering_is_always_detected` |
| Forged undo data used as a write primitive | Undo verifies the chain first | `test_undo_refuses_a_log_that_fails_verification` |

The design rationale and threat model are in [docs/design.md](docs/design.md).

## Layout

```
agent_tool_safety/
  risk.py        risk levels, tool registry and decorator
  plan.py        ToolCall, Plan, stable plan hash
  policy.py      approval rules, signed single-use tokens
  audit.py       hash-chained JSONL log
  executor.py    dry run, approve, execute, undo
  sheets/
    mock.py      in-memory spreadsheet API with strict, generic validation
    classify.py  per-column decisions before any write
    tools.py     sheet tools registered with risk tiers
    duplicate.py duplicate a row through the executor
    diagnose.py  disposable-row bisection and orphan audit
examples/demo.py
tests/
```

## Honest limits

- The spreadsheet service is an in-memory mock written for this repository. It
  models common failure shapes and is not a clone of any product. Nothing here talks
  to a real API.
- Single process. Dry-run state and spent tokens live in memory, and the audit log
  is one file with one writer. Authentication, concurrency across processes, and real
  rate limits are out of scope.
- Truncating the end of the audit log is only detectable if you keep the head hash
  elsewhere and pass it to `verify(expected_head=...)`.
- The dry run is a preview, not a lock. If the sheet changes after approval, a call
  can still fail mid-run; undo then restores what was applied.
- Thresholds (`BLANK_RATIO`, the person-column pattern, token lifetime) are
  illustrative, not tuned.
- Whether approval is a human-side action is a convention of how you wire this up.
  Nothing here stops an agent from being handed `Executor.approve`.

## License

MIT. See [LICENSE](LICENSE).
