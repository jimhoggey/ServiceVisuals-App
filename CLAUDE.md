# Service Visuals

A local desktop app for church tech volunteers: generates countdown timers, a
wall clock, spinner wheels, QR cards, motion backgrounds and a scoreboard
editor as ProPresenter-ready 1080p MP4s / PNGs. Flask on 127.0.0.1 + Pillow +
bundled ffmpeg, wrapped in pywebview, packaged by PyInstaller.

Owner: Fynn (GitHub `jimhoggey`). Repo: **jimhoggey/ServiceVisuals-App** (public).

## Working agreements

- **Consult the graphify knowledge graph before changing code, and before
  writing a spec.** From the repo root: `export PATH="$HOME/.local/bin:$PATH"
  && graphify query "<question>" --budget 700`. The PATH prefix is not
  optional — uv installs graphify to `~/.local/bin`, which subagent shells
  do not have, and agents without it hit "command not found" and quietly
  skipped the graph. It shows what a change
  touches across files. **Then update it as the last step of the change**, so
  the next agent queries a graph that still matches the code: `graphify update
  .` on a subagent. Use that subcommand rather than a bare `graphify .` — it
  re-extracts the code locally and needs no API key, where a full build asks
  for an LLM key to read `docs/` and would send repo content to a third party.
  `graphify-out/` is gitignored. Note: that build does not index `.css`, so
  style.css being reported "deleted" is a known quirk, not real.
- **Subagents run on Sonnet or Haiku — never Opus or Fable.** This is the
  owner's standing instruction, including for heavy background jobs.
- **Features are built from a written spec.** Put it in `docs/specs/<name>.md`
  with: behaviour table, display rules the renderer AND the JS preview must
  share, the exact API contract (key names, defaults, ranges, error strings),
  frame-rate rules, per-agent file ownership (disjoint), a "do not" list, and
  done-means. Then implementers in parallel + a reviewer. See
  `docs/specs/clock-mode.md` for the format that works, and the `new-spec`
  skill, which scaffolds those sections.
- **Verify by rendering and looking.** Every feature so far has shipped
  something the implementing and reviewing agents both missed that one
  extracted frame revealed in seconds. Render the real thing, pull frames with
  the bundled ffmpeg (`imageio_ffmpeg.get_ffmpeg_exe()`), and view them. Same
  for UI: drive it in the browser, don't ask the owner to check.
- **`docs/user-flows.md` is the memory of how volunteers think.** Read the
  screen's section before changing it, and update it after: add to "Found
  and fixed" with the version, and record any open question's answer. When
  the owner reports a confusion, add it under "Found, not yet fixed" before
  fixing it, so nothing is lost between sessions. graphify indexes it, so a
  query about a control surfaces its flow as well as its code.
- **`ux-flow-reviewer` is for user-flow DECISIONS, not a gate on every
  change** (`.claude/agents/ux-flow-reviewer.md`). Reach for it when the
  question is what a volunteer will believe — a control whose meaning is
  in doubt, a layout the owner has already been confused by, a screen
  nobody has walked. Do not run a half-hour full-coverage pass before
  every release: the owner stopped one mid-flight for exactly that, and a
  review that delays a ready feature is costing more than it finds. Scope
  it to the journeys actually in question, and say what you skipped.
  Verify the rest yourself in the browser, which is faster and usually
  enough.

## Running it

```bash
SERVICE_VISUALS_STATS=0 .venv/bin/python scripts/smoke.py   # the gate — must pass
PORT=8799 SERVICE_VISUALS_STATS=0 .venv/bin/python app.py   # dev server
```

- **Port 8765 is the owner's INSTALLED packaged app**, which does not reflect
  source changes. Always use `PORT=8799` for a source run, and leave 8765 alone.
- `scripts/smoke.py` is the entire test suite and CI runs it before every build.
  Add a check there for anything you add. It is deliberately OCR-free where it
  can be, so it runs on Linux CI.
- Always set `SERVICE_VISUALS_STATS=0` outside a real release so test runs don't
  land in the owner's analytics.
- `SERVICE_VISUALS_STATS=0 .venv/bin/python scripts/golden.py --check` must
  also pass after any change to a renderer, or any refactor.
- `.venv/bin/python` — the project venv, Python 3.9.

## Conventions

- **Python 3.9.** No walrus, no `match`, no `X | Y` type unions.
- 79-column lines, 4-space indent. (~84 pre-existing long lines; don't add more.)
- **Comment the WHY, not the what.** The codebase explains the reasoning behind
  non-obvious choices — often naming the bug that forced them. Match that.
- User-facing errors are plain English sentences a volunteer can act on.
- `version.py` is the single source of the version (`app.py`, `desktop.py` and
  CI all read it). Only a release bumps it.

## Things that will bite you

- **Countdown timer output is byte-identical-guarded.** Changing
  `render/timer.py` must not move a countdown pixel. Prove it: `git show
  HEAD:render/timer.py` (plus `encoder.py`, `fonts.py`) into a temp package,
  render 6 s classic/ring/bar old vs new, extract frames, `cmp` them.
- **Never commit the owner's church content.** `Grouppoints.png`,
  `ChatGPT Image*.png`, `Summit-*.png` are gitignored — keep it that way.
- `exports/` is the owner's real output folder in the packaged app. Delete any
  test renders you create there.
- User data lives in `~/.service-visuals` (API key, boards, update log,
  crash.log) so it survives updates — never inside the app bundle.
- **Analytics are surface-level by design.** `stats.py` sends event NAME +
  version + OS only, never content. Do not add a prop carrying user text, file
  names or paths. Crash reports send the error's *shape*, scrubbed.
- **Some of these rules are enforced by hooks now** (`.claude/settings.json`),
  so a Bash call can come back denied. It refuses a `git add`/`git commit`
  that would carry church content, an `app.py` or `scripts/smoke.py` run
  without `SERVICE_VISUALS_STATS=0`, and `app.py` on port 8765; it asks
  before `golden.py --record`. Editing a `.py` file runs pyflakes on it;
  editing a renderer reminds you which guard applies. Keep those commands
  path-independent — they resolve the venv from the edited file's own git
  root, because the previous hardcoded path silently disabled pyflakes for
  months after the repo moved machines, and this file is public.
- **Two reviewers exist for the things that can't be checked by running
  them**: `windows-compat-reviewer` for anything touching `updater.py`,
  `tools.py`, the encoders or packaging, and `supply-chain-reviewer` for
  changes to the code that downloads binaries or replaces the app. The
  `release-notes` skill drafts the What's New entry and the release commit.
- **Windows can't be tested here.** The self-update helper, `winocr` detection
  and the GPU encoder are macOS-unverifiable. Reason carefully, say plainly
  what is unverified, and prefer designs that fail visibly over silently.
- macOS self-update only works from `/Applications` (App Translocation runs a
  quarantined app from a read-only copy).
- **The YouTube downloader bundles nothing.** `tools.py` fetches yt-dlp and
  Deno (YouTube needs a JS runtime since 2025) into `~/.service-visuals/bin`
  at first use and yt-dlp self-updates daily. Never spawn `yt-dlp` on a UI
  path (its onefile unpack takes seconds); `--ffmpeg-location` must be the
  exact imageio ffmpeg file. Spec: `docs/specs/youtube-download.md`.

## Releasing

Tag-triggered: pushing `vX.Y.Z` builds the Mac `.app` and Windows `.exe` in
GitHub Actions and attaches both to the Release. Users auto-update, so a bad
tag reaches real churches. Use the `/release` skill rather than doing it by hand.
