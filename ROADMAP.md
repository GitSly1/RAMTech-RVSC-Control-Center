# RAMTech Virtual Software Company — Versioned Roadmap

Roadmap version: 1.0
Command: Max Command
Measurement rule: progress is credited only when the named checkpoint has versioned acceptance evidence on `main`.

## RVSC Control-Plane Roadmap

| Checkpoint | Scope | Weight | State |
|---|---|---:|---|
| RVSC-013 | Git / source isolation | 15 | PASS |
| RVSC-014 | Work Package contract, lifecycle/controller, end-to-end proof | 20 | PASS |
| RVSC-015 | Versioned roadmap, project registry, dependency and readiness denominator | 10 | IN PROGRESS |
| RVSC-016 | Agent registry, role capabilities, assignment/ownership controls | 15 | PLANNED |
| RVSC-017 | Automated evidence collection and policy enforcement | 15 | PLANNED |
| RVSC-018 | Release governance, versioning, rollback and artifact controls | 10 | PLANNED |
| RVSC-019 | Multi-project parallel orchestration and dashboard consolidation | 10 | PLANNED |
| RVSC-020 | Operating-model production qualification | 5 | PLANNED |

Total denominator: 100 weighted points.
Current verified credit before RVSC-015 acceptance: 35/100.

## SEMANTIQ Product Roadmap v1

| Workstream | Scope | Weight | Verified state |
|---|---|---:|---|
| SEM-001 | Controlled product bootstrap / identity / test harness | 5 | PASS |
| SEM-002 | Source baseline inventory and legacy capability migration map | 10 | PLANNED |
| SEM-003 | Intent + semantic schema domain model | 12 | PLANNED |
| SEM-004 | Mechanics discovery engine contracts | 12 | PLANNED |
| SEM-005 | Deep discovery + field candidate model | 12 | PLANNED |
| SEM-006 | Relationship traversal model | 10 | PLANNED |
| SEM-007 | Data cleaning & normalization core | 12 | PLANNED |
| SEM-008 | Extraction runner / pagination / resource handling | 10 | PLANNED |
| SEM-009 | Modern desktop workflow shell and integration | 8 | PLANNED |
| SEM-010 | Regression suite across reference sites | 5 | PLANNED |
| SEM-011 | Packaging, migration, release qualification | 4 | PLANNED |

Total denominator: 100 weighted points.
Current verified SEMANTIQ product readiness: 5/100.

## Dependency policy
No downstream work package may claim readiness credit until its acceptance evidence is merged into the Control Center. Product work must use an isolated `rvsc/` branch, explicit path scope, review, QA and release-gate evidence.

## Immediate sequence
1. Accept RVSC-015 roadmap baseline.
2. Dispatch SEM-002 to inventory the available SEMANTIQ repository/source baseline without modifying protected product behavior.
3. Establish RVSC-016 agent registry in parallel where it does not conflict with SEM-002.
