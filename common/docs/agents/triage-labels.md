# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Changing labels

Add the new label first, then remove the old one in a separate `gh issue edit` call. When one call adds a label the tracker lacks and removes another, `gh` reports the failed add but still applies the removal, leaving the issue with no triage label. Check with `gh label list` first; if a label in the right-hand column is missing from the tracker, report it to the user.

## Project tracker

The tracker, terminal states, and Wayfinder conventions are project-owned. Follow
`docs/agents/workflow-project.md` for whether this project uses GitHub or local files.
