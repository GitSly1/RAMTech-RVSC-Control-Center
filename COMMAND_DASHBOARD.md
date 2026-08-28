# RVSC Command Dashboard

> Founder/Command visual tracker. Evidence-first: status changes must be backed by Git, CI, QA, PR, or merge evidence. Assignment alone does not count as execution.

## Executive lanes

| Priority | Project | Current state | Active checkpoint | Last verified evidence | Next gate |
|---|---|---|---|---|---|
| P0 | SEMANTIQ | ⚠️ EXECUTION GAP | Controlled mature-source migration not yet dispatched | `04177d2b` — SEM-002 baseline inventory | Create migration WP → branch → import → regression |
| P0 | RVSC Control Center | 🟢 EXECUTING | RVSC-016 unattended orchestration core | `cb023605` + `RVSC CI` run #1 SUCCESS | Independent QA → merge eligibility |
| P1 | MOXIE | 🟠 WAITING NEXT WP | MOX-001 foundation merged | `486ac39` — MOX-001 foundation | MOX-002 architecture foundation |

## RVSC-016 pipeline

`PLAN ✅ → CODE ✅ → COMMIT ✅ → CI 🟢 → QA ⏭ → PR OPEN ✅ → MERGE ⏳`

- Branch: `rvsc/016-agent-orchestration-core`
- PR: #9 — `RVSC-016: unattended agent orchestration core`
- CI: `RVSC CI` run #1 — SUCCESS
- Main protection: ACTIVE ruleset `Protect main`

## SEMANTIQ P0 pipeline

`BASELINE VERIFIED ✅ → MIGRATION WP ⏭ → CONTROLLED IMPORT ⏳ → REGRESSION ⏳ → ENHANCEMENTS ⏳ → GREEN SCOPE ⏳`

Critical evidence rule: SEMANTIQ is not considered actively developing until product-repository evidence appears on a bounded work branch.

## MOXIE P1 pipeline

`FOUNDATION ✅ → MOX-002 ARCHITECTURE ⏭ → FIRST VERTICAL SLICE ⏳ → QA ⏳`

## Resource-agent board

| ID | Resource | Role | Lifecycle state | Evidence / note |
|---|---|---|---|---|
| CMD-001 | Max | Command / CDO | EXECUTING | Active command, orchestration and evidence coordination |
| ARC-001 | Maya | Architecture & Requirements | AVAILABLE | No new execution evidence |
| DEV-001 | Daniel | Lead Software Engineer | AVAILABLE | SEMANTIQ execution evidence not yet present |
| UX-001 | Sofia | Product Experience / UI-UX | AVAILABLE | No active WP evidence |
| QA-001 | Quinn | Quality Engineering | ASSIGNED | Next gate: RVSC-016 independent QA |
| RND-001 | Nova | Research / Red Team | AVAILABLE | No active WP evidence |
| INT-001 | Ethan | Integration & Build | AVAILABLE | SEMANTIQ migration lane pending dispatch |
| REL-001 | Clara | Release & Documentation | AVAILABLE | No active WP evidence |
| DATA-001 | Iris | Data Engineering | AVAILABLE | No active WP evidence |
| DB-001 | Marcus | Database Engineering | AVAILABLE | No active WP evidence |
| WEB-001 | Kai | Web Extraction Engineering | AVAILABLE | SEMANTIQ migration lane pending dispatch |
| SEC-001 | Rhea | Application Security | AVAILABLE | No active WP evidence |
| PERF-001 | Leo | Performance Engineering | AVAILABLE | No active WP evidence |
| AUTO-001 | Alex | Automation & Integration | EVIDENCE DELIVERED | RVSC-016 orchestration/CI evidence exists |
| AI-001 | Mira | AI/ML Engineering | AVAILABLE | No active WP evidence |
| OPS-001 | Noah | DevOps & Runtime | ASSIGNED | CI/runtime lane; no independent new commit evidence yet |
| BI-001 | Elena | Analytics & BI | AVAILABLE | No active WP evidence |
| DOC-001 | Sam | Technical Documentation | UNKNOWN | Status not formally established |

## Health signals

- 🔴 **SEMANTIQ inactivity warning:** last verified product commit remains SEM-002. P0 product evidence must resume next.
- 🟢 **RVSC CI:** first automated pull-request validation run succeeded.
- 🔒 **Main branch governance:** active ruleset protects default branch against deletion/non-fast-forward updates and requires PR workflow.
- 🟠 **MOXIE:** intentionally secondary; no claim of current execution.

## Evidence semantics

`AVAILABLE → ASSIGNED → EXECUTING → EVIDENCE DELIVERED → QA ACCEPTED → MERGED`

Additional states: `BLOCKED`, `UNKNOWN`.

A resource becomes **EXECUTING** only when there is verifiable work evidence (e.g., branch activity, commit, workflow execution, or other accepted execution artifact). Assignment alone is not progress.
