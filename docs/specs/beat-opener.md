# Spec: Beat opener (countdown, Advanced)

A conference-opener countdown. The screen flashes white and black on the
song's beat, and a ping in the song's key plays on the beat, so the band
comes in at 0:00 in time and in tune. Modelled on a reference clip the owner
supplied (2026-10-02), measured frame by frame:

- flash cycle 0.928 s = two beats at ~126 BPM; white for one beat, black
  for the next;
- a G♯ tone (415 / 830 / 1661 Hz, same note in three octaves) whose energy
  repeats every 0.93 s and peaks as the screen cuts **white → black**,
  ringing into the next white beat;
- the band's entry lands on a black-and-ping beat;
- the timer, black monospace digits on the white beats.

Approved by the owner in conversation: sound inside the MP4; beat grid
anchored so 0:00 is a beat; alternate white/black each beat; ping on the
white→black cut, copying the clip; digits visible on white only; at 0:00 a
final ping, then hold black and silent; 60 fps; FORMAT also accepts a
colon before milliseconds.

## Behaviour

Let `L = 60 / bpm` seconds, `Z = total` (the moment the timer reads 0:00),
and for video time `t`: `k = floor((t − Z) / L)` (negative before zero).

| When | Screen | Digits | Sound |
|---|---|---|---|
| `t < Z`, `k` even | black | hidden | a ping starts at the start of every even beat |
| `t < Z`, `k` odd | white | black, the running countdown | — (previous ping rings on) |
| `t = Z` | black | hidden | the final ping |
| `t > Z` (the hold) | black | hidden | silent apart from the final ping's tail |

- Even `k` is the "one" (white → black + ping), odd `k` the "two". The beat
  just before 0:00 is always `k = −1`, a white "two", so 0:00 is always a
  white → black cut with a ping.
- The video may start part-way through a beat. A ping whose start time is
  before 0 is not played; the opening partial beat is just black or white.
- Integer-exact, judged at the middle of each frame: for frame `i` at
  `fps`, `k = ((2i + 1 − 2·Z·fps) · bpm) // (120 · fps)` with Python's
  floor division (JS: `Math.floor` on the same integer expression). No
  float comparison decides a frame's colour, and every edge is within half
  a frame (8 ms at 60 fps) of its beat. (First built at frame start; a
  measured render showed edges up to 13 ms late, so it moved to mid-frame.)
- Ping start times: `Z − 2·m·L` for `m = 0, 1, 2 …` while ≥ 0.

## The tone (revised after the owner listened)

The first build was a dry bell that died after 0.9 s; the owner found it
"monotone, very dry, not hype building". Re-measuring the clip showed a
sustained pad that swells on each cut and is never silent. The owner then
ruled: **the same tone on every cut, just a better tone** — no build or
riser toward zero ("that screams AI"). Engine: the bundled ffmpeg, chosen
over Spotify's pedalboard, which needs Python 3.10+ (the app is 3.9),
pulls in numpy, and is GPL-3.0 — it would load inside the bundled app.

Generated in plain Python (no numpy), 44.1 kHz 16-bit **stereo** WAV,
exactly `total_frames / fps` seconds:

- **Pad:** the key's note in octaves 3, 4, 5, 6 at 0.30 / 0.65 / 1.0 /
  0.22 (octave 5 strongest, like the clip; octaves only, so major/minor
  can't clash). Each voice a soft wave (harmonics 1, 2, 3 at 1 / 0.22 /
  0.07). Built as a seamless 4 s loop (frequencies on a 0.25 Hz grid, under
  2 cents off). **No detuned voices** — their beating made cuts measure
  65 % apart. Width comes from each octave having a different phase in
  the right ear (¼, ½, ¾, ⅛ cycle).
- **Level, identical on every cut:** a 35 ms swell from the sustain floor
  (0.35) to full, settling back with τ = 0.40 s. Never silent while
  counting. 0.4 s fade-in at the start. After 0:00 the same swell, then a
  0.8 s exponential ring-out; the end of the file fades over 30 ms.
- **Bell accent on every cut:** octaves 5, 6, 7 at 1 / 0.45 / 0.12, 3 ms
  strike, τ = 0.14 s, at 0.35 of the pad. Built from the **same phase
  grid** as the pad — an independent bell met the pad's 880 Hz at a
  different phase each cut (55 % apart).
- **Reverb:** ffmpeg `afir` convolution with a generated stereo impulse
  (2.2 s darkened noise, τ = 0.55 s, 12 ms pre-delay, independent seeded
  noise per ear) with `gtype=none:irnorm=2` (unit energy). Note: afir's
  `dry` is its *input* gain — `dry=0` silences it. Mixed wet at 0.45
  against dry, then `alimiter=limit=0.89:level=0`.

