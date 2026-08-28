# RVSC Work Package Lifecycle

Checkpoint: RVSC-014 — Work Package Contract

## Lifecycle

`draft → ready → in_progress → review → accepted → closed`

Controlled exception paths:
- `ready → blocked`
- `in_progress → blocked`
- `blocked → in_progress`
- `blocked → rejected`
- `review → in_progress` for rework
- `review → rejected`
- `rejected → in_progress` for authorized rework
- `rejected → closed`

No other state transition is valid.

## Dispatch gate

A work package may move from `draft` to `ready` only when it declares:
- target repository
- base branch
- isolated work branch
- allowed paths
- objective
- acceptance criteria
- validation requirements
- mandatory handoff reporting

A work package may move to `in_progress` only after the target branch exists and differs from the base branch.

## Review gate

A work package may move to `review` only when implementation has stopped changing long enough to produce a handoff containing:
- files changed
- validation results
- risks
- commit and/or PR identifier

## Controlled merge eligibility

A product PR is merge-eligible only when all of the following are true:
1. Work package status is `review`.
2. Actual repository matches the declared target repository.
3. Base branch remains `main`.
4. Work branch uses the `rvsc/` prefix and differs from `main`.
5. Every changed file is inside an allowed path and outside forbidden paths.
6. Every acceptance criterion has verified PASS evidence.
7. Every required validation check has verified PASS evidence.
8. Handoff reporting is complete.
9. A pull request exists and GitHub reports it mergeable.
10. Required PR review is approved.
11. QA/acceptance is recorded as PASS.

Only after those gates pass may Command authorize merge.

## Separation of roles

For a controlled test, RVSC records role assignments even when the same authenticated GitHub identity performs the mechanical connector calls.

- **Max Command** — creates/authorizes work packages and controls lifecycle transitions.
- **Implementation Agent** — writes only within declared repository/path scope.
- **Review Agent** — evaluates diff/scope/acceptance independently from implementation evidence.
- **QA Agent** — verifies required checks and records acceptance evidence.
- **Release Gate** — evaluates merge eligibility; it does not infer PASS from implementation completion.

## Rework

Any review or QA defect moves the work package from `review` back to `in_progress`. The defect and corrective commit must be recorded. Review and QA are then repeated before merge eligibility is reconsidered.

## Evidence chain

Each accepted package must be traceable as:

`Work Package → Assignment → Target Repo → Isolated Branch → Commits → Validation → Handoff → PR → Review → QA → Merge Gate → Merge Commit → Closed`
