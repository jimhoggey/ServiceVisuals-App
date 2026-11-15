# Spec: trim a downloaded clip

The owner downloads a 15-minute video but needs one minute of it. After a
download finishes, the volunteer trims it inside the app and a trimmed copy
is saved beside the original. Not an editor: one start, one end, save.

## Behaviour

| Situation | What the volunteer sees |
|---|---|
| Single MP4 or MP3 download finishes | Done panel gains **TRIM THIS CLIP** beside SHOW FILE |
| Batch download finishes | No trim button (one file at a time keeps it simple) |
| Clicks TRIM THIS CLIP | Trim panel opens under the done panel: player, gold bar, the start and end times under its handles, CLIP LENGTH 30 SEC / 1 MIN / 5 MIN, PLAY MY CLIP, SAVE TRIMMED COPY |
| Panel opens | The whole video selected; "Nothing trimmed yet…" and SAVE held until it is narrowed |
| Types the start time (gold, under the left handle) | The end moves to keep the clip's length, capped at the video's end |
| Picks 30 SEC / 1 MIN / 5 MIN | End = start + that length, capped |
| Types the end time | The clip's length changes |
| Drags a handle on the bar | That edge moves, on whole seconds, and the player shows that frame |
| Clicks the bar outside the handles | The player jumps there |
| PLAY MY CLIP | Plays from START and pauses at END |
| SAVE TRIMMED COPY | Progress bar in the trim panel, then "Trimmed copy saved." + filename + SHOW FILE |
| DOWNLOAD ANOTHER / a new link / leaving the view | Trim panel closes |

## Display rules

- Times read `M:SS`, or `H:MM:SS` from an hour up. Fields accept `90`,
  `1:30`, `1:02:30` and a decimal (`1:30.5`).
- A field that can't be read turns red with `Type a time like 1:30.`
  SAVE is disabled while any field is bad or the clip is under 0.5 s.
- Each time appears once (the owner's rule): start and end under their
  handles, the clip's length inside the gold part ("5:00 clip"), the
  video's length dim at the far right (hidden when the end time reaches
  it). No time boxes and no SET HERE buttons.
- The line under the buttons only ever shows a problem or the
  "Nothing trimmed yet" nudge.
- The original is never changed. The hint says so.

## API

`POST /api/render` with `{"type": "trim", "options": {...}}` (same job
queue and polling as every other export).

| Key | Type | Rule |
|---|---|---|
| `filename` | string | A file in the exports folder, ending `.mp4` or `.mp3` |
| `start` | number | seconds, ≥ 0 |
| `end` | number | seconds, > start + 0.5, ≤ the file's length + 0.5 |

Errors (verbatim):
- `Pick a downloaded file to trim.`
- `That file isn't in your exports folder any more.`
- `Only MP4 and MP3 downloads can be trimmed.`
- `Type the start and end as times, like 1:30.`
- `The start has to be before the end.`
- `The clip has to be at least half a second long.`
- `The end is past the end of the video (it is M:SS long).`

`GET /api/download/media/<filename>` serves an exports-folder MP4/MP3
inline, with range requests, so the player can seek.

## Output

- MP4: re-encoded H.264 (libx264 veryfast, CRF 18, yuv420p) + AAC 192k,
  faststart, so the cut lands on the exact frame (a stream copy would snap
  the start to the previous keyframe, seconds early). MP3: stream copy.
- Name: `<original name> (2m30s to 3m30s).mp4`; ` 2`, ` 3` … if taken.
- Written as `.part` and renamed, like every other export.
- Analytics: the `export` event with `tool="trim"`, nothing else.

## Do not

- Do not overwrite or delete the original download.
- Do not spawn yt-dlp for any of this.
- Do not send the filename or times to analytics.

## Done means

- Smoke: validation errors, a real trim of a generated clip (length right
  within a frame), name collision, media route refuses `../`.
- Browser: download a short video, trim to 0:05–0:10, frames checked.
