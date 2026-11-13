# Spec: make a scoreboard from scratch — three looks

The owner (2026-11-14): "we have all the logic that can take in an existing
scoreboard and change the numbers, and that's great. But I also want the
scoreboard to create scoreboards … three distinct different versions of
where a user could add a team name and add a number and have three
different looks to make it look cool … still keep the logic and the
feature of changing an existing scoreboard."

**Additive.** The read-an-image board (OCR, saved boards, editor) is not
touched. This is a second way in, in the same Scoreboard tile.

## Behaviour

| When | Behaviour |
|---|---|
| Scoreboard tile opens | Two choices side by side at the top of the picker: **START FROM YOUR POINTS SCREEN** (today's upload, unchanged) and **MAKE A SCOREBOARD** (new). Saved boards list stays below, unchanged. |
| MAKE A SCOREBOARD | A form beside a live preview (same two-column layout as the other tiles): Title, Teams list, Look, Sort, Accent, Background, EXPORT PNG. A BACK link returns to the picker. |
| Teams | Starts with 4 rows: "Red team 0", "Blue team 0", "Green team 0", "Yellow team 0" with the matching team colours. ADD TEAM (up to 8), a remove × per row (never below 2). Each row: colour swatch (click cycles the 8 team colours), name, score. |
| Live preview | Re-renders ~300 ms after any change: the server's own PNG, so preview = export. |
| Remembered | The form (title, teams, look, sort, accent, background choice) is kept in `localStorage` key `sv-scoremake` so next week's scores start from last week's teams. Never sent anywhere. |
| Export | PNG 1920×1080 into exports/, the normal done panel (Download / Show file / Make another). |

## The three looks (`look`)

All 1920×1080. Title (if any) at the top in the `display` font, upper-
cased, letter-spaced, accent colour, centred. Team names `label` font;
scores `display` font (tabular feel: right-aligned or centred). When
`sort` is true teams are ordered by score, highest first (ties keep the
entered order); otherwise entered order. **The leader** (highest score; on
a tie every team sharing it) is marked in every look. If every score is 0
nobody is the leader.

| `look` | Label | Design |
|---|---|---|
| `bars` | BARS | A horizontal race: one row per team, name on the left (right-aligned in a fixed column), a rounded bar in the team colour whose length is proportional to score / max score (min visible stub 2 % so 0 still shows), score in big numerals just past the bar's end. Leader's row gets a small accent "LEADING" tag after the score. Rows evenly fill the space under the title. |
| `cards` | CARDS | One tile per team in a centred grid (2–4 per row; 5–8 teams → two rows): dark rounded tile (#1a1d22) with a thick top band in the team colour, team name upper-cased near the top, the score huge in the middle. The leader's tile has an accent outline and a "★ LEADING" label. |
| `podium` | PODIUM | A ranked leaderboard (always sorted, `sort` ignored): one row per team — rank number in a circle (1st gold `#e8b44f`, 2nd silver `#c9ced6`, 3rd bronze `#c98a55`, then neutral `#3a3f47`), a small team-colour dot, name, score right-aligned. The 1st row is taller and brighter. Ties share a rank (1, 1, 3). |

Team colours (in order): `#e2564b` red, `#4f8fe6` blue, `#5bbf6a` green,
`#f2c94c` yellow, `#9b7ede` violet, `#f08a3c` orange, `#3cc3c9` teal,
`#e66fb0` pink.

Backgrounds: `dark` = the app's dark vignette (reuse `render/qr.py`'s
`_vignette_background()`); `image` = an uploaded file (from
`/api/upload-bg`) cover-filled to 1080p with a black scrim at `bg_dim` %
(default 40 here: text sits straight on the image, unlike the QR card).
Scores render with thousands separators off ("1250"), whole numbers.
Long names shrink to fit their space (to 60 %), then cut with "…".

## API

`POST /api/scoreboard/make/preview` → PNG, 900 px wide.
`POST /api/scoreboard/make/export` → `{"filename": "scoreboard_<look>_<stamp>.png"}`.
Both take this JSON, validated by `validate_scoreboard_make(options)` in
`validation.py`:

| Key | Values | Default | Error |
|---|---|---|---|
| `title` | string ≤ 40 | "" | `The title must be 40 characters or fewer.` |
| `teams` | list of 2–8 `{name, score, color}` | — | `Add between 2 and 8 teams.` |
| `teams[].name` | 1–24 chars after trim | — | `Team {n} needs a name (up to 24 characters).` |
| `teams[].score` | whole number 0–999999 | — | `Team {n}'s score must be a whole number from 0 to 999999.` |
| `teams[].color` | `#rrggbb` | — | `Team {n}'s colour must be a hex colour like "#e2564b".` |
| `look` | `"bars"` `"cards"` `"podium"` | `"bars"` | `That scoreboard look is not valid.` |
| `sort` | bool | true | `Sort must be true or false.` |
| `accent` | `#rrggbb` (reuse `_accent_field`) | `#e8b44f` | existing message |
| `background` | uploaded filename or "" (reuse `_background_field`) | "" | existing message |
| `bg_dim` | whole number 0–80 | 40 | `Dim must be a whole number between 0 and 80.` |

`{n}` is the 1-based row number. (The browser shows the same check
beside the rows using the team's typed name — "Lions's score must be …"
— after the UX check; the server's `Team {n}` wording is the fallback.)
Team names have whitespace runs collapsed to one space. Fonts: `digits`
for the title and scores (there is no `display` role), `label` for names. Analytics: the export calls the existing
`track_export("scoreboard_make", started)`-style path if one exists (look
at how `/api/qr-image` tracks), with ONE extra prop `look` (one of the three
words). Team names, scores and titles are NEVER sent. Add the tool name to
the `stats.py` comment where tools are listed.

## Files (disjoint)

- **Backend agent (Sonnet):** `render/scoremake.py` (new), `validation.py`
  (new function only, plus imports), `routes/board.py` (two routes only),
  `stats.py` (comment / props only if needed), `scripts/smoke.py` (one new
  `check_scoreboard_make()` registered in `main()` next to the other
  scoreboard checks).
- **Frontend agent (Sonnet):** `static/index.html` (Scoreboard view only),
  `static/js/scoremake.js` (new IIFE on `window.SV`, loaded after
  board.js), `static/style.css`, and `scripts/jscheck.py` only if it needs
  the new file registered.

## Do not

Touch the OCR / saved-board code paths, other tiles, `version.py`,
`whatsnew.py`, golden. No new dependencies. Never send team names, titles
or scores to analytics. Don't commit, stash, reset or checkout. Python 3.9
(no walrus/match/`X | Y`), 79 columns, ES5 in an IIFE, comment the WHY.
Edit files only with Edit/Write, never through Bash.

## Done means

Smoke: validation (each error string exactly), each look renders 1920×1080
for 2 and 8 teams, sort on/off changes order for bars and cards but never
for podium, ties share podium rank, all-zero has no leader, an image
background is used, export writes a PNG into exports/ (deleted after).
Stills of all three looks with 4 and 8 teams looked at by the orchestrator.
The form driven in the browser: add/remove team, cycle colour, switch
looks, export, reload restores the form.
