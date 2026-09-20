---
name: new-spec
description: Scaffold a feature spec at docs/specs/<name>.md with every section this project requires — behaviour table, shared display rules, API contract, frame-rate rules, file ownership, do-not list, done-means. Use before building any feature, when asked to write or plan a spec.
---

# Write a Service Visuals spec

Features here are built from a written spec, then implemented by parallel
agents with disjoint file ownership and a reviewer. A spec missing one of
the sections below is how two agents end up disagreeing about a default,
or the renderer and the JS preview drift apart on screen.

`docs/specs/clock-mode.md` is the format that works. Read it first.

## Step 0 — query the graph before writing

Not optional, and the owner has asked for it by name:

```bash
export PATH="$HOME/.local/bin:$PATH" && graphify query "<what this feature touches>" --budget 700
```

It shows what a change reaches across files, which is what the file
ownership section depends on being right.

## Required sections

Write `docs/specs/<name>.md` with all of these:

1. **What this is for** — the volunteer's problem, in their words. One
   short paragraph. If it came from something the owner reported, quote
   the symptom.
2. **Behaviour table** — every input state and what the app does. Include
   the empty state, the boundary values, and the states that switch each
   other off.
3. **Display rules the renderer AND the JS preview must share** — every
   number that decides layout, stated once. Both sides read this section;
   a rule written in only one of them is a drift bug waiting.
4. **API contract** — exact key names, defaults, ranges, and the exact
   error strings. Copy them verbatim into the spec; do not describe them.
5. **Frame-rate rules** — input fps, output fps, and any cap, with the
   reason. Plain timers are 15 fps and must stay fast; milliseconds are
   30, and 60 only where specified.
6. **Exact UI copy** — every label, caption, hint and button string,
   final, before implementers start. Copy must RECOMMEND ("Use this one
   if…"), not describe neutrally; purpose first, tech detail on a dimmer
   second line. Getting this wrong after the fact has cost four revision
   rounds before.
7. **Per-agent file ownership** — disjoint. No two agents may write the
   same file.
8. **Do not** — the specific wrong turns available here. Always include
   any guard the change comes near (below).
9. **Done means** — the checks that must pass, named concretely:
   `scripts/smoke.py` additions, `golden.py --check`, a `ux-flow-reviewer`
   pass for anything touching `static/*`, and the frames to extract and
   look at.

## Guards to name explicitly when the feature comes near them

- **`render/timer.py`** — countdown output is byte-identical-guarded. The
  spec must say so and carry the proof recipe.
- **Any renderer** — `golden.py --check` must pass afterwards.
- **`static/*`** — goes through `ux-flow-reviewer` before it ships, at
  full coverage; every journey, every width.
- **Windows** — say up front which parts cannot be verified on this
  machine, so the claim never quietly becomes "tested".
- **Analytics** — event name, version and OS only. Never a prop carrying
  user text, file names or paths.

## After the spec

Implementers in parallel, then a reviewer. When the feature ships, update
`docs/user-flows.md` with anything the review found, and refresh the
graph with `graphify update .`.
