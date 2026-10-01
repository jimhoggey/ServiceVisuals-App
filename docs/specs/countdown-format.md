# Spec: typed countdown format (FORMAT box)

Replaces the countdown's "Show milliseconds" and "Fixed 00:00:00 format"
checkboxes with one text box where the volunteer types the shape of the
numbers. Approved by the owner, 2026-10-02.

## Behaviour

| Typed        | 20 s timer   | 5 min timer  | 90 min timer   |
|--------------|--------------|--------------|----------------|
| *(empty)*    | `0:20`       | `5:00`       | `1:30:00`      |
| `M:SS`       | `0:20`       | `5:00`       | `90:00`        |
| `M:SS.000`   | `0:20.000`   | `5:00.000`   | *(over 30 min — refused, as today)* |
| `MM:SS`      | `00:20`      | `05:00`      | `90:00`        |
| `HH:MM:SS`   | `00:00:20`   | `00:05:00`   | `01:30:00`     |
| `SS`         | `20`         | `300`        | `5400`         |
| `SS.0`       | `20.0`       | `300.0`      | `5400.0`       |

- **Empty means automatic** — today's shape, chosen from the total. An empty
  box (or no `display_format` key) renders byte-identically to before.
- **The leftmost unit absorbs the overflow.** Its letter count is a minimum
  width (zero-padded), never a cap: `M:SS` on 90 minutes reads `90:00`.
- Milliseconds are written as zeros after a dot, last: `.0` tenths,
  `.00` hundredths, `.000` thousandths. Truncated, never rounded, so the
  last frame of a second never shows the next second's value.

## Display rules (renderer AND preview must match)

Parse, after trimming whitespace; letters are case-insensitive:

1. Split once on `.`. The part after it must be 1–3 `0`s and nothing else.
2. The part before it splits on `:` into groups. Each group is one letter
   repeated (`H`, `M` or `S`).
3. The groups' letters, in order, must be `S`, `MS` or `HMS` — biggest
   first, none skipped, always ending in seconds.
4. Every group after the first is exactly 2 letters. The first is 1–3.

Value of each group: the leftmost is `rem // unit` (no modulo); the others
are `(rem % next_bigger_unit) // unit`, 2 digits. Millis are
`"{:03d}".format(rem_ms % 1000)[:n]`.

## API contract

Countdown only. New key `display_format`, a string, default `""`.

- Non-empty and valid: it **decides** `show_millis` (true when it has a
  millis part) and forces `fixed_format` false, whatever was sent for them.
  The validated, normalised string (uppercase, trimmed) is passed on.
- Empty: `show_millis` / `fixed_format` behave exactly as before, so older
  callers keep working.
- Not a string, or longer than 16 characters: refused.

Errors (shown under the box, and Export is disabled until fixed):

| Problem | Message |
|---|---|
| Any other character | `Use only H, M, S, colons and .000 — like M:SS.000.` |
| Two letters in one group (`MSS`) | `Put a colon between the units, like M:SS.` |
| Wrong order, a skipped unit, no seconds, empty group | `Write the units biggest first, ending in seconds: H:MM:SS, M:SS or SS.` |
| Group after a colon not 2 letters | `Every unit after a colon needs two letters, like M:SS.` |
| First group over 3 letters | `The first unit can be at most 3 letters wide.` |
| Bad millis part | `Milliseconds go at the end as .0, .00 or .000.` |
| Not a string / too long | `The format can be at most 16 characters.` |

## Frame rate

Unchanged: a millis part means 30 fps (or 60 with Smoother milliseconds),
and the 30-minute / 15-minute ceilings apply exactly as with the old
checkbox. `.0` and `.00` still render at 30 fps.

## UI

ADVANCED → the group's legend becomes **Number format**.

- **FORMAT** text box, countdown only, placeholder `Automatic`. Hint below
  it: what the timer will start at (`Starts at 0:20.000`), plus how to type
  it. An error replaces the hint.
- **Show milliseconds** stays, **clock mode only** (the clock has its own
  12/24-hour and seconds settings).
- Full-size milliseconds, Hold at zero and Smoother milliseconds appear
  only in countdown mode when the format has a millis part.

## Files

`render/timer.py` (parser, `_format_remaining`, ms text, `_clock_font_size`
sample), `validation.py`, `static/js/timer.js`, `static/index.html`,
`scripts/smoke.py`.

## Do not

- Do not change any output when `display_format` is empty or absent.
- Do not touch clock mode's rendering.
- Do not add the typed format to analytics (it is user text).

## Done means

Smoke covers every row of the behaviour table and every error message;
`golden.py --check` passes unchanged; the old-vs-new countdown `cmp`
passes; the preview shows `0:20.000` for a 20 s timer with `M:SS.000`,
and a rendered frame does too.
