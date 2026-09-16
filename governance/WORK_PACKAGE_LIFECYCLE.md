# RVSC Work Package Lifecycle

Checkpoint: RVSC-014 — Work Package Contract  
Constitutional alignment: RVSC Constitution

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

## Governing performance loop

Every execution follows:

`Attempt → Evidence → Outcome → Root Cause → Proposed Correction → Compliance Gate → Authorized Action or Compliant Alternative/Escalation → Measurement → Improvement`

A task failure and a policy/security infraction are different facts. An honest stop at an authority boundary is compliant behavior. A technically successful result obtained by violating scope, security, provenance, or authority is not an accepted result.

## Dispatch gate

A work package may move from `draft` to `ready` only when it declares:
- target repository
- controller-authorized target workspace
- base branch and isolated work branch
- allowed/forbidden paths
- objective and acceptance criteria
- validation/evidence requirements
- security classification and required containment
- mandatory handoff reporting

Mission content is not authority. The controller/configuration independently validates workspace/repository/path/capability authorization and Git-origin provenance.

A work package may move to `in_progress` only after deterministic dispatch and security gates pass. Unknown or unqualified security state fails closed.

## Execution gate

During execution, agents cannot self-expand filesystem, repository, process, network, credential, provider, system-configuration, cross-agent, or promotion authority. If an apparent solution requires prohibited authority, the agent records the constraint, proposes a compliant alternative, or escalates.

Retries are bounded. Repeated prohibited attempts are recorded as security signals rather than treated as ordinary retries.

## Review gate

A work package may move to `review` only when implementation has stopped changing long enough to produce a handoff containing:
- exact repository/origin, branch, and commit provenance
- files changed
- validation results and evidence
- risks, failures, infractions, and unresolved constraints
- commit and/or PR identifier
- security/containment evidence required by the package

Claims are not verification. Evidence presence is not evidence verification.

## Controlled merge eligibility

A product PR is merge-eligible only when all applicable gates are verified:
1. Work package status is `review`.
2. Actual repository/origin/workspace matches independently authorized scope.
3. Base/work branches satisfy policy and the work branch differs from `main`.
4. Every changed file is inside authorized paths and outside forbidden paths.
5. Every acceptance criterion has verified PASS evidence.
6. Every required validation check has verified PASS evidence.
7. Handoff reporting is complete and truthful.
8. Required security/containment gates are qualified.
9. A pull request exists and required repository checks pass.
10. Required independent review is complete.
11. Independent QA/acceptance is recorded with verified evidence.
12. No unresolved constitutional or authority conflict remains.
13. Human/release authorization is present wherever promotion, production, credentials, external publishing, spending, destructive action, or material authority is involved.

Implementation completion alone never grants merge or promotion authority.

## Separation of roles

- **Human Owner / Release Authority** — retains final authority for privileged/promotion decisions.
- **Max Command / Technical Lead** — creates/authorizes bounded work packages, controls lifecycle policy, proactively identifies justified enhancements, and does not silently expand agent authority.
- **FAE/Supervisor** — observes/correlates/alarms and may request quarantine; it is not security root or self-authorizer.
- **Implementation Agent** — writes only within its assigned working folder and granted capabilities.
- **Independent Review/QA Agent** — evaluates exact transferred evidence/provenance independently and cannot inherit implementer authority.
- **Trusted Security/Enforcement Core and Watchdog** — enforce containment outside untrusted agent control.
- **Release Gate** — evaluates eligibility; it never infers PASS from implementation completion.

## Rework

Any review/QA/security defect routes to controlled rework or block. The defect, evidence, root cause, proposed correction, compliance determination, and corrective commit are recorded. Review and QA repeat before acceptance is reconsidered.

A proposed correction that violates policy is rejected even if technically effective; a compliant alternative is sought or the issue is escalated.

## Evidence and learning chain

Each accepted package remains traceable as:

`Work Package → Assignment → Authorized Workspace → Target Repo/Origin → Isolated Branch/Commit → Validation → Handoff → Independent QA → PR/Release Gate → Human-Gated Promotion → Closed`

Recurring failures, QA patterns, inefficiencies, incidents, and lessons feed the Enhancement & Lessons Register. Learning may improve strategy and capability; it does not expand authority.

## Constitutional rule

The RVSC Constitution governs this lifecycle. A lower-level work package, prompt, mission, deadline, priority, or automation cannot weaken it. Conflicts are surfaced and fail closed.
