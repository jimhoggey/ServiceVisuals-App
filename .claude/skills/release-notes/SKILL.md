---
name: release-notes
description: Draft the whatsnew.py NOTES entry and the release commit message for a version, from the commits and flow-doc entries since the last tag. Use when preparing a release, writing What's New copy, or writing a release commit message. Drafts only — /release does the bump, tag and push.
---

# Release notes for Service Visuals

Two pieces of writing, both in the same voice, both easy to get subtly
wrong: the **What's New card** a volunteer reads inside the app, and the
**release commit message** that records why the version exists.

This drafts them. It never bumps `version.py`, tags, or pushes — that is
`/release`, which the owner invokes deliberately.

## 1. Gather what actually changed

```bash
LAST=$(git describe --tags --abbrev=0)
git log --oneline $LAST..HEAD
git diff --stat $LAST..HEAD
```

Then read `docs/user-flows.md` for entries still marked *Unreleased*. Those
are confusions a real volunteer hit, written from their side — the best
source for what a note should say. They also need dating with this version
as part of the release (see `CLAUDE.md`).

Ignore anything invisible to a volunteer: refactors, test additions, spec
edits, agent and process files. A release of only those gets an empty
`NOTES` list — `notes_for()`'s fallback handles it.

## 2. The What's New lines

`notes_for(version, limit=3)` shows **three lines**, this version's first.
So three good lines is the practical maximum; more will not be read.

Order them by what a volunteer needs first:

1. **Anything that moved or was renamed.** Someone updating from the last
   version goes looking for the old button. Tell them where it went. This
   outranks even a bigger fix, because it is the one that makes them think
   the app is broken.
2. **The new thing they can now do**, in the words they would use.
3. **Fixed: …** for a bug they hit.

Rules the copy has to hold to:

- Written to be read aloud by a volunteer, not a release engineer.
- Keep each line under ~90 characters (`scripts/smoke.py` enforces ≤110;
  three older lines run to 92/94/106 and are left alone).
- Name the control exactly as it appears on screen — `IMAGES`, not "the
  images toggle".
- Lead with what it is FOR, not what it is. No codec names, no jargon.
  Tech detail belongs in the UI's dim second line, not here.
- Prefix the first line of a topic with its area (`Timer:`,
  `Background:`, `YouTube download:`); continuation lines carry no prefix.
- `Fixed:` prefix for fixes.

Put the entry at the TOP of the `NOTES` dict in `whatsnew.py`, formatted
like its neighbours: source lines under 79 columns, split across two
string literals where needed.

## 3. The release commit message

Shape, matching the repo's history:

```
Area: what changed, in plain words (vX.Y.Z)

The problem this solves, in the user's terms. Name the concrete thing —
the measurement, the failure, the symptom they reported. Not a list of
files.

The tradeoff or limit worth recording, and what was actually verified.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

Two things to be strict about:

- **Verification claims must be true.** Say what ran — the widths a review
  actually covered, the number of tests, the measured timing. If something
  could not be checked (anything Windows-only), say that plainly rather
  than implying it was tested. The owner has asked for that honesty
  explicitly.
- **Record the open point** if one survives the release, so the next
  session inherits it instead of rediscovering it.

## Worked examples in the repo

- `whatsnew.py` — every shipped entry; 1.33.0 through 1.37.0 are the
  closest to current voice.
- `git log --grep="(v1\." --format="%B" | head -60` — the commit prose.
