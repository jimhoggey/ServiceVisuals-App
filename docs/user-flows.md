# User flows — how volunteers actually use Service Visuals

This is the memory of how operators think. Read it **before** changing any
screen, and **update it after** — especially the "Found and fixed" lists.

## Why this file exists

UI kept shipping that passed every check and still confused the owner. The
checks proved the code did what the implementer decided; nothing checked
whether that decision matched what an operator believes when they look at
the screen. Each confusion below was found by the owner, one at a time, and
cost a round trip. Recording them here means the next change starts from
what we already know instead of rediscovering it.

The `ux-flow-reviewer` agent (`.claude/agents/ux-flow-reviewer.md`) walks
these flows before any `static/*` change ships.

## Who the operator is

- A church tech volunteer. Often non-technical, often rushed — sometimes
  minutes before a Sunday service.
- Forms their understanding **only from what they can see.** Does not read
  code, does not know what a codec is, does not read long hints.
- The owner's own chain is **Service Visuals → CapCut → MP4 → ProPresenter.**
  But roughly **90% of use goes straight into ProPresenter**, so the default
  path must stay simple and fast.

## Rules that must always hold (every screen)

These are the invariants the confusions below broke. Check each one.

1. **Labels describe the current output.** A caption, button or hint must
   never still describe a previous state. *(Broken: EXPORT button and
   caption said "MP4" while exporting .mov; caption said "30fps" on 15fps
   plain timers.)*
2. **Chosen looks chosen.** If the operator just picked something, it must
   visibly read as selected — immediately, not only once a later condition
   is met. *(Broken: + ADD IMAGE opened its panel but did not look
   selected.)*
3. **Inactive looks inactive.** A control that does nothing in the current
   state must look disabled — the label dims, not just the small box.
   *(Broken: millis-only options looked live with milliseconds off; then
   only the checkbox dimmed, so the label still read as active.)*
4. **Nothing hidden may block the operator.** A field the operator cannot
   see, for a feature that is off, must never block Export or raise an
   error. *(Broken: an emptied "hold at zero" seconds box blocked a plain
   timer's export.)*
5. **One-of groups show every choice, always.** If only one option can be
   active, all options stay visible, exactly one looks selected, and "none
   selected" must be a default the operator understands. *(Broken: with
   TRANSPARENT on, ADD IMAGE was hidden, so there appeared to be two
   background choices, not three.)*
