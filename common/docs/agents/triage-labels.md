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

## One terminal value beyond the five: `done`

The five roles above are all *open* states — they say who acts next, never that nobody does. A
closed-and-shipped issue needs a value too, and a local markdown tracker has no "closed" flag to
carry it (GitHub's `state` field is what the five roles lean on). So this repo adds exactly one
terminal value:

| Value  | Meaning                                                       |
| ------ | ------------------------------------------------------------- |
| `done` | Implemented, verified, and committed. No further action.      |

`wontfix` is the *other* terminal value — closed without the work being done. Use `ready-for-human`
when a human still has to act (verify on hardware, decide), not to mean "finished".

## How a label is applied here

This repo's issue tracker is local markdown (`docs/agents/issue-tracker.md`), not GitHub, so there
is no label API. A role is recorded as the value of the `Status:` line near the top of the issue
file — for example `Status: ready-for-agent`.

The six values above (five roles plus `done`) are the whole vocabulary; `tools/issue-status.ps1`
groups tickets by exactly these. A value may be followed by a note in the form ` (…)` or ` — …`
(for example `done (2026-09-02 — …)`); for these forms the script reads only the leading value token.

Wayfinder's workflow state is separate: its child tickets use a `Wayfinder:` line for `claimed` or
`resolved`. Do not put those values in `Status:`; a ticket may carry both a triage status and a
Wayfinder workflow state.
