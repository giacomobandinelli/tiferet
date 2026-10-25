# 0001. Record architecture decisions

- Status: Accepted
- Date: 2026-10-25

## Context

Tiferet is built in phases by developers and Claude Code agents working in separate sessions (plan §8, §11). Without a record of what was decided and why, later sessions reopen settled questions or reverse decisions without knowing it. The plan requires decisions and their reasons to be kept as short decision records in `docs/decisions/` (§8.5).

## Decision

Significant decisions are recorded in this folder, one file per decision, written for an external reader (§8.1).

- Files are named `NNNN-short-title.md`, numbered in sequence and never renumbered.
- An accepted record is not rewritten. A changed decision gets a new record that supersedes the old one, and the old record's status is updated to point to it.
- A decision that changes the specification updates `docs/plan.md` in the same change.
- Records follow the template below.

## Consequences

- Sessions read the relevant records before revisiting a topic, and a settled decision is reopened only through a new record.
- `docs/plan.md` states what the system is; decision records state why.
- Routine choices with no lasting effect are not recorded.

## Template

```markdown
# NNNN. Title

- Status: Proposed | Accepted | Superseded by NNNN
- Date: YYYY-MM-DD

## Context

The problem, the constraints and the options considered.

## Decision

What was decided, in full sentences.

## Consequences

What becomes easier or harder as a result, and what follows from it.
```
