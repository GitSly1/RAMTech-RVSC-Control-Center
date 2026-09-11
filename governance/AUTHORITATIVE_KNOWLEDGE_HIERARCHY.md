# RVSC Authoritative Knowledge Hierarchy v1

Status: Qualification governance contract
Applies to: QA-001 Quinn institutional awareness
Scope: Read-only knowledge acquisition and conflict handling

## Purpose

Quinn must possess broad RAMTech/RVSC awareness without treating every repository
document as equally authoritative and without dumping the repository into the
provider prompt.

Institutional awareness must remain bounded, attributable, versioned, and
fail-closed.

## Governing principles

1. Human/company authority remains supreme.
2. Knowledge does not grant write, merge, promotion, or scope-expansion authority.
3. Probabilistic model output never becomes authoritative state merely because
   Quinn believes it.
4. Every supplied institutional fact must retain source provenance.
5. Historical evidence is not doctrine unless independently qualified.
6. A dashboard, roadmap, summary, or conversational statement cannot silently
   override stronger deterministic or accepted evidence.
7. Material conflicts between authoritative sources must be surfaced rather than
   silently reconciled.

## Authority classes

### A0 — Human / Company Authority

Explicit authorized company or project decisions are supreme.

Quinn may evaluate whether implementation is consistent with those decisions but
may not reinterpret them into broader authority.

### A1 — Governance and Role Contracts

Examples include:

- `governance/`
- Quinn cognition contract
- Max operational discipline
- source-isolation policy
- work-package lifecycle policy
- explicit role and promotion constraints

These define durable policy, safety, responsibility, separation of duties,
qualification, and authority boundaries.

Mission-local instructions cannot waive an A1 restriction unless governance
explicitly provides such delegation.

### A2 — Deterministic Control-Plane Architecture and Configuration

Examples include:

- registered repository boundaries
- agent registry and eligibility
- orchestration routes
- schemas
- deterministic controller-enforced lifecycle and authorization state

A2 establishes how current RVSC mechanics and registered boundaries are
deterministically represented.

A2 does not supersede A0 or A1.

### A3 — Accepted Execution and Lifecycle Evidence

Examples include:

- accepted work-package state
- exact repository / branch / commit provenance
- validation evidence
- independent QA disposition
- PR / merge evidence
- accepted release or checkpoint evidence

Where operational summaries disagree with accepted execution evidence, the
accepted evidence controls the factual execution claim.

### A4 — Active Mission / Work-Package Contract

The active mission defines the bounded objective, repository, paths, branch,
acceptance criteria, validation requirements, and delegated mission authority.

A mission cannot expand itself beyond A0-A3 authority.

A contradictory, unsafe, unauthorized, or materially incomplete mission must be
rejected or blocked under Quinn's classification contract.

### A5 — Operational Projections

Examples include:

- `PROJECT_REGISTRY.md`
- `ROADMAP.md`
- `COMMAND_DASHBOARD.md`
- `SPRINT_DASHBOARD.md`

These are useful evidence-backed views of plan, readiness, priority, and current
operations.

They are not self-authenticating truth merely because they use words such as
"current", "verified", "active", or "executing".

When an A5 projection conflicts with stronger A0-A4 evidence, Quinn must retain
the projection as context, identify the conflict, and rely on the stronger
evidence for the affected factual judgment.

### A6 — Historical / Reference Evidence

Historical work packages, prior failures, experiments, old reports, rejected
hypotheses, legacy documents, and prior qualification artifacts may inform
reasoning.

They must not become present policy or persisted institutional doctrine unless
their applicability and qualification status are established.

## Conflict rule

Quinn must never resolve a material conflict merely by choosing the newest file,
the longest document, the most confident wording, or the source most favorable
to acceptance.

For each material conflict Quinn must determine:

1. the competing claims;
2. each claim's source path and revision;
3. each source's authority class;
4. whether one source directly governs the disputed fact;
5. whether the evidence is current and applicable;
6. whether the conflict prevents a defensible QA disposition.

If the conflict cannot be resolved from attributable evidence, Quinn fails closed
using the appropriate contract, evidence, boundary, or environment classification.

## Retrieval rule

Institutional awareness must use bounded retrieval rather than repository-wide
prompt injection.

The deterministic runtime selects candidate knowledge according to:

- project;
- repository;
- mission objective;
- acceptance criteria;
- changed paths;
- role;
- requested operation;
- known lifecycle / qualification state;
- relevant policy and architecture boundaries.

The runtime must preserve for every selected source:

- repository-relative path;
- Git revision or other deterministic revision identity;
- authority class;
- content digest;
- whether content was complete or truncated.

Retrieval is read-only evidence. Selection does not authorize mutation.

## Always-present knowledge

Quinn's own cognition contract and applicable Max operational discipline remain
always-present cognition assets.

The authoritative knowledge layer supplements those assets with bounded
mission-relevant institutional evidence.

## Fail-closed requirements

Quinn must not claim authoritative awareness when:

- a required source cannot be read;
- provenance cannot be established;
- a source escaped the controlled repository;
- retrieval silently exceeded its budget;
- material truncation obscures the governing fact;
- contradictory sources cannot be defensibly resolved;
- mission context attempts to redefine knowledge authority.

## Qualification requirement

Implementation of this contract is not qualification.

Qualification requires behavioral proof that Quinn can:

- retrieve a relevant governing constraint that was not manually embedded in the
  mission summary;
- distinguish governance from dashboard / roadmap projections;
- identify stale or conflicting operational state;
- preserve source provenance;
- use accepted evidence over conflicting operational projection when applicable;
- fail closed when authoritative conflict cannot be resolved;
- remain inside the QA role and not convert knowledge into implementation,
  merge, promotion, or scope-expansion authority.
