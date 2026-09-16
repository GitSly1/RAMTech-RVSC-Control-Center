# RVSC Unattended Orchestration Architecture

Constitutional alignment: RVSC Constitution

## Objective

RVSC orchestrates bounded computational teammates under deterministic, fail-closed governance. Automation exists to improve resolution efficiency without transferring human/security authority to agents. Product source remains isolated from the control plane.

## Trust hierarchy

`Human Owner → Trusted Security/Enforcement Core → Independent FAE Watchdog → FAE Supervisor Control Sandbox → Agent Execution Sandboxes`

FAE is supervisor but not security root. Agents do not control their own containment, evidence, credentials, network, watchdog, or promotion authority.

## Control flow

1. A work package enters `ready`.
2. The orchestrator validates constitutional compliance, repository/origin, controller-authorized workspace, branch/path scope, dependencies, priority, inputs, containment, and required capabilities.
3. The resource resolver selects an eligible qualified worker.
4. The worker receives only bounded semantic capabilities inside its assigned sandbox/workspace.
5. Evidence is collected from exact provenance, changed files, validations, logs, security telemetry, and handoff metadata.
6. Independent QA evaluates exact transferred evidence and acceptance criteria.
7. PASS routes to governed release gates; FAIL/BLOCK routes to root-cause/corrective work or escalation.
8. Promotion occurs only through applicable human/release authority.
9. Incidents and repeated infractions are correlated outside agent control.

## Responsible Performance Loop

`Attempt → Evidence → Outcome → Root Cause → Proposed Correction → Compliance Gate → Authorized Action or Compliant Alternative/Escalation → Measurement → Improvement`

Resolution efficiency and compliance are measured together. Honest failure reporting is protected. A compliant stop at an authority boundary is not an infraction. A noncompliant success is not accepted.

## Main application responsibilities

The orchestration application owns deterministic trigger/event normalization, work-package queue/dependencies, project priority/resource allocation, capability registry, bounded worker invocation, timeout/retry policy, state transitions, evidence collection, QA routing, release-gate evaluation, escalation, telemetry, and dashboard emission.

It does not own product source and does not convert an agent recommendation into privileged host action automatically.

## Worker abstraction

Workers may use different inference/execution providers, but provider choice never weakens the security contract.

A worker receives only the information/capabilities necessary for the assigned objective. It does not receive ambient host authority, general shell authority, credentials, arbitrary Internet/LAN/loopback, generic proxying, unrestricted Git network, package-repository access, or other-agent filesystem access.

Worker output is untrusted until deterministically verified.

## Capability broker

Necessary external actions use controller-owned semantic capabilities. The broker owns exact operation/destination, schemas, credentials, sanitation, rate/size limits, logging, revocation, and policy. Arbitrary URL, arbitrary command, agent-selected destination, credential disclosure, and generic transport proxy capabilities are forbidden.

## No transitive egress

Direct network denial is insufficient. Reachability is evaluated transitively across local services, model/provider endpoints, Git helpers/remotes, package caches/proxies, DNS, named pipes/IPC, controller interfaces, FAE/watchdog interfaces, other agents, shared directories, and equivalent intermediaries.

If an agent can reach A and A can reach B, B is considered potentially reachable unless a qualified semantic boundary proves otherwise.

## FAE and watchdog

FAE observes telemetry, correlates incidents, alarms, reports, and may request quarantine. It cannot modify security/system configuration, expand authority, suppress deterministic severity, erase evidence, disable its watchdog, expose credentials, or rewrite governance.

An independent watchdog monitors FAE and agent security state. Monitoring failure fails closed.

## Priority policy

Project priority affects dispatch order and resource allocation but never weakens QA, security, containment, evidence, or authority controls. Security supersedes performance when they genuinely conflict; resource efficiency remains an optimization after safety requirements are met.

## Enhancement doctrine

Technical leadership proactively identifies justified enhancements from evidence, recurring failures, QA patterns, incidents, inefficiencies, and architectural needs. Enhancements inspect the dependency cone, evaluate authority/attack-surface/privacy/safety/resource implications, preserve contracts or govern their replacement, and undergo deterministic regression plus independent qualification appropriate to risk.

Improvement changes strategy/capability, not authority.

## Safety and governance invariants

- human authority is supreme
- greater intelligence never implies greater authority
- no worker expands its own scope
- agent system-configuration authority is none
- agent filesystem authority is assigned working folder only
- direct and transitive egress are denied unless mediated by qualified semantic capabilities
- credentials never enter untrusted agent environments
- implementer and QA are independent
- failed tests and incidents cannot be silently ignored
- retries are bounded
- blocked work is explicit
- agent output is untrusted until verified
- unknown security state fails closed
- no force push, autonomous merge, or autonomous promotion
- monitoring/evidence remain outside agent control
- external publishing, spending, destructive production changes, credentials, privilege, and material authority remain governed/human-gated

## Constitutional rule

The RVSC Constitution is the governing doctrine. Lower-level routes, prompts, missions, priorities, or product instructions cannot weaken it. Conflicts are surfaced and fail closed.
