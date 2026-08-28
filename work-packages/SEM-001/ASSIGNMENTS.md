# SEM-001 Agent Assignments

- Command Authority: Max Command
- Implementation Agent: SEMANTIQ Implementation Agent
- Review Agent: RVSC Review Agent
- QA Agent: RVSC QA Agent
- Release Gate: RVSC Work Package Controller

## Separation rule

The implementation role may write only to `GitSly1/RAMTech-SEMANTIQ` on branch `rvsc/SEM-001-bootstrap` and only within the Work Package allowed paths.

The Review role evaluates the resulting diff against the Work Package and does not create product implementation changes unless SEM-001 is formally returned to `in_progress` for rework.

The QA role independently verifies required checks and acceptance evidence before Release Gate eligibility is evaluated.

Mechanical GitHub connector calls may use the authenticated `GitSly1` identity; role separation is represented by lifecycle stage, evidence records, and prohibited actions at each stage.