6. **Side by side stays side by side.** Check at **1024, 900 and 768 px**
   wide. A row that wraps reads as fewer choices plus a stray one.
   *(Broken: TRANSPARENT wrapped onto its own row at the owner's width.)*
7. **A stale error never outlives its context.** Switching mode must not
   leave an error on screen for a control that no longer applies. *(Broken:
   the seconds box's red error stayed frozen after switching to clock mode.)*
8. **Actions do what their words say.** "DOWNLOAD ANOTHER" must start a new
   one, not repeat the last. *(Broken: it re-downloaded the whole list.)*
9. **A control that changes jobs still gets the old click.** When a button
   stops doing what it used to, operators keep reaching for it out of habit,
   unless the control that now does the old job is at least as visible.
   *(Risk: + ADD IMAGE became an on/off switch but still reads "+ ADD", so
   it is the most likely click for "add a second image" — which switches
   images off instead.)*

## Copy rules

- **Recommend, don't describe.** Tell them which to pick: "Use this one.",
  "Only if you are putting the file straight into ProPresenter…". Neutral,
  accurate descriptions still left the owner asking three times which to
  click.
- **Purpose first, in plain words.** "see-through background", never "alpha
  channel". Name the app it is for.
- **Tech detail on its own dim line**, exact enough to Google:
  `.mov — Apple ProRes 4444, yuva444p12le`.
- **Never imply the cheaper option is worse** unless measured. (STANDARD is
  measurably *higher* fidelity than ProRes.)
- **Keep "not tested" statements verbatim.** The owner explicitly wants that
  honesty — never soften it to "may not work".
- **Warn before the cost, in the operator's units**: "1.5 MB instead of
  180 MB", "roughly doubles render time".

---

## Timer tile

### The rushed path (must never get slower or harder)

A volunteer wants a **plain 5-minute countdown on screen**: set 5:00, press
EXPORT. No milliseconds, no background options.

- Exports at **15 fps** and finishes **in seconds** (a classic countdown
  draws one frame per second).
- Nothing advanced may be required, pre-selected or in the way.
- Advanced options (milliseconds, 60 fps, transparent) are all **off by
  default** and must not slow this path when off.

### Background — a single choice

**Operator's model:** the background is ONE choice.

| Choice | Means | What they should see |
|---|---|---|
| *(none selected)* | Plain dark background — the default | "No images — plain dark background." |
| **IMAGES** | My own image(s) behind the numbers | Button selected (gold); upload panel / thumbnails with **+ ADD IMAGE**; seconds, dim, blur |
| **GREEN SCREEN** | Solid green to key out in an editor | Button selected (green) |
| **TRANSPARENT** | See-through, for an editor or a ProPresenter layer | Button selected (white); STANDARD / PRORES choice |

- All three buttons are **always visible**, in one row.
- Picking one **turns the others off.**
- Images added earlier are **kept** when switching away, and come back when
  images are chosen again.

**Decided by the owner (2026-09-17)** — the switch is labelled **IMAGES**,
a noun like GREEN SCREEN and TRANSPARENT, and it behaves exactly like them: a
toggle meaning "images are the background". Adding an image is a separate
**+ ADD IMAGE** action in the thumbnail row.

| Operator does | IMAGES switch | Background |
|---|---|---|
| Clicks IMAGES, no images yet | **Gold at once**; upload panel opens; GREEN SCREEN / TRANSPARENT turn off | Dark until one is picked |
| Closes the panel having picked nothing | Back to plain | Dark |
| Uploads or picks an image | Stays gold | That image |
| Clicks the gold IMAGES | Plain — **images switch off** (kept, not deleted) | Dark, caption says why |
| Clicks IMAGES again, images stored | Gold — the same images come back; **panel stays closed** | Their images |
| Clicks **+ ADD IMAGE** in the thumbnail row | Stays gold; panel opens | Unchanged — adds another |
| Removes the last thumbnail, panel closed | Back to plain | Dark |

- **Why IMAGES, not "+ ADD IMAGE".** It was first labelled "+ ADD IMAGE".
  Once that button became an on/off switch, a review found volunteers
  reaching for it to add a second image — the button that added their first
  — and switching their images off. A noun names what is chosen; the word
  "ADD" now lives only on the control that adds. (Rule 9.)
- **+ ADD IMAGE in the thumbnail row has visible words.** It was a bare "+",
  and went unfound.
- **The 10-image cap disables + ADD IMAGE in the row, never IMAGES.** IMAGES
  is the only way to switch images off, so disabling it would trap an
  operator with images they could not turn off.
- **All three buttons share one shape** — dot on top, label below — so their
  labels stay level at every width.
- Images are never deleted by switching — only by their thumbnail's ×.

**Settled**
- **DONE is not un-choosing.** It only closes the panel; every pick is live
  the instant it is clicked, and the strip, dim, blur and gold all survive
  it. But the word "DONE" suggests confirming or finalising something, so a
  volunteer may think closing without it loses their pick. Consider
  "CLOSE".
- "No images — plain dark background." is what tells a volunteer that
  nothing selected means dark. It is doing necessary work.

**Found and fixed**
- v1.36.0 — + ADD IMAGE was hidden while GREEN SCREEN or TRANSPARENT was on,
  so only two choices appeared. Now always visible.
- v1.36.0 — + ADD IMAGE gained a gold selected state.
- *Unreleased (next: v1.37.0)* — + ADD IMAGE gave no feedback when clicked
  with no images. Now gold the instant it is clicked. (Rule 2.)
- *Unreleased* — the three choices wrapped at every width from 940px up, and
  widening the window never helped. They now have a row of their own;
  confirmed one row at 1440, 1024, 945, 900 and 768px, from 0 to 10 images.
  (Rule 6.)
- *Unreleased* — which button got orphaned changed with the image count.
  The row is now stable at every count. (Rule 6.)
- *Unreleased* — "No images — plain dark background." sat in the button row
  and was pushed around by the wrap. It is now a caption above the row.
- *Unreleased* — "TRANSPAREN / T" broke mid-word at 945px. Measurement said
  18px to spare; only a screenshot showed it. The word is now kept whole;
  checked at every width and image count.
- *Unreleased* — the switch read "+ ADD IMAGE", so it was the most likely
  click for a second image and switched images off instead. Renamed to
  IMAGES; + ADD IMAGE moved to the thumbnail row with visible words.
  Confirmed by review: with images on, + ADD IMAGE is found right after the
  thumbnails and adds another without touching IMAGES, at 1440, 1024, 945,
  900 and 768px, with 2 images and at the 10-image cap. (Rule 9.)
- *Unreleased* — at two-column widths the switch's label sat about 7px
  higher than its neighbours'. All three now share dot-over-label;
  confirmed level to the pixel at all five widths, with 0, 2 and 10 images.
- *Unreleased* — switching stored images back on reopened the picker every
  time. It now opens only when there is nothing to show; confirmed with 2
  stored images and at 10 of 10.

**Found, not yet fixed**
- **+ ADD IMAGE is correctly worded and placed, but a third the weight of
  IMAGES.** It sits in the thumbnail row at thumbnail height, while IMAGES
  is the larger, gold, top-left control. Every review click-through found it
  once looking at the thumbnails; the residual risk is a click made from
  habit without looking, which can still land on IMAGES and switch images
  off. Not blocking — one click reverses it, and the "Images are off"
  caption explains what happened. (Rule 9.) *Candidate rule, pending the
  owner: a relocated action needs comparable visual weight to the control
  it replaces, not just the right words.*

**Checked and clear**
- The default Timer tile is a complete plain 5-minute countdown; it exported
  cleanly in about 10 seconds without touching Background.
- Closing the panel having picked nothing returns + ADD IMAGE to plain.
- Clicking a gold + ADD IMAGE switches images off, and clicking again brings
  back the same images.
- Images → GREEN SCREEN → TRANSPARENT → images keeps the exact image set,
  at 1440px and 768px.
- The 10-image cap disables "+" (tooltip: "A timer can use up to 10
  background images."), never + ADD IMAGE, which still switches images off
  at 10 of 10.
- "Images are off — plain dark background. Click + ADD IMAGE to bring them
  back." earns its place: it appears only while stored images are switched
  off, names the cause and the fix, and keeps the rule 9 surprise from being
  a dead end.
- Deleting from the stored library asks first; cancelling changes nothing;
  confirming removes it from the library and from this timer's set.
- CLOCK mode changes nothing in the Background group, including an open
  picker.
- With no images, IMAGES reads as the place to add a picture: it goes gold
  and opens the upload panel at once.
- IMAGES and + ADD IMAGE, two controls about images, differ enough in shape
  and position that no walked journey confused them.
- GREEN SCREEN's and TRANSPARENT's own click-twice journeys are clean.
- DIM and Blur show with at least one image; SECONDS PER IMAGE with two or
  more.

### Transparent — STANDARD or PRORES

**Operator's question:** "Which one do I click?" Answer it directly.

| | STANDARD (default) | PRORES |
|---|---|---|
| **Pick it when** | Editing in CapCut or another editor | Putting the file **straight** onto a ProPresenter layer |
| **Quality** | Pixel-perfect (lossless) | Visually identical (very slightly lossy) |
| **5-min countdown** | ~180 MB | ~530 MB |
| **ProPresenter** | **Not tested** — say so | Documented as supported |
| **Tech line** | `.mov — QuickTime Animation (RLE), argb, lossless` | `.mov — Apple ProRes 4444, yuva444p12le` |

- **For a timer going straight on screen, neither** — leave TRANSPARENT off.
  The hint says so: the normal export is 1.5 MB instead of 180 MB.
- Why PRORES exists: drop it on a ProPresenter layer with the church's own
  video underneath — it shows through, no editing, no keying.
- WebM was tried and dropped: it imports into CapCut as a solid white box.

**Found and fixed**
- v1.34.0 — copy described the options neutrally; owner asked three times
  which to pick. Rewritten to recommend.
- v1.34.0 — EXPORT button and preview caption still said MP4 while
  exporting .mov.

### Milliseconds

Options live under **Show milliseconds**, countdown only. None of them do
anything in clock mode.

| Option | Operator's model |
|---|---|
| Show milliseconds | Adds `.000` — renders at 30 fps, slower to export |
| Full-size milliseconds | `.000` as tall as the minutes and seconds; the whole timer shrinks 10–16% to fit |
| Hold at zero until the end | Shows `.000` without ticking until the last N seconds (default 60), then ticks — **seamless, nothing moves** |
| Smoother milliseconds (60 fps) | Smoother ticking; ~2× render time, ~1.5× file; **capped at 15:00** (15:00 itself allowed) |

- The owner's real use: a **15-minute countdown**, milliseconds held at zero
  until the last minute.
- 60 fps **only helps if the editor also exports at 60 fps** — a 30 fps
  CapCut export throws the frames away. The hint says so.

**Found and fixed**
- v1.35.0 — a hidden, emptied "hold at zero" seconds box blocked a plain
  timer's export. The check existed twice; fixing one copy still left
  Export greyed out. (Rule 4.)
- v1.35.0 — millis-only options looked active with milliseconds off.
  (Rule 3.)
- v1.35.0 — disabling them dimmed only the checkbox, so labels still read
  as live. (Rule 3.)
- v1.35.0 — caption said "30fps" on plain timers, which export at 15.
  (Rule 1.)
- v1.35.0 — "Renders at 30 fps" hint and preview caption did not change
  when 60 fps was ticked. (Rule 1.)
- v1.35.0 — the seconds box's red error stayed frozen after switching to
  clock mode. (Rule 7.)
- v1.35.0 — past 15:00 the 60 fps box disables and unticks itself, so the
  request can never carry 60 fps past the cap.

### Exporting a long render

- A 15-minute 60 fps transparent export takes **several minutes**. Operators
  may reopen the app wondering if it stalled.
- **Found and fixed**, v1.36.0 — reopening the app mid-render deleted the
  render in progress (and in-progress YouTube downloads), failing at the
  very last step.

---

## YouTube download tile

### The rushed path

Paste a link, choose MP4 or MP3, press DOWNLOAD.

- **Batch:** links separated by **commas or new lines** — both work the same.
- The finished screen must answer "is it done?" at a glance: **"All 9 are
  saved in your exports folder."**, not a lone filename.
- **DOWNLOAD ANOTHER** clears the box for the next one.

**Found and fixed**
- v1.33.0 — the finished batch led with the last file's name, as though it
  were the only one, with "9 saved" small and dim beneath it.
- v1.33.0 — DOWNLOAD ANOTHER re-downloaded the whole list. (Rule 8.)
- v1.36.0 — reopening the app mid-download deleted the download in progress.

---

## How to use and update this file

1. **Before** changing a screen: read its section and the rules above.
2. **After** a change: add to "Found and fixed" with the version, move
   anything resolved out of "Found, not yet fixed", and record the answer to
   any open question.
3. When the owner reports a confusion, add it here **even before fixing it**,
   under "Found, not yet fixed", so it is never lost between sessions.