**v3 (owner: "the reverb carries too long and blends with the other
beats"):** floor 0.35 → 0.18, settle τ 0.40 → 0.22 s, reverb 2.2 s / τ
0.55 s / mix 0.45 → 0.9 s / τ 0.16 s / mix 0.30. Measured: the sound now
drops 14.9 dB between a hit and just before the next cut (v2: ~5.5 dB);
cut-to-cut spread still 5.7 %; L/R correlation −0.30. The figures below
are v2's.

Measured on 20 s / 125 BPM / A: every cut within 5.7 % of the others;
the sustain between cuts never drops below about half the swell;
L/R correlation −0.16 (wide); dominant pitch 880 Hz; quiet ~2.5 s after
0:00.

## Your own sound (addendum, approved 2026-10-02)

The owner wants a more interesting sound than any synthesised tone: a
real pad or hit, recorded or exported by the band. Their answers: play it
**as recorded** (no re-pitching), and **detect its note** automatically.

| When | Behaviour |
|---|---|
| Upload | One sound at a time, kept in `~/.service-visuals/beat-sound/` (`sound.wav` + `sound.json`) so it survives updates; a new upload replaces it. |
| Stored form | Decoded by the bundled ffmpeg (first 30 s of input at most) to 44.1 kHz 16-bit stereo; leading silence trimmed (start = 5 ms before the first sample above 5 % of the peak); first **4.0 s** kept; peak normalised to 0.89. |
| Note | Autocorrelation pitch on up to 0.6 s from the attack (mono, decimated to 11025 Hz, 55–1760 Hz); clarity ≥ 0.5 → note name (`C`…`B`, sharps), else unknown. |
| Render | The sound starts exactly at every cut (the same `ping_times`), at full level, and fades out over the 30 ms before the next cut so cuts never smear. The 0:00 instance plays its whole length into the hold. **No reverb, no limiter**: used as recorded. The file's last 30 ms fade. |
| Built-in | Stays the default and unchanged. |

API:

- `POST /api/beat-sound`, multipart field `sound` → `{"present": true,
  "note": "G#"|null, "seconds": 1.23, "trimmed": bool}` (`trimmed` = the
  input ran past 4 s after its start). Errors (400, `{"error"}`):
  `No sound was uploaded.` · `That file isn't a sound the app can read —
  use WAV, MP3 or M4A.` · `That sound is silent — record it again a little
  louder.`
- `GET /api/beat-sound` → `{"present": false}` or the POST shape without
  `trimmed`.
- `DELETE /api/beat-sound` → `{"present": false}`.
- Render option `beat_sound`: `"builtin"` (default) | `"mine"`. Only
  checked while `beat_opener` is true. Not one of those: `Sound must be
  "builtin" or "mine".` `"mine"` with nothing stored: `Upload your sound
  first, or switch back to the built-in tone.`
- Analytics `opener`: `off` | `on` | `mine` — never the note, length or
  file name.

UI (inside `#timer-beat-fields`, after BPM/KEY): **SOUND** — two choices,
"Built-in tone" (default) and "My sound". With "My sound": an UPLOAD
SOUND… button (`accept="audio/*,.wav,.mp3,.m4a"`), a status line ("Your
sound: 1.2 s, in G♯." / "Uploading…" / the error), a REMOVE link when one
is stored, and a warning when the detected note differs from KEY: "Your
sound is in G♯ but the song is in A — the band would come in on a
different note." Unknown note: "Couldn't tell your sound's note — make
sure it's in the song's key." A 4 s trim says "Only the first 4 seconds
are used." Export is disabled, with the message under the status line,
while "My sound" is chosen and nothing is stored.

Files: lead — `beatsound.py` (new: store, decode, trim, detect),
`routes/beatsound.py` (new), `app.py` (register + `_timer_props`),
`render/beat.py`, `validation.py`, `scripts/smoke.py`. UI agent —
`static/index.html`, `static/js/timer.js`, `static/style.css`.

## Picture

- Classic style only. White = (255, 255, 255), black = (0, 0, 0): no
  vignette, no accent, no warn colour.
- Digits: (17, 17, 17) on white, drawn exactly where the classic countdown
  draws them, at the same size (the FORMAT box and milliseconds apply as
  usual). Hidden on black.
- 60 fps in and out, always, while the opener is on (reuses the 60 fps
  millis path: `_millis_fps(True)`).

## Sound track

The video is encoded silent through the unchanged `encode_parallel`, then
one extra ffmpeg pass adds the WAV:

`ffmpeg -y -i <video> -i <wav> -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac
-b:a 192k -movflags +faststart -f mp4 <out>.mux.part` → `os.replace` onto
the output. The video stream is copied, never re-encoded. The WAV is written
as `<out>.wav.part` and deleted afterwards (the boot-time stale-`.part`
sweep in app.py catches any leftover). The ffmpeg spawn passes
`CREATE_NO_WINDOW` on Windows, like every other spawn.

## API contract (countdown only)

| Key | Type | Default | Rule |
|---|---|---|---|
| `beat_opener` | bool | `false` | `"Beat opener" must be true or false.` |
| `bpm` | int | `120` | 60–200: `BPM must be a whole number between 60 and 200.` |
| `key` | str | `"A"` | one of `C C# D D# E F F# G G# A A# B`: `Key must be one of C, C#, D, D#, E, F, F#, G, G#, A, A#, B.` |

- `bpm` and `key` are only validated while `beat_opener` is true; otherwise
  the clean dict carries the defaults (a hidden field never blocks a
  render — docs/user-flows.md rule 4).
- While `beat_opener` is true, refused with these messages:
  - style not classic: `The beat opener only works with the CLASSIC style.`
  - background images: `The beat opener flashes the whole screen, so it can't use background images.`
  - green screen: `The beat opener can't be combined with GREEN SCREEN.`
  - transparent: `The beat opener can't be combined with TRANSPARENT.`
  - total over 900 s: `With the beat opener on, the timer can run for at most 15 minutes.`
- `millis_60fps` is irrelevant while it is on (always 60).
- Clock mode ignores all three keys (they never reach the clean dict).
- Filename gets `_beat<bpm><key>` (`#` written as `s`): `0m20s_classic_beat125A`.
- Analytics: `_timer_props` gains `opener: "on" | "off"` — our own words,
  never the BPM or the key.

## FORMAT addendum (docs/specs/countdown-format.md)

A final group of 1–3 zeros after a colon is milliseconds, like `.000`:
`M:SS:000` → `0:20:000`. The separator typed is the separator drawn.
Normalised text keeps the colon. `.` and `:` forms share every other rule.

## UI (static/*)

ADVANCED gets a **Beat opener** group (countdown only, hidden in clock mode):

- Checkbox **Beat opener**. Hint: "Flashes the screen and plays a note on
  the beat, so the band comes in at 0:00 in time and in key."
- While ticked: **BPM** number (60–200, default 120) and **KEY** select
  (C, C♯ / D♭, D, D♯ / E♭, E, F, F♯ / G♭, G, G♯ / A♭, A, A♯ / B♭, B —
  values `C`…`B` with `#`), default A. Hint under them: "0:00 lands on a
  beat. Renders at 60 fps, with sound."
- While ticked: RING and BAR are disabled (the selection falls back to
  CLASSIC, like clock mode does for BAR); IMAGES, GREEN SCREEN and
  TRANSPARENT are disabled with the background hint "The beat opener
  flashes the screen white and black." The 60 fps checkbox is hidden.
- Preview: a white beat — white canvas, digits in #111 at the classic size.
  No audio in the preview.
- Spec line: `1920x1080 - 60fps - H.264 MP4 with sound`. Estimate uses
  60 fps. Duration hint: "5 seconds to 15 minutes with the beat opener".
- Export disabled with the error under the field for a bad BPM.

## File ownership (disjoint)

- **Python (lead):** `render/beat.py` (new), `render/timer.py`,
  `validation.py`, `app.py` (`_timer_props` only), `scripts/smoke.py`.
- **UI agent:** `static/index.html`, `static/js/timer.js`,
  `static/style.css`.

## Do not

- Do not change any output with `beat_opener` false or absent: golden and
  the byte-identical countdown check must pass untouched.
- Do not touch `render/encoder.py`.
- Do not add numpy or any dependency.
- Do not put the BPM or key into analytics.
- Do not flash faster than one change per beat (200 BPM = 1.7 flashes a
  second, under the 3-a-second photosensitivity guideline).

## Done means

Smoke covers the beat grid (0:00 is a white→black ping beat for several
BPMs and totals, frame colours at beat edges, ping times), the WAV (length,
pitch of the strongest partial within 1 % of the key's octave-5 note), the
validation table, the FORMAT colon form, and that the exported MP4 has an
AAC stream. A rendered 20 s / 125 BPM / A opener: frames inspected at beat
edges; the exported audio's dominant pitch measured at 880 Hz; golden
passes; byte-identical check passes; scoped UX review run.
