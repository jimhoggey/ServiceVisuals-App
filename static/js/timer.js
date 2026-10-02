/* Service Visuals — timer tile: countdown/clock preview, background
   images, and export. Loaded after core.js. */

"use strict";

(function (SV) {

  var $ = SV.$, PW = SV.PW, PH = SV.PH;
  var TRACK = SV.TRACK, TEXT_LIGHT = SV.TEXT_LIGHT;
  var FONT_DIGITS = SV.FONT_DIGITS, FONT_LABEL = SV.FONT_LABEL;
  var paintBackground = SV.paintBackground, roundRectPath = SV.roundRectPath;
  var intFrom = SV.intFrom, toInt = SV.toInt, pad2 = SV.pad2;
  var currentAccent = SV.currentAccent;
  var showError = SV.showError, hideError = SV.hideError;
  var exportBusy = SV.exportBusy;

  // ============================================================ TIMER ======

  // ---- background images (spec: docs/specs/timer-backgrounds.md) ----------
  // `ids` is the ordered set attached to THIS timer (0-10 items, upload/add
  // order). `library` is the full store the picker lists — re-fetched every
  // time the picker opens (see openTimerBgPicker) and otherwise kept in sync
  // in-place as images are added/deleted through this same panel.
  var timerBg = { ids: [], library: null };
  // Mirrors BACKGROUNDS_PER_TIMER_MAX in backgrounds.py, which is the
  // library's own size: a timer may use everything saved, and nothing
  // caps it below that. The cost is real but bounded — every image
  // becomes a full 1920x1080 plate held for the whole render, ~8 MB each
  // (measured: 40 images = +334 MB) — so this is not a free number to
  // raise on either side.
  var BG_PER_TIMER_MAX = 40;

  // IMAGES is a toggle like GREEN SCREEN and TRANSPARENT (owner decision,
  // docs/user-flows.md): pressed means images really are the background.
  // It was labelled "+ ADD IMAGE" until a review found that volunteers
  // clicked it to add a second image and switched their images off
  // instead; the "+ ADD IMAGE" action now lives in the thumbnail row. Stored images can exist while it is off -- switching
  // images off keeps them, so the count alone no longer says which.
  function bgImagesOn() {
    return $("timer-bg-images").getAttribute("aria-pressed") === "true";
  }

  // Beat opener (spec: docs/specs/beat-opener.md): ticked AND in countdown
  // mode. The box keeps its tick while Clock is showing -- the group is
  // hidden there and the server never hears of it -- so every rule below
  // asks this, never the checkbox alone, or a stale tick would grey out
  // controls for a feature the operator can no longer see.
  // What the opener overrides, held while it is ticked (applyTimerMode).
  var BEAT_BG_BUTTONS = ["timer-bg-images", "timer-bg-green",
                         "timer-bg-transparent"];
  var beatSaved = null;

  function beatOpenerOn() {
    var modeEl = document.querySelector('input[name="timer-mode"]:checked');
    return !!modeEl && modeEl.value !== "clock" && $("timer-beat").checked;
  }

  // Your own sound (spec: docs/specs/beat-opener.md, addendum). The server
  // keeps one uploaded sound; this is its last known state, from GET
  // /api/beat-sound at boot and after every upload or remove. `trimmed` only
  // comes back from the POST (GET doesn't know it), so it is carried over
  // the refresh that follows -- otherwise "Only the first 4 seconds are
  // used." would vanish a moment after it appeared. `error` is the last
  // upload or remove failure, shown in place of the status until the
  // operator does something else.
  var beatSound = { present: false, note: null, seconds: 0, trimmed: false,
                    busy: false, error: "" };
  var BEAT_SOUND_UPLOAD_FAILED = "Couldn't upload that sound — try again.";
  var BEAT_SOUND_MISSING = "Upload your sound first, or switch back to " +
      "the built-in tone.";

  function beatSoundChoice() {
    var el = document.querySelector('input[name="timer-beat-sound"]:checked');
    return el ? el.value : "builtin";
  }

  // "Mine" only counts while the opener is on AND visible: a hidden choice
  // must never block a render (docs/user-flows.md rule 4).
  function beatSoundMineOn() {
    return beatOpenerOn() && beatSoundChoice() === "mine";
  }

  function validateTimerBeatSound() {
    if (!beatSoundMineOn() || beatSound.present) return null;
    return BEAT_SOUND_MISSING;
  }

  // Note names for people: the server sends "G#", a musician reads "G♯" --
  // and a chart may say "A♭", so a sharp is named both ways, exactly as
  // the KEY dropdown lists it (UX review: "G♭" on the chart and "F♯" in
  // the warning didn't connect).
  var FLAT_OF = { "C#": "D♭", "D#": "E♭", "F#": "G♭", "G#": "A♭",
                  "A#": "B♭" };
  function noteLabel(n) {
    n = String(n);
    return FLAT_OF[n] ? n.replace("#", "♯") + " / " + FLAT_OF[n] : n;
  }

  function applyBeatSoundState(j) {
    beatSound.present = !!(j && j.present);
    beatSound.note = beatSound.present && j.note ? j.note : null;
    beatSound.seconds = beatSound.present ? (j.seconds || 0) : 0;
    if (!beatSound.present) beatSound.trimmed = false;
  }

  function refreshBeatSound() {
    return fetch("/api/beat-sound", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : { present: false }; })
      .catch(function () { return { present: false }; })
      .then(function (j) {
        applyBeatSoundState(j);
        updateTimer();
      });
  }

  function uploadBeatSound(file) {
    var fd = new FormData();
    fd.append("sound", file);
    beatSound.busy = true;
    beatSound.error = "";
    updateTimer();
    return fetch("/api/beat-sound", { method: "POST", body: fd })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok || !j.present) {
            beatSound.error = (j && j.error) || BEAT_SOUND_UPLOAD_FAILED;
            return;
          }
          applyBeatSoundState(j);
          beatSound.trimmed = !!j.trimmed;
        });
      })
      .catch(function () { beatSound.error = BEAT_SOUND_UPLOAD_FAILED; })
      .then(function () {
        beatSound.busy = false;
        // The server's copy is the truth; this also redraws.
        return refreshBeatSound();
      });
  }

  function removeBeatSound() {
    beatSound.busy = true;
    beatSound.error = "";
    updateTimer();
    return fetch("/api/beat-sound", { method: "DELETE" })
      .then(function (r) {
        if (!r.ok) beatSound.error = "Couldn't remove that sound — try again.";
      })
      .catch(function () {
        beatSound.error = "Couldn't remove that sound — try again.";
      })
      .then(function () {
        beatSound.busy = false;
        return refreshBeatSound();
      });
  }

  // Status line, the note under it, and REMOVE. Called from updateTimer().
  function renderBeatSound() {
    var status = $("timer-beat-sound-status");
    var noteEl = $("timer-beat-sound-note");
    var key = $("timer-key").value || "A";
    var text, bad = false;
    if (beatSound.busy) {
      text = "Uploading…";
    } else if (beatSound.error) {
      text = beatSound.error;
      bad = true;
    } else if (beatSound.present) {
      // Says it WILL play, not just what the file is: the highlighted
      // segment was the only other sign of which sound gets exported.
      text = "Your sound plays on every beat: " +
          beatSound.seconds.toFixed(1) + " s" +
          (beatSound.note ? ", in " + noteLabel(beatSound.note) : "") + ".";
      if (beatSound.trimmed) text += " Only the first 4 seconds are used.";
    } else {
      // Empty: the instruction line below already says what to do, and
      // a second "no sound yet" line just repeated it.
      text = "";
    }
    status.textContent = text;
    status.hidden = !text;
    status.classList.toggle("is-bad", bad);

    // One line under the status, most urgent first: the blocking error,
    // then the two key warnings (which never block).
    var line = "", cls = "";
    var missing = beatSoundMineOn() && !beatSound.busy
        ? validateTimerBeatSound() : null;
    if (missing) {
      line = missing; cls = "is-bad";
    } else if (beatSound.present && !beatSound.busy && !beatSound.error) {
      if (beatSound.note === null) {
        line = "Couldn't tell your sound's note — make sure it's " +
            "in the song's key.";
        cls = "is-warn";
      } else if (beatSound.note !== key) {
        // The way out, in the same line: KEY doesn't change the sound
        // (it plays as recorded), so the volunteer needs to know which
        // of the two to fix.
        line = "Your sound is in " + noteLabel(beatSound.note) +
            " but the song is in " + noteLabel(key) + ". The sound " +
            "isn't re-tuned — set KEY to " + noteLabel(beatSound.note) +
            " if that's the song's key, or upload a sound in " +
            noteLabel(key) + ".";
        cls = "is-warn";
      } else {
        // Confirms the fix worked instead of a warning just vanishing.
        line = "Matches the song's key (" + noteLabel(key) + ").";
      }
    }
    noteEl.textContent = line;
    noteEl.hidden = !line;
    noteEl.classList.toggle("is-bad", cls === "is-bad");
    noteEl.classList.toggle("is-warn", cls === "is-warn");
    $("timer-beat-sound-remove").hidden = !beatSound.present;
    $("timer-beat-sound-remove").disabled = beatSound.busy;
  }

  // Mirrors BEAT_MAX_SECONDS in validation.py: 15 minutes at 60 fps.
  var BEAT_MAX_SECONDS = 900;

  // Preview Image objects, cached by (id, blur) so a redraw triggered by an
  // unrelated keystroke (dim slider, warn checkbox, ...) never re-fetches
  // or re-decodes an image that is already on screen — without this cache
  // the canvas would flash back to the plain background on every redraw
  // while the fetch is in flight. Blurred and plain are separate cache
  // entries and separate requests: the blur itself is now baked into the
  // image server-side (see drawTimerBgPlate below) rather than a canvas
  // filter, so the two are genuinely different images, not one image drawn
  // two ways.
  var bgImageCache = {};

  function bgImageCacheKey(id, blur) {
    return id + (blur ? "|blur" : "|plain");
  }

  function getTimerBgImage(id, blur) {
    var key = bgImageCacheKey(id, blur);
    var entry = bgImageCache[key];
    if (entry) return entry;
    entry = { img: new Image(), loaded: false };
    entry.img.onload = function () {
      entry.loaded = true;
      drawTimerPreview();
    };
    entry.img.src = "/api/backgrounds/" + encodeURIComponent(id) +
        (blur ? "?blur=1" : "");
    bgImageCache[key] = entry;
    return entry;
  }

  // Cover-fit (scale to fill, crop the overflow, centred — never letterbox,
  // never distort), then dim. `img` already has the renderer's own blur
  // baked in when BLUR is on (drawTimerBackground picked the right cached
  // image below) — this used to also set `ctx.filter = "blur(9px)"` here,
  // but CanvasRenderingContext2D.filter is a browser feature the app's
  // embedded webview (pywebview -> WKWebView/WebView2) does not reliably
  // apply, so the preview looked sharp while DIM (a plain fillRect, no
  // browser feature involved) visibly worked. Fetching the real Pillow-
  // blurred image instead guarantees parity by construction.
  function drawTimerBgPlate(ctx, img, dimPct) {
    var scale = Math.max(PW / img.naturalWidth, PH / img.naturalHeight);
    var dw = img.naturalWidth * scale;
    var dh = img.naturalHeight * scale;
    ctx.drawImage(img, (PW - dw) / 2, (PH - dh) / 2, dw, dh);

    ctx.save();
    ctx.fillStyle = "#000000";
    ctx.globalAlpha = Math.max(0, Math.min(80, dimPct)) / 100;
    ctx.fillRect(0, 0, PW, PH);
    ctx.restore();
  }

  // Transparent export (spec: docs/specs/alpha-export.md): the checkerboard
  // is a PREVIEW-ONLY convention — the universal "this is transparent"
  // signal used by every image/video editor. The canvas 2D context already
  // composites correctly by default (no JS equivalent of the Pillow bug
  // the renderer works around), so this needs no compositing logic, only
  // the fill itself, drawn fresh every redraw. NEVER baked into anything
  // the renderer produces — the real plate is plain RGBA(0, 0, 0, 0)
  // (render/timer.py's _plates()).
  function drawCheckerboard(ctx) {
    var size = 20;
    for (var y = 0; y < PH; y += size) {
      for (var x = 0; x < PW; x += size) {
        var even = (Math.round(x / size) + Math.round(y / size)) % 2 === 0;
        ctx.fillStyle = even ? "#ffffff" : "#cccccc";
        ctx.fillRect(x, y, size, size);
      }
    }
  }

  // With no images this is exactly paintBackground(), so the empty-set
  // preview never changes. With one, draw the first image (only the first —
  // the preview always shows the opening frame, cycling is video-only).
  function drawTimerBackground(ctx, t) {
    // Transparent (spec: docs/specs/alpha-export.md): checked first, before
    // green — the two are mutually exclusive by construction (the click
    // handlers below never let both read pressed at once).
    if (t.transparent) { drawCheckerboard(ctx); return; }
    // Green screen (spec: docs/specs/green-screen.md): a flat chroma plate,
    // no vignette/dim/blur — the style's track and digits are painted on
    // top of it exactly as with any other background, by the caller.
    if (t.greenScreen) {
      ctx.fillStyle = "#00ff00";
      ctx.fillRect(0, 0, PW, PH);
      return;
    }
    if (!t.backgrounds.length) { paintBackground(ctx); return; }
    var id = t.backgrounds[0];
    var entry = getTimerBgImage(id, t.bgBlur);
    if (entry.loaded) {
      drawTimerBgPlate(ctx, entry.img, t.bgDim);
      return;
    }
    // The blurred variant is still loading (it may need a fresh render on
    // the server the first time) — show the plain image already in cache,
    // if there is one, rather than flash back to the plain dark background.
    if (t.bgBlur) {
      var plain = bgImageCache[bgImageCacheKey(id, false)];
      if (plain && plain.loaded) {
        drawTimerBgPlate(ctx, plain.img, t.bgDim);
        return;
      }
    }
    paintBackground(ctx);
  }

  function renderTimerBgStrip() {
    var strip = $("timer-bg-strip");
    while (strip.firstChild) strip.removeChild(strip.firstChild);
    timerBg.ids.forEach(function (id, idx) {
      var li = document.createElement("li");
      li.className = "bg-thumb";
      var img = document.createElement("img");
      img.src = "/api/backgrounds/" + encodeURIComponent(id);
      img.alt = "";
      var x = document.createElement("button");
      x.type = "button";
      x.className = "bg-thumb-x";
      x.setAttribute("aria-label", "Remove image " + (idx + 1) + " from this timer");
      x.textContent = "×";
      x.addEventListener("click", function () { removeTimerBg(id); });
      li.appendChild(img);
      li.appendChild(x);
      strip.appendChild(li);
    });
    // "+" adds another image. + ADD IMAGE now switches images on and off,
    // so it can no longer be the way to add a second one. The image
    // cap disables THIS, never + ADD IMAGE: that is the only way left to
    // switch images off, and disabling it would trap an operator with
    // images they could not turn off.
    if (timerBg.ids.length >= 1) {
      var atCap = timerBg.ids.length >= BG_PER_TIMER_MAX;
      var addLi = document.createElement("li");
      addLi.className = "bg-thumb bg-thumb-add";
      var addBtn = document.createElement("button");
      addBtn.type = "button";
      addBtn.className = "bg-thumb-add-btn";
      // Visible words, not a bare "+": the unlabelled glyph went unfound,
      // and volunteers reached for the IMAGES switch instead.
      addBtn.textContent = "+ ADD IMAGE";
      addBtn.disabled = atCap;
      addBtn.title = atCap ? "A timer can use up to " + BG_PER_TIMER_MAX +
                             " background images."
                           : "Add another image, or drag images onto this row";
      addBtn.setAttribute("aria-label", addBtn.title);
      addBtn.addEventListener("click", openTimerBgPicker);
      addLi.appendChild(addBtn);
      strip.appendChild(addLi);
    } else {
      // Nothing chosen yet: say that images can be dropped, at the one
      // moment the operator is looking for a way to add them. A plain
      // tile, not a button — the picker below is already open in this
      // state, so a second thing to click would just be noise.
      var hintLi = document.createElement("li");
      hintLi.className = "bg-thumb bg-thumb-hint";
      hintLi.textContent = "Drop images here";
      strip.appendChild(hintLi);
    }
    // #timer-bg-empty is decided in applyTimerBg() alone, which every
    // caller runs straight after this -- it used to be set in both.
  }

  function addTimerBg(id) {
    if (timerBg.ids.length >= BG_PER_TIMER_MAX) return;
    if (timerBg.ids.indexOf(id) !== -1) return;   // already in this set
    timerBg.ids.push(id);
    renderTimerBgStrip();
    updateTimer();
  }

  function removeTimerBg(id) {
    var i = timerBg.ids.indexOf(id);
    if (i === -1) return;
    timerBg.ids.splice(i, 1);
    renderTimerBgStrip();
    updateTimer();
  }

  function bgLibRow(entry) {
    var li = document.createElement("li");
    li.className = "bg-lib-item";

    var thumb = document.createElement("button");
    thumb.type = "button";
    thumb.className = "bg-lib-thumb";
    thumb.title = "Add to this timer";
    var img = document.createElement("img");
    img.src = "/api/backgrounds/" + encodeURIComponent(entry.id);
    img.alt = "";
    thumb.appendChild(img);
    thumb.addEventListener("click", function () { addTimerBg(entry.id); });

    var del = document.createElement("button");
    del.type = "button";
    del.className = "bg-lib-del";
    del.setAttribute("aria-label", "Delete this stored image");
    del.title = "Delete from storage";
    del.textContent = "×";
    del.addEventListener("click", function (e) {
      e.stopPropagation();
      deleteTimerBgLib(entry.id);
    });

    li.appendChild(thumb);
    li.appendChild(del);
    return li;
  }

  function renderTimerBgLib() {
    var list = $("timer-bg-lib-list");
    while (list.firstChild) list.removeChild(list.firstChild);
    var images = (timerBg.library || []);
    images.forEach(function (entry) { list.appendChild(bgLibRow(entry)); });
    // A drop-down that stays shut: what matters is today's images, so the
    // count answers "is anything in there?" without opening it, and an
    // empty library shows nothing at all rather than a fold-out saying so.
    var lib = $("timer-bg-lib");
    lib.hidden = images.length === 0;
    if (images.length === 0) lib.open = false;
    $("timer-bg-lib-summary").textContent = images.length
      ? "SAVED IMAGES (" + images.length + ")"
      : "SAVED IMAGES";
  }

  function refreshTimerBgLibrary() {
    return fetch("/api/backgrounds", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : { images: [] }; })
      .then(function (j) {
        timerBg.library = (j && j.images) || [];
        renderTimerBgLib();
      })
      .catch(function () {
        timerBg.library = timerBg.library || [];
        renderTimerBgLib();
      });
  }

  function openTimerBgPicker() {
    $("timer-bg-picker").hidden = false;
    // Re-fetch every time the panel opens (it's one small JSON GET) rather
    // than trusting a cache that could have gone stale while this view sat
    // unopened — an empty array is still truthy, so "already fetched once"
    // is not the same question as "still correct".
    refreshTimerBgLibrary();
  }

  function closeTimerBgPicker() {
    $("timer-bg-picker").hidden = true;
  }

  // One status line sits inside the picker, one under the thumbnail row.
  // An upload can start from either, and a drop usually lands while the
  // picker is shut, so both carry the same words and whichever is on
  // screen is the one the operator reads.
  function setTimerBgStatus(text) {
    $("timer-bg-upload-status").textContent = text;
    var line = $("timer-bg-drop-status");
    line.textContent = text;
    line.hidden = !text;
  }

  // An image that lands IS the background now, however it arrived. Doing
  // this only once one has actually landed avoids the "on with nothing in
  // it" state applyTimerBg() backs out of: during a drop the ids list is
  // still empty and the picker is shut, which is exactly that state.
  function imagesBecomeBackground() {
    $("timer-bg-images").setAttribute("aria-pressed", "true");
    $("timer-bg-green").setAttribute("aria-pressed", "false");
    $("timer-bg-transparent").setAttribute("aria-pressed", "false");
  }

  function uploadOneTimerBg(file) {
    var fd = new FormData();
    fd.append("image", file);
    return fetch("/api/backgrounds", { method: "POST", body: fd })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok || !j.id) {
            return { error: (j && j.error) || "Could not use that image." };
          }
          return { id: j.id };
        });
      })
      .catch(function () {
        return { error: "Could not upload the image — is the server running?" };
      });
  }

  // POST /api/backgrounds takes one image per request, so a multi-file
  // pick or drop walks them in order rather than firing ten at once: the
  // server cover-fits every image to 1920x1080 as it arrives, and in
  // parallel the operator would get no idea how far through it was.
  function uploadTimerBgFiles(files) {
    var images = [];
    var notImages = 0;
    var i;
    for (i = 0; i < files.length; i += 1) {
      // A drop carries whatever was dragged — a folder, a PDF, anything.
      // The file dialog is filtered by accept=, a drop is not.
      if (files[i].type && files[i].type.indexOf("image/") === 0) {
        images.push(files[i]);
      } else {
        notImages += 1;
      }
    }
    // Stop at the cap rather than uploading images that could not be used:
    // storing them anyway would fill the library toward its own limit with
    // pictures the operator never sees on this timer.
    var room = BG_PER_TIMER_MAX - timerBg.ids.length;
    var overflow = 0;
    if (images.length > room) {
      overflow = images.length - room;
      images = images.slice(0, Math.max(room, 0));
    }

    if (images.length === 0) {
      $("timer-bg-upload").value = "";
      setTimerBgStatus("");
      if (overflow > 0) {
        showError("timer", "This timer already has " + BG_PER_TIMER_MAX +
          " background images — remove one before adding more.");
      } else if (notImages > 0) {
        showError("timer", notImages === 1
          ? "That file is not an image we can read (use PNG or JPG)."
          : "Those files are not images we can read (use PNG or JPG).");
      }
      return;
    }

    var total = images.length;
    var added = 0;
    var failure = "";
    var chain = Promise.resolve();
    images.forEach(function (file, idx) {
      chain = chain.then(function () {
        setTimerBgStatus(total > 1
          ? "Uploading " + (idx + 1) + " of " + total + "…"
          : "Uploading…");
        return uploadOneTimerBg(file).then(function (res) {
          if (!res.id) {
            if (!failure) failure = res.error;
            return;
          }
          added += 1;
          timerBg.library = [{ id: res.id, added: Date.now() / 1000 }]
            .concat(timerBg.library || []);
          renderTimerBgLib();
          // The natural read of "upload a new one": use it on this timer
          // now. Adding each as it lands also means a long drop shows its
          // progress as thumbnails appearing, not just a counter.
          imagesBecomeBackground();
          addTimerBg(res.id);
        });
      });
    });

    chain.then(function () {
      $("timer-bg-upload").value = "";
      var problems = [];
      if (failure) problems.push(failure);
      if (notImages > 0) {
        problems.push(notImages === 1
          ? "One file was not an image, so it was skipped."
          : notImages + " files were not images, so they were skipped.");
      }
      if (overflow > 0) {
        problems.push("A timer can use up to " + BG_PER_TIMER_MAX +
          " background images, so " +
          (overflow === 1 ? "one more was" : overflow + " more were") +
          " not added.");
      }
      if (problems.length) showError("timer", problems.join(" "));
      else hideError("timer");

      if (added > 1) {
        // Cleared shortly after, so it cannot sit there describing
        // something that stopped being true the moment anything else
        // changed (docs/user-flows.md, rule 7).
        var msg = "Added " + added + " images.";
        setTimerBgStatus(msg);
        setTimeout(function () {
          if ($("timer-bg-drop-status").textContent === msg) {
            setTimerBgStatus("");
          }
        }, 4000);
      } else {
        setTimerBgStatus("");
      }
    });
  }

  // Drag and drop onto the background block (owner request): dropping a
  // handful of images straight in is what most people reach for, and it
  // skips the picker entirely. The zone is the whole block rather than the
  // <ul> alone — with no images yet that list is empty and a few pixels
  // tall, which is impossible to aim at — and it stays droppable while
  // GREEN SCREEN or TRANSPARENT is on, where dropping images reads as
  // "actually, use these instead".
  function wireTimerBgDrop() {
    var zone = $("timer-bg-drop");
    var depth = 0;      // dragenter/dragleave fire for every child too

    function draggingFiles(e) {
      var types = e.dataTransfer && e.dataTransfer.types;
      if (!types) return false;
      var i;
      for (i = 0; i < types.length; i += 1) {
        // DOMStringList in some engines, so no indexOf().
        if (types[i] === "Files") return true;
      }
      return false;
    }

    zone.addEventListener("dragenter", function (e) {
      if (!draggingFiles(e)) return;
      e.preventDefault();
      depth += 1;
      zone.classList.add("is-drop-target");
    });
    zone.addEventListener("dragover", function (e) {
      if (!draggingFiles(e)) return;
      // Without preventDefault on dragover the browser refuses the drop.
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    });
    zone.addEventListener("dragleave", function () {
      depth -= 1;
      if (depth <= 0) {
        depth = 0;
        zone.classList.remove("is-drop-target");
      }
    });
    zone.addEventListener("drop", function (e) {
      if (!draggingFiles(e)) return;
      e.preventDefault();
      depth = 0;
      zone.classList.remove("is-drop-target");
      // The beat opener has no background: swallow the drop (so the
      // webview does not navigate to the file) but add nothing.
      if (beatOpenerOn()) return;
      var files = e.dataTransfer.files;
      if (files && files.length) uploadTimerBgFiles(files);
    });
  }

  function deleteTimerBgLib(id) {
    if (!window.confirm("Delete this stored background image? It will no " +
      "longer appear in the picker for any timer.")) return;
    fetch("/api/backgrounds/" + encodeURIComponent(id), { method: "DELETE" })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) {
            var e = new Error("reject");
            e.userMessage = (j && j.error) || "Could not delete that image.";
            throw e;
          }
        });
      })
      .then(function () {
        hideError("timer");
        timerBg.library = (timerBg.library || []).filter(
          function (im) { return im.id !== id; });
        renderTimerBgLib();
        // The file is gone from storage — keep it out of the current set
        // too, or export would fail with "missing" once submitted.
        removeTimerBg(id);
      })
      .catch(function (err) {
        showError("timer", (err && err.userMessage) || "Could not delete that image.");
      });
  }

  // Only shown with 2+ images (spec table) — a single image never cycles,
  // so there is nothing for "seconds per image" to mean. Dim and blur go the
  // same way at zero images: with the plain dark background there is nothing
  // to darken or soften, so offering the controls only invites the question
  // of why moving them does nothing.
  //
  // Found from the ids rather than given their own, because index.html is
  // being edited by another session right now and this needs no markup
  // change: each control sits in a stable wrapper followed by its hint.
  function timerBgOptional() {
    var dim = $("timer-bg-dim").closest(".range-field");
    var blur = $("timer-bg-blur").closest(".check-row");
    return [dim, dim && dim.nextElementSibling,
            blur, blur && blur.nextElementSibling];
  }

  function applyTimerBg(isClockMode) {
    // Beat opener: the whole screen flashes white and black, so there is no
    // background to choose. Switch all three choices off (images stay
    // stored, like any time IMAGES is switched off) and disable them --
    // an unpressed, disabled button cannot leave a hidden choice that
    // blocks or alters the export (docs/user-flows.md rule 4).
    var beat = beatOpenerOn();
    ["timer-bg-images", "timer-bg-green", "timer-bg-transparent"]
      .forEach(function (id) {
        var btn = $(id);
        if (beat) btn.setAttribute("aria-pressed", "false");
        btn.disabled = beat;
        btn.title = beat ? "The beat opener flashes the screen white " +
                           "and black." : "";
      });
    var green = $("timer-bg-green").getAttribute("aria-pressed") === "true";
    var transparent = $("timer-bg-transparent").getAttribute("aria-pressed") === "true";
    // Transparent (spec: docs/specs/alpha-export.md) hides the image UI for
    // the same reason green screen does — there is no background to pick
    // images for — so every place below that used to check `green` alone
    // now checks either one.
    var imagesOn = bgImagesOn();
    // Backed out: images chosen, panel closed, nothing picked. Images are
    // not really the background, so + ADD IMAGE returns to plain. Covers
    // DONE with nothing picked, and removing the last thumbnail.
    if (imagesOn && timerBg.ids.length === 0 &&
        $("timer-bg-picker").hidden) {
      $("timer-bg-images").setAttribute("aria-pressed", "false");
      imagesOn = false;
    }
    var hideImages = !imagesOn;
    var multi = timerBg.ids.length >= 2;
    var any = timerBg.ids.length >= 1;
    // Green screen (spec: docs/specs/green-screen.md): the whole image UI
    // hides while green is on — timerBg.ids itself is untouched, so
    // turning green back off restores the strip exactly as it was.
    $("timer-bg-strip").hidden = hideImages;
    // + ADD IMAGE stays visible whatever is chosen: with TRANSPARENT on
    // and it hidden, GREEN SCREEN looked like the only alternative, so
    // there appeared to be two background choices instead of three. Its
    // selected look is aria-pressed, the same as its two neighbours.
    // The picker's own trigger just went hidden above; if it was left open
    // from before green/transparent was switched on, close it rather than
    // leave an orphaned panel with no visible way back to it.
    if (hideImages && !$("timer-bg-picker").hidden) closeTimerBgPicker();
    $("timer-bg-seconds-field").hidden = hideImages || !multi;
    $("timer-bg-seconds-hint").hidden = hideImages || !multi;
    timerBgOptional().forEach(function (el) {
      if (el) el.hidden = hideImages || !any;
    });
    $("timer-bg-dim-value").textContent = $("timer-bg-dim").value + "%";

    // Usage hint under the TRANSPARENT toggle itself (copy review round 2,
    // spec: docs/specs/alpha-export.md) — the owner asked which format to
    // pick for a plain 5-minute timer, and the honest answer is "neither,
    // leave this off", so this steers a volunteer away from reaching for
    // Transparent on a normal Sunday timer before they ever see the
    // format picker below.
    $("timer-transparent-usage-hint").hidden = !transparent;

    // Format picker + hint: STANDARD (qtrle) / PRORES (prores). Copy
    // review round 2 of a coordinator-directed correction OVER the
    // spec's original SMALLER FILE/MAXIMUM COMPATIBILITY wording (spec:
    // docs/specs/alpha-export.md) — round 1 didn't say which button was
    // actually ProRes; round 2 makes the recommendation directive rather
    // than descriptive, and switches the size figures from a 15-minute
    // to a 5-minute countdown (the realistic common case). Both formats
    // measured side by side against source frames (qtrle pixel-exact;
    // ProRes 4444 max channel error 1/255, invisible), so neither hint
    // may imply the smaller file is lower quality — "Pixel-perfect" on
    // STANDARD is literally true, not marketing. The qtrle/prores API
    // value tokens this reads from are unchanged — display copy only.
    // Round 3 adds a technical line (container/codec/pixel format) under
    // each recommendation for the rare volunteer who is also the editor
    // and wants to verify compatibility themselves — timer-transparent-
    // tech below, a separate and deliberately more subdued element (see
    // .hint-tech in style.css) so it never competes with the plain-
    // English recommendation above it.
    $("timer-transparent-format-intro").hidden = !transparent;
    $("timer-transparent-format-group").hidden = !transparent;
    var transparentHint = $("timer-transparent-hint");
    var transparentTech = $("timer-transparent-tech");
    var transparentWhy = $("timer-transparent-why");
    transparentHint.hidden = !transparent;
    transparentTech.hidden = !transparent;
    if (transparent) {
      var formatEl = document.querySelector('input[name="timer-transparent-format"]:checked');
      var isProres = !!formatEl && formatEl.value === "prores";
      // qtrle is unverified in ProPresenter (spec "Do not claim or imply
      // qtrle works in ProPresenter, anywhere") — kept exactly, per the
      // owner: this is the honest status, not to be softened to "may not
      // work" or dropped.
      transparentHint.textContent = isProres ?
          "Only if you are putting the file straight into ProPresenter " +
          "without editing it first. Bigger — about 530 MB for a " +
          "5-minute countdown." :
          "Use this one. Pixel-perfect, and about 180 MB for a " +
          "5-minute countdown. Opens in CapCut and other editing " +
          "software. Not tested in ProPresenter.";
      // Probed values, not the encode-time request: ProRes 4444 is
      // requested at yuva444p10le but ffmpeg's own probe reports
      // yuva444p12le (spec's Encoder section, "Verified during spec
      // research" #1) — showing the request instead would look like a
      // mismatch to anyone who inspects the file themselves in
      // MediaInfo/ffprobe/an editor.
      transparentTech.textContent = isProres ?
          ".mov — Apple ProRes 4444, yuva444p12le" :
          ".mov — QuickTime Animation (RLE), argb, lossless";
      // ProRes only: the operator asked why this format exists at
      // all, so the answer sits where they pick it. The
      // ProPresenter claim is warranted HERE and only here --
      // ProRes 4444 with alpha is in ProPresenter's documented
      // format list, where qtrle is not.
      transparentWhy.hidden = !isProres;
    } else {
      transparentWhy.hidden = true;
    }

    // The shared preview caption is hardcoded "H.264 MP4" across four
    // tiles, which stops being true the moment TRANSPARENT is on --
    // that export is a .mov. Correct it for this tile only.
    //
    // Smoother milliseconds (60 fps) (spec: docs/specs/millis-60fps.md):
    // this line also names the real output fps, so it must say 60 the
    // moment that's actually what will render -- countdown mode, millis
    // on, AND the box itself checked (applyTimerMode() already ran this
    // pass and auto-unchecked it past the 15-minute cap, so reading the
    // checkbox directly here is the true, corrected state).
    var specLine = $("timer-spec-line");
    if (specLine) {
      var showMillisOn = isClockMode ? $("timer-show-millis").checked
                                     : countdownMillisOn();
      var millis60fpsLive = !isClockMode && showMillisOn &&
          $("timer-millis-60fps").checked;
      // Plain timers export at 15fps (TIMER_OUTPUT_FPS in
      // render/timer.py) -- the digits change once a second, so more
      // frames buy nothing. This caption said 30fps for them; the real
      // rates were confirmed from golden.py's decoded frame counts: 15
      // plain, 30 with milliseconds, 60 with smoother milliseconds.
      var fpsTag = !showMillisOn ? "15fps"
          : (millis60fpsLive ? "60fps" : "30fps");
      if (beat) {
        // Always 60 fps, and the only export with an audio track. The
        // preview is one still white beat, so the caption under it is
        // where the flashing has to be said (UX review).
        specLine.textContent = "Preview shows a white beat. The video " +
            "flashes white and black with a note - 60fps H.264 MP4 " +
            "with sound";
      } else {
        specLine.textContent = transparent
            ? "1920x1080 - " + fpsTag + " - .mov with transparency"
            : "1920x1080 - " + fpsTag + " - H.264 MP4";
      }
    }
    // Same problem on the button itself: it is hardcoded
    // "EXPORT MP4" in the markup, and a transparent export is a
    // .mov. Caught by looking at the tile, not by reading the DOM.
    var exportBtn = $("timer-export");
    if (exportBtn && !exportBusy["timer"]) {
      exportBtn.textContent = transparent ? "EXPORT MOV"
                                          : "EXPORT MP4";
    }

    var emptyHint = $("timer-bg-empty");
    if (beat) {
      emptyHint.hidden = false;
      emptyHint.textContent = "The beat opener flashes the screen white " +
          "and black. Your background comes back when you untick it.";
    } else if (transparent) {
      emptyHint.hidden = false;
      emptyHint.textContent = "Transparent — no background at all. " +
          "Overlay this file directly on your own video, no keying needed.";
    } else if (green) {
      emptyHint.hidden = false;
      emptyHint.textContent = "Solid green — key it out in your " +
          "video software to put your own background behind the numbers.";
    } else if (imagesOn && any) {
      emptyHint.hidden = true;        // the thumbnails say it
    } else if (any) {
      // Images stored but switched off: without this the background went
      // dark with nothing on screen to say why.
      emptyHint.hidden = false;
      emptyHint.textContent = "Images are off — plain dark background. " +
          "Click IMAGES to bring them back.";
    } else {
      emptyHint.hidden = false;
      emptyHint.textContent = "No images — plain dark background.";
    }
  }

  function validateTimerBg() {
    // Mirrors _int_field's generic message template in app.py for both
    // fields — neither has a custom override there (spec: "bg_seconds:
    // _int_field ... label 'Seconds per image'", "bg_dim: _int_field ...
    // label 'Dim'").
    //
    // Green screen (spec: docs/specs/green-screen.md) and Transparent
    // (spec: docs/specs/alpha-export.md): a stale id count in the hidden
    // set (kept in memory so turning either off restores it) must never
    // block an export — the ids aren't even sent while either is on
    // (readTimer() forces backgrounds to []).
    // Only images that are really the background can block an export:
    // stored-but-switched-off ones are never sent (rule 4 in
    // docs/user-flows.md -- nothing hidden may block the operator).
    // The beat opener has no background at all, so none of this applies.
    if (!bgImagesOn() || beatOpenerOn()) return null;
    if (timerBg.ids.length > BG_PER_TIMER_MAX) {
      return "A timer can use up to " + BG_PER_TIMER_MAX +
             " background images.";
    }
    if (timerBg.ids.length >= 2) {
      var secs = intFrom($("timer-bg-seconds"));
      if (secs === null || secs < 2 || secs > 120) {
        return "Seconds per image must be a whole number between 2 and 120.";
      }
    }
    var dim = intFrom($("timer-bg-dim"));
    if (dim === null || dim < 0 || dim > 80) {
      return "Dim must be a whole number between 0 and 80.";
    }
    return null;
  }

  // mode is "countdown" (default, untouched behaviour) or "clock" — a wall
  // clock that starts at a chosen time and ticks forward in real time.
  function readTimer() {
    var styleEl = document.querySelector('input[name="timer-style"]:checked');
    var modeEl = document.querySelector('input[name="timer-mode"]:checked');
    var formatEl = document.querySelector('input[name="timer-clock-format"]:checked');
    // Green screen (spec: docs/specs/green-screen.md): read once so
    // `greenScreen` and `backgrounds` below are both derived from the one
    // source of truth (the button's aria-pressed) instead of a JS flag
    // that could drift from the DOM.
    var green = $("timer-bg-green").getAttribute("aria-pressed") === "true";
    // Transparent (spec: docs/specs/alpha-export.md): same one-source-of-
    // truth reasoning as green above. Not a plain bool downstream —
    // there is no single "on", only a specific format — so this reads
    // straight to the format string, `false` when the toggle itself is off.
    var transparentOn = $("timer-bg-transparent").getAttribute("aria-pressed") === "true";
    var transparentFormatEl = document.querySelector('input[name="timer-transparent-format"]:checked');
    var mode = modeEl ? modeEl.value : "countdown";
    // Typed countdown format (spec: docs/specs/countdown-format.md): in
    // countdown mode it alone decides milliseconds -- the checkbox is
    // clock-only now and hidden there, so a stale tick from clock mode
    // must not leak into a countdown. An invalid format reads as "no
    // millis" here; validateTimer() is what blocks the export.
    var parsed = parseCountdownFormat($("timer-format").value);
    var showMillis = mode === "clock" ? $("timer-show-millis").checked
        : !!(parsed.layout && parsed.layout.ms > 0);
    // Beat opener: classic only, and no background of any kind. Forced here
    // as well as by applyTimerMode()/applyTimerBg() so the payload is right
    // by construction even if a stale RING tick or an image choice survived.
    var beat = beatOpenerOn();
    return {
      mode: mode,
      minutes: toInt($("timer-minutes").value, 0),
      seconds: toInt($("timer-seconds").value, 0),
      style: beat ? "classic" : (styleEl ? styleEl.value : "classic"),
      beatOpener: beat,
      bpm: toInt($("timer-bpm").value, 120),
      key: $("timer-key").value || "A",
      // "builtin" unless the opener is on AND "My sound" is chosen.
      beatSound: beatSoundMineOn() ? "mine" : "builtin",
      accent: currentAccent("timer"),
      warn: $("timer-warn").checked,
      hold: toInt($("timer-hold").value, 5),
      clockHours: toInt($("timer-clock-hours").value, 19),
      clockMinutes: toInt($("timer-clock-minutes").value, 59),
      clockSeconds: toInt($("timer-clock-seconds").value, 50),
      clockLength: toInt($("timer-clock-length").value, 30),
      clockFormat: formatEl ? formatEl.value : "12h",
      showSeconds: $("timer-clock-show-seconds").checked,
      showMillis: showMillis,
      displayFormat: $("timer-format").value.replace(/^\s+|\s+$/g, ""),
      formatLayout: parsed.layout,
      formatError: parsed.error,
      // Digits in the millis run: the typed count, or the clock's fixed 3.
      msDigits: parsed.layout && mode !== "clock" ? parsed.layout.ms : 3,
      // Full-size / Hold-at-zero millis (spec: docs/specs/millis-reveal.md):
      // countdown-only, read unconditionally -- timerPayload() is what
      // keeps them out of the clock branch.
      millisFullSize: $("timer-millis-full-size").checked,
      millisReveal: $("timer-millis-reveal").checked,
      millisRevealSeconds: toInt($("timer-millis-reveal-seconds").value, 60),
      // Smoother milliseconds (60 fps) (spec: docs/specs/millis-60fps.md):
      // countdown-only, read unconditionally like millisFullSize above --
      // timerPayload() is what keeps it out of the clock branch. Reflects
      // applyTimerMode()'s disable/auto-uncheck-past-15-min correction,
      // which always runs earlier in the same updateTimer() pass.
      millis60fps: $("timer-millis-60fps").checked,
      greenScreen: green && !beat,
      transparent: transparentOn && !beat ?
          (transparentFormatEl ? transparentFormatEl.value : "qtrle") : false,
      // Backgrounds (spec: docs/specs/timer-backgrounds.md): valid in both
      // modes. `backgrounds` comes from JS state (timerBg.ids), not a DOM
      // field — the strip is built dynamically, there is no single input
      // that holds the set. Forced to [] under green screen or transparent
      // (spec: docs/specs/green-screen.md, docs/specs/alpha-export.md) so
      // hasBg (backgrounds.length > 0, used throughout for the digit-
      // shadow halo) and the export payload are both right by
      // construction — no separate case needed anywhere downstream.
      backgrounds: bgImagesOn() && !beat ? timerBg.ids.slice() : [],
      bgSeconds: toInt($("timer-bg-seconds").value, 10),
      bgDim: toInt($("timer-bg-dim").value, 45),
      bgBlur: $("timer-bg-blur").checked
    };
  }

  function validateTimerDuration() {
    var m = intFrom($("timer-minutes"));
    var s = intFrom($("timer-seconds"));
    if (m === null || m < 0 || m > 120) return "Minutes must be a whole number from 0 to 120.";
    if (s === null || s < 0 || s > 59) return "Seconds must be a whole number from 0 to 59.";
    var total = m * 60 + s;
    if (total < 5) return "The timer must run for at least 5 seconds.";
    // Mirrors BEAT_MAX_SECONDS in validation.py. Checked before the 120
    // minute cap so the operator sees the limit that actually applies.
    if (beatOpenerOn() && total > BEAT_MAX_SECONDS) {
      return "With the beat opener on, the timer can run for at most " +
          "15 minutes.";
    }
    if (total > 7200) return "The timer can run for at most 120 minutes in total.";
    // Mirrors MILLIS_MAX_SECONDS in validation.py: 30 fps with nothing
    // cacheable.
    if (countdownMillisOn() && total > 1800) {
      return "With milliseconds on, the timer can run for at most 30 minutes. Turn milliseconds off for a longer timer.";
    }
    return null;
  }

  // Only while the opener is ticked in countdown mode: a hidden BPM box
  // must never block a render (docs/user-flows.md rule 4).
  function validateTimerBpm() {
    if (!beatOpenerOn()) return null;
    var bpm = intFrom($("timer-bpm"));
    if (bpm === null || bpm < 60 || bpm > 200) {
      return "BPM must be a whole number between 60 and 200.";
    }
    return null;
  }

  function validateTimerHold() {
    var hold = intFrom($("timer-hold"));
    if (hold === null || hold < 0 || hold > 30) return '"Keep 0:00 on screen" must be 0 to 30 seconds.';
    return null;
  }

  // Range check only (spec: docs/specs/millis-reveal.md API contract) --
  // seconds >= total is NOT checked here: it's a described, not rejected,
  // degenerate case (see updateTimer()'s hint text below), so it must
  // never block export the way an out-of-range value does.
  function validateTimerMillisRevealSeconds() {
    var s = intFrom($("timer-millis-reveal-seconds"));
    if (s === null || s < 1 || s > 1800) {
      return "Milliseconds can start ticking with 1 to 1800 seconds " +
          "left on the timer.";
    }
    return null;
  }

  // Messages mirror validate_timer_options()'s clock branch in app.py exactly.
  function validateTimerClockStart() {
    var h = intFrom($("timer-clock-hours"));
    var m = intFrom($("timer-clock-minutes"));
    var s = intFrom($("timer-clock-seconds"));
    if (h === null || h < 0 || h > 23 ||
        m === null || m < 0 || m > 59 ||
        s === null || s < 0 || s > 59) {
      return "Start time must look like 19:59:50 (24-hour, hours 0-23).";
    }
    return null;
  }

  function validateTimerClockLength() {
    var s = intFrom($("timer-clock-length"));
    if (s === null || s < 5 || s > 1800) {
      return "Clip length must be a whole number between 5 and 1800 seconds.";
    }
    return null;
  }

  function validateTimer() {
    var t = readTimer();
    // Backgrounds are valid in BOTH modes (spec), so this check runs before
    // the mode branch rather than being duplicated in each arm.
    var bgErr = validateTimerBg();
    if (bgErr) return bgErr;
    if (t.mode === "clock") return validateTimerClockStart() || validateTimerClockLength();
    // The seconds box only means something while milliseconds are shown
    // AND holding at zero. Checking it unconditionally blocked a plain
    // quick timer whenever that box -- hidden by then -- had been left
    // empty: an error about a feature the operator had switched off,
    // pointing at a field they could no longer see.
    return t.formatError || validateTimerDuration() || validateTimerBpm() ||
        validateTimerBeatSound() || validateTimerHold() ||
        (t.showMillis && t.millisReveal
            ? validateTimerMillisRevealSeconds() : null);
  }

  // Typed countdown format (spec: docs/specs/countdown-format.md). Mirrors
  // parse_countdown_format() in render/timer.py line for line -- the same
  // rules in the same order, so the same typing gets the same message --
  // because the preview must show exactly what the first frame will.
  // Returns {layout: null|{units, ms, msSep}, error: null|"message"}.
  var FORMAT_ERRORS = {
    chars: "Use only H, M, S, colons and .000 — like M:SS.000.",
    mixed: "Put a colon between the units, like M:SS.",
    order: "Write the units biggest first, ending in seconds: " +
        "H:MM:SS, M:SS or SS.",
    pair: "Every unit after a colon needs two letters, like M:SS.",
    wide: "The first unit can be at most 3 letters wide.",
    millis: "Milliseconds go at the end as .0, .00 or .000.",
    long: "The format can be at most 16 characters."
  };
  var UNIT_SECONDS = { H: 3600, M: 60, S: 1 };

  // The countdown's millis switch now: does the typed format end in .0s?
  function countdownMillisOn() {
    var p = parseCountdownFormat($("timer-format").value);
    return !!(p.layout && p.layout.ms > 0);
  }

  // The millis run as drawn: the separator the operator typed (":" for
  // M:SS:000, "." otherwise) then one to three zeros. The first frame is
  // always the full total, so the digits are always zeros.
  function millisRunText(layout, digits) {
    return ((layout && layout.msSep) || ".") + "000".slice(0, digits);
  }

  function parseCountdownFormat(raw) {
    var fail = function (key) { return { layout: null, error: FORMAT_ERRORS[key] }; };
    if (raw.length > 16) return fail("long");
    var text = raw.replace(/^\s+|\s+$/g, "");
    if (!text) return { layout: null, error: null };
    var dotAt = text.indexOf(".");
    var main = dotAt < 0 ? text : text.slice(0, dotAt);
    var ms = 0;
    var msSep = ".";
    if (dotAt >= 0) {
      var tail = text.slice(dotAt + 1);
      if (tail.length < 1 || tail.length > 3 || !/^0+$/.test(tail)) {
        return fail("millis");
      }
      ms = tail.length;
    } else {
      // Colon form (spec: docs/specs/beat-opener.md FORMAT addendum): with
      // no "." anywhere, a LAST colon group of 1-3 zeros is the
      // milliseconds -- M:SS:000 -- and the colon typed is the colon drawn.
      // Unit groups are letters, so a zeros group can never be one.
      var lastColon = text.lastIndexOf(":");
      if (lastColon >= 0) {
        var colonTail = text.slice(lastColon + 1);
        // Mirrors the Python: a last group of zeros is always the millis
        // part, and four or more gets the millis message, not "only H, M
        // and S" -- the volunteer clearly meant milliseconds.
        if (/^0+$/.test(colonTail)) {
          if (colonTail.length > 3) return fail("millis");
          main = text.slice(0, lastColon);
          ms = colonTail.length;
          msSep = ":";
        }
      }
    }
    main = main.toUpperCase();
    if (!/^[HMS:]*$/.test(main)) return fail("chars");
    var groups = main.split(":");
    var i, letters = "";
    for (i = 0; i < groups.length; i++) {
      if (!/^(H*|M*|S*)$/.test(groups[i])) return fail("mixed");
    }
    for (i = 0; i < groups.length; i++) letters += groups[i].charAt(0);
    var hasEmpty = false;
    for (i = 0; i < groups.length; i++) if (!groups[i]) hasEmpty = true;
    if (hasEmpty || (letters !== "S" && letters !== "MS" && letters !== "HMS")) {
      return fail("order");
    }
    for (i = 1; i < groups.length; i++) {
      if (groups[i].length !== 2) return fail("pair");
    }
    if (groups[0].length > 3) return fail("wide");
    var units = [];
    for (i = 0; i < groups.length; i++) {
      units.push({ letter: groups[i].charAt(0), width: groups[i].length });
    }
    return { layout: { units: units, ms: ms, msSep: msSep }, error: null };
  }

  // Mirrors _format_with_units(): the leftmost unit takes everything above
  // it and is padded to its letter count; the rest are two digits.
  function formatWithUnits(rem, units) {
    var parts = [], above = null;
    for (var i = 0; i < units.length; i++) {
      var unit = UNIT_SECONDS[units[i].letter];
      var value = above === null ? Math.floor(rem / unit)
                                 : Math.floor((rem % above) / unit);
      var s = String(value);
      var width = i === 0 ? units[i].width : 2;
      while (s.length < width) s = "0" + s;
      parts.push(s);
      above = unit;
    }
    return parts.join(":");
  }

  // Same display rule as the renderer: unpadded minutes, H:MM:SS above 1 hour.
  // Mirrors _format_remaining in render/timer.py: zero-padded to the initial
  // total's width so the preview shows exactly what the video will.
  //
  // A typed `layout` (parseCountdownFormat below) wins over both rules.
  function formatClock(remaining, total, fixed, layout) {
    if (layout) return formatWithUnits(remaining, layout.units);
    if (fixed) {
      return pad2(Math.floor(remaining / 3600)) + ":" +
        pad2(Math.floor((remaining % 3600) / 60)) + ":" + pad2(remaining % 60);
    }
    if (total >= 3600) {
      return Math.floor(remaining / 3600) + ":" + pad2(Math.floor((remaining % 3600) / 60)) + ":" + pad2(remaining % 60);
    }
    if (total >= 600) {
      return pad2(Math.floor(remaining / 60)) + ":" + pad2(remaining % 60);
    }
    return Math.floor(remaining / 60) + ":" + pad2(remaining % 60);
  }

  // Fixed-width slots: every digit centred in a slot as wide as the widest
  // digit; colon slot is 55% of that, "." slot 40% (mirrors _digits_metrics
  // in timer.py — the "." slot only matters to clock mode's milliseconds).
  function digitMetrics(ctx, px) {
    ctx.font = "700 " + px + "px " + FONT_DIGITS;
    var slot = 0;
    "0123456789".split("").forEach(function (ch) {
      slot = Math.max(slot, ctx.measureText(ch).width);
    });
    return { px: px, slot: slot, colon: slot * 0.55, dot: slot * 0.40 };
  }

  function slotWidth(ch, met) {
    if (ch === ":") return met.colon;
    if (ch === ".") return met.dot;
    return met.slot;
  }

  function clockWidth(text, met) {
    var w = 0;
    text.split("").forEach(function (ch) { w += slotWidth(ch, met); });
    return w;
  }

  // Digit shadow (spec: docs/specs/timer-backgrounds.md addendum) — mirrors
  // render/timer.py's _paste_digits: a dark halo behind the digits so they
  // stay readable over a busy, bright background image. `hasBg` is only
  // true when a real image is in use (never the plain vignette), so the
  // no-background preview never grows a shadow. shadowBlur 9 is HALF the
  // renderer's 18px radius because this canvas is half the 1920x1080
  // export — same halving rule the background blur used to apply via
  // ctx.filter (now fetched pre-blurred from the server instead, see
  // drawTimerBgPlate above, but the 18px/2 scale factor is the same one).
  // Reset to 0 straight after so nothing drawn afterwards (the ring/bar
  // track, etc.) inherits it.
  function drawClock(ctx, text, cx, cy, met, color, hasBg) {
    ctx.font = "700 " + met.px + "px " + FONT_DIGITS;
    ctx.fillStyle = color;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    if (hasBg) {
      ctx.shadowColor = "rgba(0, 0, 0, 0.7)";
      ctx.shadowBlur = 9;
    }
    var x = cx - clockWidth(text, met) / 2;
    text.split("").forEach(function (ch) {
      var w = slotWidth(ch, met);
      ctx.fillText(ch, x + w / 2, cy);
      x += w;
    });
    if (hasBg) ctx.shadowBlur = 0;
  }

  // ---- clock-mode display rules (spec: docs/specs/clock-mode.md) ----------
  // Mirrors format_clock_time() in render/timer.py so the preview always
  // shows the exact string the renderer's first frame will. Returns the
  // pieces separately (not one string) because the main digits, the millis
  // suffix and the AM/PM tag are drawn at three different sizes/colours.
  function formatClockTime(totalMs, fmt, showSeconds, showMillis) {
    var DAY_MS = 24 * 3600 * 1000;
    var t = ((totalMs % DAY_MS) + DAY_MS) % DAY_MS;
    var ms = t % 1000;
    var totalSec = Math.floor(t / 1000);
    var hh = Math.floor(totalSec / 3600);
    var mm = Math.floor((totalSec % 3600) / 60);
    var ss = totalSec % 60;

    var tag = "";
    var hourStr;
    if (fmt === "12h") {
      var h12 = hh % 12;
      if (h12 === 0) h12 = 12;
      hourStr = String(h12);
      tag = hh < 12 ? "AM" : "PM";
    } else {
      hourStr = pad2(hh);
    }

    var base = hourStr + ":" + pad2(mm);
    var millis = "";
    if (showMillis) {
      base += ":" + pad2(ss);
      millis = "." + ("00" + ms).slice(-3);
    } else if (showSeconds) {
      base += ":" + pad2(ss);
    }
    return { base: base, millis: millis, tag: tag };
  }

  // The label shown under the "Start time" fields, e.g. "Shows as 7:59:50 PM".
  function timerClockStartLabel(t) {
    var totalMs = (t.clockHours * 3600 + t.clockMinutes * 60 + t.clockSeconds) * 1000;
    var f = formatClockTime(totalMs, t.clockFormat, t.showSeconds, t.showMillis);
    return f.base + f.millis + (f.tag ? " " + f.tag : "");
  }

  // AM/PM is drawn at a FIXED width (the wider of the two measured) so
  // switching formats never shifts the clock — same rule as the renderer.
  function tagWidth(ctx, px) {
    ctx.font = "700 " + px + "px " + FONT_LABEL;
    return Math.max(ctx.measureText("AM").width, ctx.measureText("PM").width);
  }

  // msScale (spec: docs/specs/millis-reveal.md "Preview"): the millis run's
  // size as a fraction of the main digits' px, default 0.55 (today's fixed
  // ratio) so every caller that doesn't pass it -- clock mode, and any
  // countdown call site that predates this option -- is pixel-unchanged.
  // Full-size millis passes 1.0.
  function clockCompositeWidth(ctx, base, millis, tag, px, msScale) {
    var met = digitMetrics(ctx, px);
    var w = clockWidth(base, met);
    if (millis) {
      var scale = (msScale === undefined) ? 0.55 : msScale;
      var mpx = Math.max(1, Math.round(px * scale));
      w += clockWidth(millis, digitMetrics(ctx, mpx));
    }
    if (tag) {
      var tpx = Math.max(1, Math.round(px * 0.28));
      w += met.colon + tagWidth(ctx, tpx);
    }
    return w;
  }

  // Draws "base" (main digits, full size) + "millis" (55% size, same
  // colour, same baseline) + "tag" (28% size, accent colour, one colon-slot
  // gap after the last digit) as a single centred line. Can't reuse
  // drawClock() directly — that draws one string at one uniform size — but
  // reuses its digitMetrics()/clockWidth() geometry throughout.
  function drawClockComposite(
      ctx, base, millis, tag, cx, cy, px, color, accent, hasBg, msScale) {
    var totalW = clockCompositeWidth(ctx, base, millis, tag, px, msScale);
    var met = digitMetrics(ctx, px);
    var x = cx - totalW / 2;

    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    var baseY = cy + px * 0.34;   // digits' shared baseline, centred overall

    // Digit shadow (spec: docs/specs/timer-backgrounds.md addendum) — see
    // drawClock() above for the full rationale. Covers all three runs
    // (main/millis/tag) since together they are one "digit block" on the
    // renderer side; reset once at the end, after the tag is drawn.
    if (hasBg) {
      ctx.shadowColor = "rgba(0, 0, 0, 0.7)";
      ctx.shadowBlur = 9;
    }

    ctx.font = "700 " + px + "px " + FONT_DIGITS;
    ctx.fillStyle = color;
    base.split("").forEach(function (ch) {
      var w = slotWidth(ch, met);
      ctx.fillText(ch, x + w / 2, baseY);
      x += w;
    });

    if (millis) {
      var scale = (msScale === undefined) ? 0.55 : msScale;
      var mpx = Math.max(1, Math.round(px * scale));
      var mmet = digitMetrics(ctx, mpx);
      ctx.font = "700 " + mpx + "px " + FONT_DIGITS;
      millis.split("").forEach(function (ch) {
        var w = slotWidth(ch, mmet);
        ctx.fillText(ch, x + w / 2, baseY);
        x += w;
      });
    }

    if (tag) {
      x += met.colon;
      var tpx = Math.max(1, Math.round(px * 0.28));
      var tw = tagWidth(ctx, tpx);
      ctx.font = "700 " + tpx + "px " + FONT_LABEL;
      ctx.fillStyle = accent;
      ctx.fillText(tag, x + tw / 2, baseY);
    }

    if (hasBg) ctx.shadowBlur = 0;
  }

  // Clock mode's preview: always the FIRST frame (elapsed = 0), so seconds
  // come straight from the chosen start time and milliseconds are always
  // exactly .000 (ms = round(i * 1000 / fps), i = 0) — never wall-clock time.
  function drawClockTimerPreview(ctx, t) {
    var totalMs = (t.clockHours * 3600 + t.clockMinutes * 60 + t.clockSeconds) * 1000;
    var f = formatClockTime(totalMs, t.clockFormat, t.showSeconds, t.showMillis);

    if (t.style === "ring") {
      // render: centreline radius 400, thickness 26, digits 190px (all at 2x)
      var R = 200, thick = 13;
      ctx.lineWidth = thick;
      ctx.strokeStyle = TRACK;
      ctx.beginPath();
      ctx.arc(PW / 2, PH / 2, R, 0, Math.PI * 2);
      ctx.stroke();
      // the ring is a SECONDS hand here, not a remaining-time arc: it fills
      // over each minute and resets on the minute (frac = sec/60 on frame 0).
      var frac = t.clockSeconds / 60;
      if (frac > 0) {
        ctx.strokeStyle = t.accent;
        ctx.beginPath();
        ctx.arc(PW / 2, PH / 2, R, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2);
        ctx.stroke();
      }
      // Fit to the ring's inner diameter (702px full res -> 351 here),
      // capped at the countdown ring's 190px (95 here): "7:59" sits at the
      // familiar size, "19:59:50.000" shrinks to clear the track — mirrors
      // RING_INNER_FIT / RING_DIGITS_MAX in render/timer.py.
      var rw = clockCompositeWidth(ctx, f.base, f.millis, f.tag, 100);
      var rpx = rw > 0 ? Math.max(30, Math.min(95, Math.round(100 * 351 / rw))) : 95;
      drawClockComposite(ctx, f.base, f.millis, f.tag, PW / 2, PH / 2, rpx, TEXT_LIGHT, t.accent, t.backgrounds.length > 0);
    } else {
      // classic: auto-size the FULL string (incl. millis + tag) to fit
      // 1600px at full res (800 here), capped at 400 (200 here).
      var w = clockCompositeWidth(ctx, f.base, f.millis, f.tag, 100);
      var px = w > 0 ? Math.max(30, Math.min(200, Math.round(100 * 800 / w))) : 200;
      drawClockComposite(ctx, f.base, f.millis, f.tag, PW / 2, PH / 2, px, TEXT_LIGHT, t.accent, t.backgrounds.length > 0);
    }
  }

  function drawTimerPreview() {
    var canvas = $("timer-canvas");
    var ctx = canvas.getContext("2d");
    var t = readTimer();
    // Empty set -> paintBackground(), unchanged from before this feature.
    // The style's track + digits are painted on top either way (spec).
    if (t.beatOpener) {
      // Beat opener: the preview is a white beat -- a flat white screen, no
      // vignette, track or accent, digits in near-black. (The black beats
      // and the sound exist only in the render.) readTimer() already holds
      // style at classic and backgrounds empty, so the classic branches
      // below draw the digits exactly where the renderer puts them.
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, PW, PH);
    } else {
      drawTimerBackground(ctx, t);
    }

    if (t.mode === "clock") { drawClockTimerPreview(ctx, t); return; }

    // An invalid typed format used to fall back to the automatic layout
    // here, which read as "my format was dropped" to anyone looking up from
    // the box (UX review of the FORMAT box). Say so instead of pretending.
    if (t.formatError) {
      ctx.font = "600 22px " + FONT_LABEL;
      ctx.fillStyle = t.beatOpener ? "#111111" : TEXT_LIGHT;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("Fix the format to see the preview", PW / 2, PH / 2);
      return;
    }

    var total = Math.max(0, t.minutes * 60 + t.seconds);
    var text = formatClock(total, total, false, t.formatLayout);
    // renderer: accent digits whenever remaining <= 10s (first frame shown here)
    var digitColor = (t.warn && total > 0 && total <= 10) ? t.accent : TEXT_LIGHT;
    // The opener never uses the warn colour; its digits are (17, 17, 17).
    if (t.beatOpener) digitColor = "#111111";
    // Addendum (v1.23.0): first frame is always the full total, so millis
    // are always ".000" here — never derived from wall time (drawClockTimerPreview
    // above follows the same "frame 0" rule for clock mode).
    // A typed ".0"/".00" draws one/two zeros, like the renderer's run.
    var millis = t.showMillis ? millisRunText(t.formatLayout, t.msDigits) : "";
    // Full-size millis (spec: docs/specs/millis-reveal.md): 1.0 instead of
    // today's fixed 0.55, threaded into every clockCompositeWidth/
    // drawClockComposite call below so the preview's auto-fit shrinks the
    // main digits exactly the way the renderer's _millis_size/
    // _clock_font_size do. Countdown only -- drawClockTimerPreview() (clock
    // mode) never receives this and keeps its own untouched 0.55 default.
    var msScale = t.millisFullSize ? 1.0 : 0.55;

    if (t.style === "ring") {
      // render: centreline radius 400, thickness 26, digits 190px (all at 2x)
      var R = 200, thick = 13;
      ctx.lineWidth = thick;
      ctx.strokeStyle = TRACK;
      ctx.beginPath();
      ctx.arc(PW / 2, PH / 2, R, 0, Math.PI * 2);
      ctx.stroke();
      ctx.strokeStyle = t.accent;   // full arc at the first frame
      ctx.beginPath();
      ctx.arc(PW / 2, PH / 2, R, -Math.PI / 2, Math.PI * 1.5);
      ctx.stroke();
      if (millis) {
        // ".000" widens the string a lot; refit to clear the ring track —
        // mirrors RING_INNER_FIT/RING_DIGITS_MAX in render/timer.py at
        // preview scale (702/2=351, 190/2=95). Without millis this is
        // untouched: same fixed 95px drawClock() call as always.
        var rw = clockCompositeWidth(ctx, text, millis, "", 100, msScale);
        var rpx = rw > 0 ? Math.max(30, Math.min(95, Math.round(100 * 351 / rw))) : 95;
        drawClockComposite(
            ctx, text, millis, "", PW / 2, PH / 2, rpx, digitColor,
            t.accent, t.backgrounds.length > 0, msScale);
      } else {
        // A typed format can be far wider than "5:00"; fit it inside the
        // track, capped at the usual 95 -- mirrors the renderer's typed-
        // layout fit. Automatic keeps the fixed 95 it always had.
        var rpx2 = 95;
        if (t.formatLayout) {
          var rw2 = clockWidth(text, digitMetrics(ctx, 100));
          rpx2 = rw2 > 0 ? Math.max(30, Math.min(95, Math.round(100 * 351 / rw2))) : 95;
        }
        drawClock(ctx, text, PW / 2, PH / 2, digitMetrics(ctx, rpx2), digitColor, t.backgrounds.length > 0);
      }
    } else if (t.style === "bar") {
      // render: margin 140, top 944, height 16, digits 330px centred at y=500
      if (millis) {
        // fit to the bar's width — mirrors BAR_WIDTH/330 at preview scale
        // (1640/2=820, 330/2=165). Without millis: unchanged fixed 165px.
        var bw = clockCompositeWidth(ctx, text, millis, "", 100, msScale);
        var bpx = bw > 0 ? Math.max(30, Math.min(165, Math.round(100 * 820 / bw))) : 165;
        drawClockComposite(
            ctx, text, millis, "", PW / 2, 250, bpx, digitColor,
            t.accent, t.backgrounds.length > 0, msScale);
      } else {
        // Same typed-format fit as the ring above, to the bar's width.
        var bpx2 = 165;
        if (t.formatLayout) {
          var bw2 = clockWidth(text, digitMetrics(ctx, 100));
          bpx2 = bw2 > 0 ? Math.max(30, Math.min(165, Math.round(100 * 820 / bw2))) : 165;
        }
        drawClock(ctx, text, PW / 2, 250, digitMetrics(ctx, bpx2), digitColor, t.backgrounds.length > 0);
      }
      roundRectPath(ctx, 70, 472, PW - 140, 8, 4);
      ctx.fillStyle = TRACK;
      ctx.fill();
      roundRectPath(ctx, 70, 472, PW - 140, 8, 4);   // full at the first frame
      ctx.fillStyle = t.accent;
      ctx.fill();
    } else if (millis) {
      // classic + millis: auto-size the FULL string (incl. ".000") to fit
      // 1600px at 2x (800 here), capped at 400 (200 here) — same fit rule
      // as _clock_font_size(show_millis=True, has_tag=False) in timer.py.
      var w = clockCompositeWidth(ctx, text, millis, "", 100, msScale);
      var px = w > 0 ? Math.max(30, Math.min(200, Math.round(100 * 800 / w))) : 200;
      drawClockComposite(
          ctx, text, millis, "", PW / 2, PH / 2, px, digitColor,
          t.accent, t.backgrounds.length > 0, msScale);
    } else {
      // classic: auto-size to fit 1600px at 2x (800 here), capped at 200
      var ref = digitMetrics(ctx, 100);
      var w2 = clockWidth(text, ref);
      var px2 = w2 > 0 ? Math.max(30, Math.min(200, Math.round(100 * 800 / w2))) : 200;
      drawClock(ctx, text, PW / 2, PH / 2, digitMetrics(ctx, px2), digitColor, t.backgrounds.length > 0);
    }
  }

  // Rough estimate: the worker feeds a number of INPUT frames and chews
  // through roughly 30 of them a second on this class of machine. Clock
  // mode's frame rate rule (docs/specs/clock-mode.md "Frame rate"): millis
  // on -> 30fps input; millis off -> 1fps (classic) or 10fps (ring) input.
  function timerEstimateText(t) {
    var frames;
    if (t.mode === "clock") {
      var fps = t.showMillis ? 30 : (t.style === "ring" ? 10 : 1);
      frames = t.clockLength * fps;
    } else {
      var total = t.minutes * 60 + t.seconds;
      // Addendum (v1.23.0): millis on -> flat 30fps input, same as clock
      // mode's millis path (docs/specs/clock-mode.md addendum "Frame rate").
      // Smoother milliseconds (60 fps) (spec: docs/specs/millis-60fps.md):
      // countdown-only -- doubles that input fps to 60 when checked (and
      // not past the 15-minute cap, which applyTimerMode() already
      // enforces by unchecking the box), so this estimate reflects the
      // real ~2x render-time cost before the operator exports.
      // Beat opener: always 60 (spec), whatever the millis settings say.
      var fps2 = t.beatOpener ? 60
        : t.showMillis ? (t.millis60fps ? 60 : 30)
        : (t.style === "classic" ? 1 : (total <= 600 ? 10 : (total <= 1800 ? 4 : 2)));
      frames = (total + t.hold) * fps2;
    }
    var sec = Math.max(2, Math.round(frames / 30));
    var label;
    if (sec < 60) {
      label = sec + "s";
    } else {
      var mm = Math.floor(sec / 60), ss = sec % 60;
      label = ss ? mm + "m " + ss + "s" : mm + "m";
    }
    return "EST. RENDER ~" + label + " (rough)";
  }

  // Clock mode swaps the right-hand column of controls for a different set
  // (start time / clip length / format / display) and hides BAR, which makes
  // no sense for a clock (nothing depletes). Runs on every updateTimer()
  // call so it always reflects the live mode/checkbox state — same pattern
  // as the spinner's winner-row toggle in updateSpinner().
  function applyTimerMode() {
    var modeEl = document.querySelector('input[name="timer-mode"]:checked');
    var mode = modeEl ? modeEl.value : "countdown";
    var isClock = mode === "clock";

    $("timer-style-bar-opt").hidden = isClock;
    if (isClock && $("timer-style-bar").checked) $("timer-style-classic").checked = true;
    $("timer-style-pick").classList.toggle("is-two", isClock);

    // Beat opener (spec: docs/specs/beat-opener.md): the flashing screen has
    // no ring or bar, so RING and BAR are greyed out and a selected one
    // falls back to CLASSIC -- same fallback as BAR in clock mode above.
    // The group and its fields exist only in countdown mode.
    var beat = !isClock && $("timer-beat").checked;
    // Ticking the opener overrides the style and background; unticking
    // gives them back. Without this a volunteer who ticked it just to see
    // what it does lost their RING and their images silently (UX review).
    // Saved on the way in, before anything below forces CLASSIC and
    // applyTimerBg() unpresses the background buttons.
    if (beat && !beatSaved) {
      var styleNow = document.querySelector('input[name="timer-style"]:checked');
      beatSaved = { style: styleNow ? styleNow.id : "timer-style-classic",
                    bg: {} };
      BEAT_BG_BUTTONS.forEach(function (id) {
        beatSaved.bg[id] = $(id).getAttribute("aria-pressed");
      });
    } else if (!beat && beatSaved) {
      var saved = beatSaved;
      beatSaved = null;
      // Clock mode has no BAR; its own fallback above already chose.
      if (!(isClock && saved.style === "timer-style-bar")) {
        $(saved.style).checked = true;
      }
      BEAT_BG_BUTTONS.forEach(function (id) {
        $(id).setAttribute("aria-pressed", saved.bg[id]);
      });
    }
    $("timer-beat-group").hidden = isClock;
    $("timer-beat-fields").hidden = !beat;
    $("timer-beat-mine").hidden = !(beat && beatSoundChoice() === "mine");
    // Said next to each control the opener switches off, not only beside
    // the tick far below in ADVANCED -- nobody connected the two.
    $("timer-style-beat-hint").hidden = !beat;
    $("timer-accent-beat-hint").hidden = !beat;
    $("timer-accent-group").classList.toggle("is-off", beat);
    // Warn colour does nothing on black-on-white digits (rule 3).
    $("timer-warn").disabled = beat;
    $("timer-style-ring").disabled = beat;
    $("timer-style-bar").disabled = beat;
    if (beat && !$("timer-style-classic").checked) {
      $("timer-style-classic").checked = true;
    }

    $("timer-duration-group").hidden = isClock;
    $("timer-options-group").hidden = isClock;
    $("timer-clock-start-group").hidden = !isClock;
    $("timer-clock-length-group").hidden = !isClock;
    $("timer-clock-format-group").hidden = !isClock;
    $("timer-clock-display-group").hidden = !isClock;

    // Milliseconds need seconds — force it on and lock the box while millis
    // is checked (spec: "Ticking it also ticks/locks Show seconds"). Show
    // seconds is still clock-only, so this only matters in clock mode, but
    // it's harmless to keep the two boxes in sync regardless of mode.
    var millisOn = $("timer-show-millis").checked;
    if (millisOn) $("timer-clock-show-seconds").checked = true;
    $("timer-clock-show-seconds").disabled = millisOn;

    // Smoother milliseconds (60 fps) (spec: docs/specs/millis-60fps.md):
    // countdown-only -- hidden outright in clock mode (unlike its sibling
    // millis controls above, which stay visible-but-inert there) because
    // clock mode has no "total duration" for the 15-minute cap below to
    // mean anything against. Same hide mechanism as BAR above.
    // Also hidden while the beat opener is on: it is always 60 fps, so the
    // choice means nothing there.
    $("timer-millis-60fps-row").hidden = isClock || beat;
    $("timer-millis-60fps-hint").hidden = isClock || beat;
    if (!isClock) {
      // Mirrors MILLIS_MAX_SECONDS_60FPS in validation.py: 900s (15 min) --
      // a tighter cap than plain millis' 1800s because 60fps roughly
      // doubles the frame count for the same duration. `>`, not `>=`: a
      // 15:00 timer is exactly 900s and is the owner's real use case, so
      // it must stay enabled exactly at that boundary.
      var total60 = Math.max(0, toInt($("timer-minutes").value, 0) * 60 +
          toInt($("timer-seconds").value, 0));
      var over60fpsCap = total60 > 900;
      var box60fps = $("timer-millis-60fps");
      box60fps.disabled = over60fpsCap;
      box60fps.title = over60fpsCap
          ? "Needs 15 minutes or less. Shorten the timer to use " +
              "smoother milliseconds."
          : "";
      // A disabled control must never stay checked -- same auto-fallback
      // pattern as BAR->CLASSIC above -- or the payload would carry
      // millis_60fps: true into a render the server rejects.
      if (over60fpsCap && box60fps.checked) box60fps.checked = false;
    }

    return mode;
  }

  function updateTimer() {
    var mode = applyTimerMode();
    // Backgrounds group is visible (and validated) in BOTH modes, so this
    // runs unconditionally rather than inside either branch below.
    applyTimerBg(mode === "clock");
    var t = readTimer();
    // Hold-at-zero's seconds field (spec: docs/specs/millis-reveal.md):
    // visible in BOTH modes, same reasoning as Fixed format in the same
    // group -- only the countdown payload actually sends it. Its hint text
    // is only recomputed below in the countdown branch; in clock mode it
    // just keeps showing the neutral range description already in the HTML.
    // Shown only while it can matter: a leftover ticked Hold at zero with
    // milliseconds off otherwise left its seconds box on screen for a
    // feature that was switched off.
    // ...and only in countdown mode. The hint's TEXT is recomputed in the
    // countdown branch alone, so a red error typed there stayed on screen,
    // frozen and uneditable, after switching to clock mode -- where the
    // box is never sent and means nothing. Same guard the fps caption and
    // the render estimate already use.
    var revealLive = mode !== "clock" && t.showMillis && t.millisReveal;
    $("timer-millis-reveal-seconds-field").hidden = !revealLive;
    $("timer-millis-reveal-seconds-hint").hidden = !revealLive;
    // Full-size, hold-at-zero and 60 fps only change a render that SHOWS
    // milliseconds. With Show milliseconds off the renderer never leaves
    // its plain 15fps path whatever is ticked, so grey them out -- or a
    // volunteer making a quick timer can tick 60 fps and reasonably
    // wonder whether it slowed the export. Ticked state is kept, not
    // cleared, so turning milliseconds back on restores the choice.
    // applyTimerMode() above has already disabled 60 fps past 15
    // minutes: only ever ADD disabled here, never clear it, or that cap
    // would be silently undone.
    //
    // Typed format (spec: docs/specs/countdown-format.md): they now appear
    // only while they can matter -- countdown mode with a format that has
    // milliseconds -- instead of sitting greyed out. The FORMAT box is the
    // countdown's switch; Show milliseconds is the clock's.
    var isClockNow = mode === "clock";
    $("timer-format-wrap").hidden = isClockNow;
    $("timer-show-millis-row").hidden = !isClockNow;
    $("timer-show-millis-hint").hidden = !isClockNow;
    $("timer-millis-options").hidden = isClockNow || !t.showMillis;
    var millisOff = !t.showMillis;
    $("timer-millis-full-size").disabled = millisOff;
    $("timer-millis-reveal").disabled = millisOff;
    $("timer-millis-reveal-seconds").disabled = millisOff;
    if (millisOff) $("timer-millis-60fps").disabled = true;
    if (!isClockNow) {
      var fmtHint = $("timer-format-hint");
      var startTotal = Math.max(0, t.minutes * 60 + t.seconds);
      if (t.formatError) {
        fmtHint.textContent = t.formatError;
      } else {
        // Say what it will actually look like -- the operator typed a
        // shape, and the first frame is the proof it read the way they
        // meant. Recommend the millis form while the box is empty.
        var startText = formatClock(startTotal, startTotal, false,
                                    t.formatLayout) +
            (t.showMillis ? millisRunText(t.formatLayout, t.msDigits) : "");
        fmtHint.textContent = "Starts at " + startText + ". " +
            (t.formatLayout
                ? "H hours, M minutes, S seconds, " +
                  (t.showMillis ? millisRunText(t.formatLayout, 3) : ".000") +
                  " milliseconds."
                : "Leave empty for the usual layout, or type M:SS.000 " +
                  "for milliseconds.");
      }
      fmtHint.classList.toggle("is-bad", !!t.formatError);
      $("timer-format").setAttribute(
          "aria-invalid", t.formatError ? "true" : "false");
    }
    // Smoother milliseconds (60 fps) (spec: docs/specs/millis-60fps.md):
    // the "Show milliseconds" hint just above names a specific fps, so it
    // goes stale the instant that real number changes. Same three-way
    // condition as the live spec-line in applyTimerBg() above, so the two
    // labels never disagree with each other.
    $("timer-show-millis-hint").textContent =
        (mode !== "clock" && t.showMillis && t.millis60fps)
            ? "Renders at 60 fps — longer to export"
            : "Renders at 30 fps — longer to export";
    var bgErr = validateTimerBg();
    var bgHint = $("timer-bg-seconds-hint");
    bgHint.textContent = bgErr || "Each image holds this long, then the next one shows.";
    bgHint.classList.toggle("is-bad", !!bgErr);
    var err;

    if (mode === "clock") {
      var startErr = validateTimerClockStart();
      var lengthErr = validateTimerClockLength();
      err = startErr || lengthErr || bgErr;
      var startHint = $("timer-clock-start-hint");
      startHint.textContent = startErr || ("Shows as " + timerClockStartLabel(t));
      startHint.classList.toggle("is-bad", !!startErr);
      var lengthHint = $("timer-clock-length-hint");
      lengthHint.textContent = lengthErr || "5 seconds to 30 minutes";
      lengthHint.classList.toggle("is-bad", !!lengthErr);
    } else {
      var durationErr = validateTimerDuration();
      var holdErr = validateTimerHold();
      // Same rule as validateTimer(): the seconds box only counts while
      // milliseconds are shown AND holding at zero. This check exists
      // twice (here it drives the Export button; there, the submit),
      // and fixing only one left a plain timer greyed out.
      var revealSecondsErr = (t.showMillis && t.millisReveal)
          ? validateTimerMillisRevealSeconds() : null;
      var bpmErr = validateTimerBpm();
      var soundErr = validateTimerBeatSound();
      err = t.formatError || durationErr || bpmErr || soundErr || holdErr ||
          revealSecondsErr || bgErr;
      renderBeatSound();
      // The error replaces the hint under the field, like every other
      // field here, and the box is flagged for screen readers.
      var beatHint = $("timer-beat-hint");
      // With MY SOUND the note is whatever was recorded and KEY only
      // checks it, so the built-in tone's "the note is the key's root"
      // would tell the volunteer KEY changes their sound (UX review).
      beatHint.textContent = bpmErr || (beatSoundMineOn()
          ? "Use the song's BPM and key — ask the band. With your own " +
            "sound, KEY only checks it's in tune."
          : "Use the song's BPM and key — ask the band. The note is " +
            "the key's root, so major or minor doesn't matter.");
      beatHint.classList.toggle("is-bad", !!bpmErr);
      $("timer-bpm").setAttribute("aria-invalid", bpmErr ? "true" : "false");
      var hint = $("timer-duration-hint");
      hint.textContent = durationErr ||
          (t.beatOpener ? "5 seconds to 15 minutes with the beat opener"
          : t.showMillis ? "5 seconds to 30 minutes with milliseconds"
          : "5 seconds to 120 minutes");
      hint.classList.toggle("is-bad", !!durationErr);
      var holdHint = $("timer-hold-hint");
      // With the opener the hold is black and silent, not a 0:00 on
      // screen -- the hint must not promise digits that aren't there.
      holdHint.textContent = holdErr || (t.beatOpener
        ? "After 0:00 the screen stays black and silent this long. 0 to 30 seconds."
        : "After the countdown ends the video stays on 0:00 this long. 0 to 30 seconds.");
      holdHint.classList.toggle("is-bad", !!holdErr);
      $("timer-hold").setAttribute("aria-invalid", holdErr ? "true" : "false");

      // Degenerate case (spec: "Milliseconds tick for the whole timer... ") --
      // NOT an error, must never join `err` above: a volunteer whose timer
      // is shorter than the (default 60s) threshold must still export fine.
      var revealSecondsHint = $("timer-millis-reveal-seconds-hint");
      var total = Math.max(0, t.minutes * 60 + t.seconds);
      if (revealSecondsErr) {
        revealSecondsHint.textContent = revealSecondsErr;
      } else if (t.millisRevealSeconds >= total) {
        revealSecondsHint.textContent =
            "Milliseconds tick for the whole timer (it's only " +
            total + "s long).";
      } else {
        revealSecondsHint.textContent = "1 to 1800 seconds";
      }
      revealSecondsHint.classList.toggle("is-bad", !!revealSecondsErr);
      $("timer-millis-reveal-seconds").setAttribute(
          "aria-invalid", revealSecondsErr ? "true" : "false");
    }

    $("timer-export").disabled = exportBusy["timer"] || (!!err);
    $("timer-estimate").textContent = err ? "EST. RENDER — (rough)" : timerEstimateText(t);
    drawTimerPreview();
  }

  // Sends exactly the API contract in docs/specs/clock-mode.md: only clock
  // keys in clock mode, only countdown keys in countdown mode (which stays
  // byte-for-byte what it always sent — no "mode" key at all — so the
  // backend's untouched countdown path never has to guess).
  function timerPayload() {
    var t = readTimer();
    if (t.mode === "clock") {
      return {
        type: "timer",
        options: {
          mode: "clock",
          start: pad2(t.clockHours) + ":" + pad2(t.clockMinutes) + ":" + pad2(t.clockSeconds),
          duration_seconds: t.clockLength,
          format: t.clockFormat,
          show_seconds: t.showSeconds,
          show_millis: t.showMillis,
          style: t.style,
          accent: t.accent,
          // Backgrounds (spec: docs/specs/timer-backgrounds.md): all four
          // keys are valid in both modes.
          backgrounds: t.backgrounds,
          bg_seconds: t.bgSeconds,
          bg_dim: t.bgDim,
          bg_blur: t.bgBlur,
          // Green screen (spec: docs/specs/green-screen.md): valid in
          // both modes, like the rest of the Background group.
          green_screen: t.greenScreen,
          // Transparent (spec: docs/specs/alpha-export.md): alongside
          // green_screen in the same Background group, both modes.
          transparent: t.transparent
        }
      };
    }
    return {
      type: "timer",
      options: {
        minutes: t.minutes,
        seconds: t.seconds,
        style: t.style,
        accent: t.accent,
        warn_last10: t.warn,
        hold_seconds: t.hold,
        // Addendum (v1.23.0): accepted (and defaults false) in countdown
        // payloads too now — see _validate_countdown_options in app.py.
        show_millis: t.showMillis,
        // Typed format (spec: docs/specs/countdown-format.md): "" means
        // automatic. When set, the server derives show_millis from it
        // too, so the two can never disagree.
        display_format: t.displayFormat,
        // Full-size / Hold-at-zero millis (spec: docs/specs/millis-reveal.md):
        // countdown-only, like display_format just above -- never sent in
        // the clock branch above.
        millis_full_size: t.millisFullSize,
        millis_reveal: t.millisReveal,
        // Only the operator's value while it is in use; otherwise the
        // server's own default. Sending a stale empty box (null) made
        // validation.py reject a plain timer for the same reason as above.
        millis_reveal_seconds: (t.showMillis && t.millisReveal)
            ? t.millisRevealSeconds : 60,
        // Smoother milliseconds (60 fps) (spec: docs/specs/millis-60fps.md):
        // countdown-only like the millis keys above -- never sent in the
        // clock branch, which never reads it (spec: "Countdown only, not
        // clock mode").
        millis_60fps: t.millis60fps,
        // Beat opener (spec: docs/specs/beat-opener.md): countdown-only.
        // bpm and key always go, at the server's defaults while the box is
        // off, so a stale or half-typed BPM in the hidden field can never
        // reach validation and block a plain timer.
        beat_opener: t.beatOpener,
        bpm: t.beatOpener ? t.bpm : 120,
        key: t.beatOpener ? t.key : "A",
        // Your own sound: always "builtin" while the opener is off, so a
        // leftover "My sound" choice can never reach validation.
        beat_sound: t.beatOpener ? t.beatSound : "builtin",
        // Backgrounds (spec: docs/specs/timer-backgrounds.md).
        backgrounds: t.backgrounds,
        bg_seconds: t.bgSeconds,
        bg_dim: t.bgDim,
        bg_blur: t.bgBlur,
        // Green screen (spec: docs/specs/green-screen.md).
        green_screen: t.greenScreen,
        // Transparent (spec: docs/specs/alpha-export.md).
        transparent: t.transparent
      }
    };
  }


  // ---------------------------------------------------------------- wiring

  // timer form
  Array.prototype.forEach.call(
    document.querySelectorAll("#timer-presets .chip"),
    function (chip) {
      chip.addEventListener("click", function () {
        $("timer-minutes").value = chip.dataset.minutes;
        $("timer-seconds").value = "0";
        updateTimer();
      });
    }
  );
  Array.prototype.forEach.call(
    document.querySelectorAll("#timer-clock-presets .chip"),
    function (chip) {
      chip.addEventListener("click", function () {
        $("timer-clock-length").value = chip.dataset.seconds;
        updateTimer();
      });
    }
  );
  // Background images: "+ ADD IMAGE" opens a small inline picker (not a
  // modal) rather than a native file dialog directly, because it also
  // offers the already-stored library to reuse (spec).
  $("timer-bg-images").addEventListener("click", function () {
    // A toggle like its two neighbours (owner decision,
    // docs/user-flows.md): selected the instant it is clicked, and
    // clicking it again switches images off. They are kept, not deleted,
    // and come back on the next click. Adding a SECOND image is the "+"
    // at the end of the thumbnails.
    var on = this.getAttribute("aria-pressed") === "true";
    this.setAttribute("aria-pressed", on ? "false" : "true");
    if (on) {
      closeTimerBgPicker();
    } else {
      $("timer-bg-green").setAttribute("aria-pressed", "false");
      $("timer-bg-transparent").setAttribute("aria-pressed", "false");
      // Open the picker only when there is nothing to show yet. Turning
      // stored images back on used to reopen it every time, an extra
      // close for a volunteer who only wanted their images back.
      if (timerBg.ids.length === 0) openTimerBgPicker();
    }
    updateTimer();
  });
  $("timer-bg-picker-close").addEventListener("click", function () {
    closeTimerBgPicker();
    updateTimer();    // closing with nothing picked backs images out
  });
  $("timer-bg-upload").addEventListener("change", function () {
    // multiple= on the input: picking images one at a time in Windows
    // Explorer was the slowest part of setting a timer up.
    if (this.files && this.files.length) uploadTimerBgFiles(this.files);
  });
  wireTimerBgDrop();
  // Your own sound. Choosing a sound source clears a stale upload error
  // (it was about the last attempt, not this choice); the form-level change
  // listener then redraws.
  Array.prototype.forEach.call(
    document.querySelectorAll('input[name="timer-beat-sound"]'),
    function (el) {
      el.addEventListener("change", function () { beatSound.error = ""; });
    });
  $("timer-beat-sound-file").addEventListener("change", function () {
    var file = this.files && this.files[0];
    // Cleared so picking the same file again (after fixing it) still fires.
    var input = this;
    if (file) uploadBeatSound(file).then(function () { input.value = ""; });
  });
  $("timer-beat-sound-remove").addEventListener("click", removeBeatSound);
  refreshBeatSound();
  // Draw the strip once at boot. It is otherwise only drawn when an image
  // is added or removed, so on a fresh load the empty state had no "Drop
  // images here" tile in it — the one moment it is most needed.
  renderTimerBgStrip();
  // Green screen (spec: docs/specs/green-screen.md): a plain button click
  // fires no form input/change event, unlike every other timer-bg-*
  // control above (all real form fields), so this calls updateTimer()
  // itself rather than relying on the form-level "change" listener.
  $("timer-bg-green").addEventListener("click", function () {
    var pressed = this.getAttribute("aria-pressed") === "true";
    this.setAttribute("aria-pressed", pressed ? "false" : "true");
    // Transparent (spec: docs/specs/alpha-export.md): mutually exclusive
    // with green — turning green ON forces transparent OFF.
    if (!pressed) {
      $("timer-bg-transparent").setAttribute("aria-pressed", "false");
      $("timer-bg-images").setAttribute("aria-pressed", "false");
    }
    updateTimer();
  });
  // Transparent (spec: docs/specs/alpha-export.md): mirror image of the
  // green handler above, including the same "plain button click fires no
  // form event" reasoning.
  $("timer-bg-transparent").addEventListener("click", function () {
    var pressed = this.getAttribute("aria-pressed") === "true";
    this.setAttribute("aria-pressed", pressed ? "false" : "true");
    if (!pressed) {
      $("timer-bg-green").setAttribute("aria-pressed", "false");
      $("timer-bg-images").setAttribute("aria-pressed", "false");
    }
    updateTimer();
  });

  // Which encoder made the last export, under ADVANCED. On Windows the
  // encoder is nearly the whole render time (drawing a 5-minute
  // countdown takes 0.2 s of 6.2 s), so this is how the owner tells a
  // graphics card that is working from one that is being skipped. Plain
  // words first; the codec name and ffmpeg's own reason on a dim second
  // line, for whoever needs to search for it.
  var HW_ENCODERS = { h264_nvenc: true, h264_qsv: true, h264_amf: true };

  function showEncoderStat(filename, job) {
    var group = $("timer-encoder-group");
    if (!job || !job.encoder) {
      group.hidden = true;
      return;
    }
    var took = (job.seconds || job.seconds === 0)
      ? job.seconds + " seconds" : "";
    var notes = job.encoder_notes || [];
    var line;
    if (HW_ENCODERS[job.encoder]) {
      line = "Made on the graphics card" + (took ? ", in " + took : "") + ".";
    } else if (notes.length) {
      // Only said when graphics-card encoders were actually tried and
      // refused — a Mac never tries them, by design, so it gets no such
      // claim.
      line = "Made without the graphics card" +
        (took ? ", in " + took : "") +
        " — its encoder was not available on this computer.";
    } else {
      line = took ? "Took " + took + "." : "";
    }
    $("timer-encoder-stat").textContent = line;
    $("timer-encoder-tech").textContent = job.encoder +
      (notes.length ? " · " + notes.join("; ") : "");
    group.hidden = false;
  }

  var timerTile = {
    update: updateTimer, validate: validateTimer, payload: timerPayload,
    enter: updateTimer, leave: function () {}, done: showEncoderStat
  };
  SV.wireTileForm("timer", timerTile, { autoUpdate: true });
  SV.registerTile("timer", timerTile);

})(window.SV);
