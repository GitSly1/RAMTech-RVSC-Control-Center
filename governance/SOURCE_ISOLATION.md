# RVSC Source Isolation Policy

Checkpoint: RVSC-013 — Git / Source Isolation

## Purpose

RVSC separates the company control plane from every managed product repository. This prevents accidental cross-project edits, protects product history, enables parallel agent execution, and gives each work package a reviewable source boundary.

## Repository classes

### Control-plane repository
`GitSly1/RAMTech-RVSC-Control-Center`

Allowed content:
- RVSC governance and operating rules
- repository registry and project metadata
- work-package schemas and templates
- orchestration/configuration definitions
- cross-project validation and release policy
- company-level documentation

Forbidden content:
- SEMANTIQ product source
- source code belonging to another managed product
- product-local secrets, credentials, or runtime data

### Product repository
Example: `GitSly1/RAMTech-SEMANTIQ`

Allowed content:
- product source code
- product tests
- product-specific documentation
- product configuration
- product release/build definitions

Forbidden content:
- RVSC control-center source
- unrelated product source
- cross-project orchestration logic that belongs in the control plane

## Work-package isolation

Every implementation work package must declare:
1. Work-package ID.
2. Target repository.
3. Allowed paths or modules.
4. Forbidden paths when applicable.
5. Base branch.
6. Work branch.
7. Acceptance criteria.
8. Required tests or validation.

An agent must not modify a repository or path outside the declared scope without a new or amended work package.

## Branch convention

Use:
`rvsc/<work-package-id>-<short-slug>`

Examples:
- `rvsc/013-source-isolation`
- `rvsc/014-work-package-contract`
- `rvsc/sem-001-bootstrap`

Direct product-development changes to `main` are prohibited by RVSC operating policy. Changes should be prepared on a work branch and reviewed through a pull request.

## Cross-repository rule

A single agent may read multiple repositories when needed for context, but write authority is limited to repositories explicitly listed in the active work package. Multi-repository writes require an explicit multi-repository work package.

## Control-center rule

The Control Center may reference product repositories by metadata, issue/PR identifiers, branch names, commit SHAs, release identifiers, and status. It must not duplicate product source code as a synchronization mechanism.

## Product rule

A managed product repository may contain an `.rvsc/` directory for product-local RVSC metadata and boundaries. That directory must not become a copy of Control Center governance.

## Release traceability

A completed work package should be traceable through:
Work Package → Repository → Branch → Pull Request → Merge Commit → Validation Result → Release/Checkpoint.

## Enforcement level at RVSC-013

This checkpoint establishes the structural and procedural isolation contract. Automated enforcement through repository rulesets, CI checks, and work-package validators is scheduled for subsequent RVSC checkpoints.
