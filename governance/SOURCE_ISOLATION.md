# RVSC Source Isolation Policy

Checkpoint: RVSC-013 — Git / Source Isolation  
Constitutional alignment: RVSC Constitution

## Purpose

RVSC separates the company control plane from every managed product repository. This prevents accidental cross-project edits, protects product history, enables bounded parallel work, and gives each work package a reviewable source boundary.

## Repository classes

### Control-plane repository
`GitSly1/RAMTech-RVSC-Control-Center`

Allowed content:
- RVSC constitution, governance, and operating rules
- repository registry and project metadata
- work-package schemas and templates
- orchestration/configuration definitions
- cross-project validation and release policy
- company-level documentation

Forbidden content:
- managed-product source
- product-local secrets, credentials, or runtime data
- agent-owned copies of security authority or credentials

### Product repository
Examples: `GitSly1/RAMTech-SEMANTIQ`, `GitSly1/RAMTech-MOXIE`

Allowed content:
- product source code and tests
- product-specific documentation/configuration
- product release/build definitions
- product-local RVSC metadata and constitutional pointer documents

Forbidden content:
- RVSC control-center source
- unrelated product source
- cross-project orchestration/security authority that belongs in the control plane
- credentials or security authority exposed to an agent workspace

## Work-package isolation

Every implementation work package must declare:
1. Work-package ID.
2. Target repository.
3. Controller-authorized target workspace.
4. Allowed paths/modules.
5. Forbidden paths when applicable.
6. Base branch.
7. Work branch.
8. Acceptance criteria.
9. Required tests/validation and evidence.
10. Required security/QA gates.

Mission declarations are requests, not authority. Configuration/controller authorization and Git-origin provenance independently establish the trusted target. An agent cannot select or expand its own workspace, repository, path, process, network, credential, or promotion authority.

## Filesystem authority

`AGENT_FILESYSTEM_AUTHORITY = ASSIGNED_WORKING_FOLDER_ONLY`

Agents have no discretionary read, enumeration, search, execution-from, copy, write, delete, rename, metadata, or traversal authority outside the assigned working folder. Cross-agent filesystem access is forbidden.

Necessary OS/runtime dependencies outside the workspace are narrow controller-owned, preapproved, read-only dependencies and are not discretionary agent data authority.

Boundary enforcement must account for parent traversal, absolute paths, symlinks, junctions/reparse points, hard links, alternate data streams, UNC/mapped/network/removable paths, and path normalization/case edge cases.

## Branch convention

Use `rvsc/<work-package-id>-<short-slug>`.

Direct product-development changes to `main` are prohibited by RVSC operating policy. Changes are prepared on an isolated work branch and reviewed through governed QA/release gates.

No force push, autonomous merge, or autonomous promotion is permitted.

## Cross-repository rule

The former assumption that an agent may freely read multiple repositories for context is superseded by the RVSC Constitution. An agent receives only its assigned workspace. Cross-repository context, when legitimately required, must be selected, verified, sanitized, and transferred by a trusted controller/broker into the authorized workspace without granting ambient access to the source repository.

Multi-repository writes require explicit human/governance authorization, separate bounded workspaces/capabilities, and independent evidence. A mission cannot create that authority by declaration.

## Control-center rule

The Control Center may reference product repositories by trusted metadata, issue/PR identifiers, branch names, commit SHAs, release identifiers, and status. It must not duplicate product source as a synchronization mechanism.

## Product rule

A managed product repository may contain an `.rvsc/` directory for product-local metadata and boundaries and a pointer to the governing constitution. It must not become a mutable copy of Control Center security authority.

## No transitive egress

Source isolation includes transport isolation. Agents receive no direct Git-network authority, package-repository authority, arbitrary loopback/LAN/Internet access, credentials, or generic controller/other-agent proxying. Required external actions use controller-owned semantic capabilities with exact destinations and policy validation.

## Release traceability

A completed work package should be traceable through:

`Work Package → Authorized Workspace → Repository/Origin → Branch/Commit → Validation Evidence → Independent QA → Pull Request/Release Gate → Human-Gated Promotion → Release/Checkpoint`

## Constitutional rule

Where this policy conflicts with the RVSC Constitution, the constitution and the more restrictive valid authority govern. Conflicts are surfaced and fail closed rather than silently weakened.
