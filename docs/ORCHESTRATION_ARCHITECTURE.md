# RVSC Unattended Orchestration Architecture

## Objective
RVSC agents operate unattended by default and are supervised by exception. GitHub is the durable control plane and evidence store; the orchestration engine interprets triggers, routes work to the correct logical path, invokes bounded workers, enforces QA separation, and advances work packages automatically.

## Control flow
1. A work package enters `ready`.
2. The orchestrator validates repository, branch, path scope, dependencies, priority, and required inputs.
3. The resource resolver selects an eligible worker by capability and project allocation.
4. The worker executes only the bounded WP scope on the declared branch.
5. Evidence is collected from commits, changed files, tests, logs, and handoff metadata.
6. Independent QA evaluates the evidence and acceptance criteria.
7. PASS routes to PR/merge gates; FAIL routes automatically to rework.
8. Merge closes the WP and unlocks dependent work.
9. Only Decision Required, User Action, UAT Ready, or Significant Risk interrupts the user.

## Main application responsibilities
The orchestration application owns:
- trigger ingestion
- event normalization
- work-package queue
- dependency graph
- project priority and resource allocation
- agent capability registry
- worker invocation adapter
- retries and timeout policy
- state transitions
- evidence collection
- QA routing
- merge-gate evaluation
- escalation policy
- dashboard event emission

It does not own product source. Product code remains isolated in product repositories.

## Trigger model
Triggers are declarative and versioned in `config/orchestration.yaml`. A trigger resolves to a named route. A route is an ordered list of actions. Actions are deterministic controller operations or calls through worker adapters.

This makes the execution path visible and auditable in Git rather than hidden in conversational state.

## Worker abstraction
Workers may later be backed by n8n, GitHub Actions, local runners, API-based agents, or other execution providers. The orchestration core should not depend on a single worker platform.

A worker receives:
- agent identity and role
- project/repository
- WP id
- base/work branch
- allowed/forbidden paths
- objective
- acceptance criteria
- inputs
- expected deliverables
- validation requirements

A worker returns:
- status
- files changed
- validation results
- risks
- commit/PR evidence
- structured logs

## Priority policy
SEMANTIQ remains P0 and may preempt shared capacity. MOXIE remains P1. Project priority affects dispatch order and worker allocation but never weakens QA or scope controls.

## Safety and governance invariants
- no worker expands its own scope
- no direct routine product development on `main`
- implementer and QA are logically separate
- failed tests cannot be silently ignored
- retry count is bounded
- blocked work is explicit
- merge requires evidence and QA acceptance
- external publishing, spending, destructive production changes, and other high-impact actions require explicit policy authorization

## First implementation milestone
RVSC-016 will establish the agent registry plus orchestration core capable of loading the trigger registry, resolving routes, enforcing transition rules, and producing a deterministic execution plan for a READY work package. External worker execution adapters follow as the next layer.
