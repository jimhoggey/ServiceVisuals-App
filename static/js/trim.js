/* Service Visuals — trim a downloaded clip (docs/specs/clip-trim.md).

   Lives inside the YouTube download tile's done panel. download.js shows
   the TRIM THIS CLIP button after a single MP4/MP3 download and calls
   SV.tiles.trim.open(filename) / .close(); everything else is here.

   Registered as its own "trim" kind so the shared export lifecycle in
   core.js (startExport -> poll -> trim-progress / trim-done / SHOW FILE,
   and the session list) works unchanged. Loaded after download.js. */

"use strict";

(function (SV) {

  var $ = SV.$;
  var exportBusy = SV.exportBusy;
  var showError = SV.showError;
  var hideError = SV.hideError;
  var startExport = SV.startExport;

  var MIN_CLIP = 0.5;
  var TIME_ERROR = "Type a time like 1:30.";

  var state = {
    filename: "",
    duration: null,     // seconds, once the player has read the file
    start: 0,
    end: 0,
    bad: {}             // field id -> true while it can't be read
  };
  var playingClip = false;

  // ------------------------------------------------------------ times ----

  // "90", "1:30", "1:02:30", "1:30.5" -> seconds; anything else -> null.
  // Typed by a volunteer, so spaces and a trailing dot are forgiven.
  function parseTime(text) {
    var t = String(text).replace(/\s+/g, "").replace(/\.$/, "");
    if (!t) return null;
    if (!/^\d+(:\d{1,2}){0,2}(\.\d+)?$/.test(t)) return null;
    var parts = t.split(":");
    var secs = 0;
    for (var i = 0; i < parts.length; i++) {
      var n = parseFloat(parts[i]);
      // 1:75 is a typo, not 2:15 — refuse it rather than guess.
      if (i > 0 && n >= 60) return null;
      secs = secs * 60 + n;
    }
    return secs;
  }

  // 1:30 / 1:02:30, with one decimal only when the time has one (a SET
  // HERE press lands between seconds, and 1:30 would hide that).
  function formatTime(secs) {
    secs = Math.max(0, secs);
    var tenths = Math.round(secs * 10);
    var whole = Math.floor(tenths / 10);
    var frac = tenths % 10;
    var h = Math.floor(whole / 3600);
    var m = Math.floor((whole % 3600) / 60);
    var s = whole % 60;
    var out = (h ? h + ":" + SV.pad2(m) : String(m)) + ":" + SV.pad2(s);
    return frac ? out + "." + frac : out;
  }

  function cap(secs) {
    return state.duration === null ? secs : Math.min(secs, state.duration);
  }

  // ------------------------------------------------------------- view ----

  function writeFields(except) {
    if (except !== "trim-start") $("trim-start").value = formatTime(state.start);
    if (except !== "trim-end") $("trim-end").value = formatTime(state.end);
  }

  function problem() {
    if (state.bad["trim-start"] || state.bad["trim-end"]) return TIME_ERROR;
    if (state.duration !== null && state.start >= state.duration) {
      return "The start is after the end of the video (it is " +
        formatTime(state.duration) + " long).";
    }
    if (state.end <= state.start) return "The end has to be after the start.";
    if (state.end - state.start < MIN_CLIP) {
      return "The clip has to be at least half a second long.";
    }
    return null;
  }

  // The panel opens on the whole video, and a bright SAVE there would
  // save a copy identical to the download (UX review): until something
  // is narrowed, say so and hold SAVE.
  function untouched() {
    return state.duration !== null && state.start < 0.05 &&
      state.end > state.duration - 0.05;
  }

  // A finished trim's SAVED panel read as describing the clip being set
  // up next. It goes as soon as an edit starts; the file stays reachable
  // from THIS SESSION below.
  function clearLastResult() {
    if (exportBusy["trim"]) return;
    $("trim-progress").hidden = true;
    $("trim-done").hidden = true;
    hideError("trim");
  }

  // A drag lands between seconds; "2:03.5" made volunteers wonder what
  // they had typed wrong. Typed times keep what was typed.
  function wholeSecond(secs) {
    return Math.round(secs);
  }

  function render() {
    ["trim-start", "trim-end"].forEach(function (id) {
      $(id).classList.toggle("is-bad", !!state.bad[id]);
    });
    var err = problem();
    var summary = $("trim-summary");
    // Only a problem or a nudge goes here. The clip itself is already
    // spelled out on the bar, and the owner asked for each time to
    // appear once (a summary sentence repeated all three).
    var text = "";
    if (err) {
      text = err;
    } else if (state.duration === null) {
      text = "Loading the video…";
    } else if (untouched()) {
      // The order matters: the length counts from the start handle, and
      // "one minute from 2:00" was hard to discover without saying so.
      text = "Nothing trimmed yet — drag the gold handles, or set the " +
        "start and press 1 MIN or 5 MIN.";
    }
    summary.textContent = text;
    summary.hidden = !text;
    summary.classList.toggle("is-bad", !!err);
    $("trim-export").disabled = !!err || !!exportBusy["trim"] ||
      !state.filename || untouched();
    $("trim-play").disabled = !!err || state.duration === null;
    drawTimeline();
  }

  function pct(secs) {
    if (!state.duration) return 0;
    return Math.max(0, Math.min(100, secs / state.duration * 100));
  }

  function drawTimeline() {
    var a = pct(state.start);
    var b = pct(Math.max(state.start, state.end));
    $("trim-keep").style.left = a + "%";
    $("trim-keep").style.width = (b - a) + "%";
    $("trim-handle-start").style.left = a + "%";
    $("trim-handle-end").style.left = b + "%";
    $("trim-playhead").style.left = pct($("trim-player").currentTime || 0) + "%";
    placeMarks(a, b);
  }

  // The two gold times sit under their handles, kept apart and inside the
  // bar so a short clip or one at the very end stays readable. The
  // video's own length (dim, far right) steps aside when the end time
  // reaches it rather than printing over it.
  var MARK_GAP = 8;

  function placeMarks(a, b) {
    var width = $("trim-scale").clientWidth;
    var startEl = $("trim-start");
    var endEl = $("trim-end");
    var sw = startEl.offsetWidth;
    var ew = endEl.offsetWidth;
    var sx = Math.max(0, Math.min(width - sw - ew - MARK_GAP,
                                  a / 100 * width - sw / 2));
    var ex = Math.max(sx + sw + MARK_GAP,
                      Math.min(width - ew, b / 100 * width - ew / 2));
    startEl.style.left = sx + "px";
    endEl.style.left = ex + "px";
    var total = $("trim-total");
    total.textContent = state.duration === null ? "" : formatTime(state.duration);
    total.hidden = ex + ew > width - total.offsetWidth - MARK_GAP;

    // The length inside the gold part when it fits; beside it when the
    // clip is short (UX review: a 30-second clip lost its only length
    // label exactly when the volunteer wanted to check it).
    var keepPx = (b - a) / 100 * width;
    var len = state.end - state.start;
    var label = state.duration === null || len <= 0 ? ""
      : formatTime(len) + " clip";
    var inside = keepPx > label.length * 9;
    $("trim-keep-label").textContent = inside ? label : "";
    var out = $("trim-keep-outside");
    out.textContent = inside ? "" : label;
    if (!inside && label) {
      var barW = $("trim-timeline").clientWidth;
      var room = out.offsetWidth + 14;
      if (b / 100 * barW + room <= barW) {
        out.style.left = "calc(" + b + "% + 10px)";
        out.style.right = "auto";
      } else {
        out.style.left = "auto";
        out.style.right = "calc(" + (100 - a) + "% + 10px)";
      }
    }

    // The preset that matches the clip lights up, so a press visibly
    // took, and a drag that changes the length visibly un-sets it.
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-trim-length]"), function (btn) {
        var want = parseFloat(btn.getAttribute("data-trim-length"));
        btn.classList.toggle("is-on", state.duration !== null &&
                             Math.abs(len - want) < 0.05);
      });
  }

  // ------------------------------------------------------------ edits ----

  // The rules (spec, behaviour table): typing the start keeps the clip's
  // length, the length buttons move the end, typing the end changes the
  // length. Every move is capped at the video's own end.
  function setStart(secs) {
    var length = state.end - state.start;
    state.start = Math.max(0, cap(secs));
    state.end = cap(state.start + Math.max(length, MIN_CLIP));
  }

  function setLength(secs) {
    state.end = cap(state.start + secs);
  }

  function setEnd(secs) {
    state.end = cap(Math.max(0, secs));
  }

  function onType(e) {
    var id = e.target.id;
    var secs = parseTime(e.target.value);
    clearLastResult();
    state.bad[id] = secs === null;
    if (secs !== null) {
      if (id === "trim-start") setStart(secs);
      else setEnd(secs);
      writeFields(id);
    }
    render();
  }

  // On leaving a field, show what it was read as (90 -> 1:30), so the
  // volunteer sees the app understood them.
  // A time that can't be read goes back to the last good one on leaving
  // the field: left as "1:75" under a bar still showing the old clip, it
  // looked as if the bar had stuck (UX review). The red message stays
  // while typing, which is when it helps.
  function onCommit(e) {
    state.bad[e.target.id] = false;
    writeFields();
    render();
  }

  // ---------------------------------------------------------- player ----

  function player() { return $("trim-player"); }

  function stopClip() { playingClip = false; }

  $("trim-player").addEventListener("loadedmetadata", function () {
    var d = player().duration;
    if (!isFinite(d) || d <= 0) return;
    var first = state.duration === null;
    state.duration = d;
    if (first) {
      // Opened on the whole video: the volunteer narrows it from there.
      state.start = 0;
      state.end = d;
      state.bad = {};
    } else {
      state.end = cap(state.end);
    }
    writeFields();
    render();
  });

  $("trim-player").addEventListener("error", function () {
    if (!state.filename) return;
    $("trim-summary").textContent =
      "This file won't play here, but you can still type the times.";
  });

  $("trim-player").addEventListener("timeupdate", function () {
    if (playingClip && player().currentTime >= state.end) {
      player().pause();
      player().currentTime = state.end;
      stopClip();
    }
    drawTimeline();
  });
  // Pressing the player's own pause/seek ends "play my clip" — otherwise a
  // later plain PLAY would stop again at END for no visible reason.
  $("trim-player").addEventListener("pause", stopClip);
  $("trim-player").addEventListener("seeking", function () {
    if (playingClip && Math.abs(player().currentTime - state.start) > 0.3) {
      stopClip();
    }
  });

  $("trim-play").addEventListener("click", function () {
    if (problem()) return;
    var p = player();
    p.currentTime = state.start;
    playingClip = true;
    var started = p.play();
    if (started && started.catch) started.catch(stopClip);
  });

  // (SET START HERE / SET END HERE were removed at the owner's request:
  // dragging a handle shows that frame in the player, which does the
  // same job with one control fewer.)

  Array.prototype.forEach.call(
    document.querySelectorAll("[data-trim-length]"), function (btn) {
      btn.addEventListener("click", function () {
        clearLastResult();
        setLength(parseFloat(btn.getAttribute("data-trim-length")));
        state.bad = {};
        writeFields();
        render();
      });
    });

  ["trim-start", "trim-end"].forEach(function (id) {
    var el = $(id);
    el.addEventListener("input", onType);
    el.addEventListener("change", onCommit);
    el.addEventListener("blur", onCommit);
    // These fields sit inside the download form: Enter would submit it,
    // and its button is DOWNLOAD ANOTHER, which wipes the finished panel
    // (trim included). Enter here just means "I'm done typing".
    el.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); onCommit(e); el.blur(); }
    });
  });

  // -------------------------------------------------------- timeline ----

  // Drag a handle to move that edge; press anywhere else to jump the
  // player there. Handles are picked by distance, not by hit-box, so a
  // short clip whose two handles overlap still lets you grab either.
  var dragging = null;

  function timeAt(clientX) {
    var r = $("trim-timeline").getBoundingClientRect();
    var f = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
    return f * (state.duration || 0);
  }

  $("trim-timeline").addEventListener("pointerdown", function (e) {
    if (!state.duration) return;
    var r = $("trim-timeline").getBoundingClientRect();
    var x = e.clientX - r.left;
    var sx = pct(state.start) / 100 * r.width;
    var ex = pct(state.end) / 100 * r.width;
    var ds = Math.abs(x - sx);
    var de = Math.abs(x - ex);
    if (Math.min(ds, de) <= 16) {
      // Equal distance (handles stacked): the side you pressed on wins.
      dragging = ds < de || (ds === de && x < sx) ? "start" : "end";
      $("trim-timeline").setPointerCapture(e.pointerId);
      e.preventDefault();
    } else {
      player().currentTime = timeAt(e.clientX);
      drawTimeline();
    }
  });

  $("trim-timeline").addEventListener("pointermove", function (e) {
    if (!dragging) return;
    clearLastResult();
    var t = wholeSecond(timeAt(e.clientX));
    if (dragging === "start") {
      // Dragging START moves only START here (unlike typing it): the
      // other handle is right there on screen, and pushing it along
      // would look like the bar was fighting you.
      state.start = Math.min(t, state.end - MIN_CLIP);
      state.start = Math.max(0, state.start);
    } else {
      state.end = Math.max(t, state.start + MIN_CLIP);
      state.end = cap(state.end);
    }
    state.bad = {};
    player().currentTime = dragging === "start" ? state.start : state.end;
    writeFields();
    render();
  });

  function endDrag() { dragging = null; }
  $("trim-timeline").addEventListener("pointerup", endDrag);
  $("trim-timeline").addEventListener("pointercancel", endDrag);

  // ----------------------------------------------------------- export ----

  function validate() {
    return problem() || (state.filename ? null : "Pick a downloaded file to trim.");
  }

  function payload() {
    return {
      type: "trim",
      options: {
        filename: state.filename,
        start: Math.round(state.start * 1000) / 1000,
        end: Math.round(state.end * 1000) / 1000
      }
    };
  }

  $("trim-export").addEventListener("click", function () {
    if (exportBusy["trim"]) return;
    var err = validate();
    if (err) { showError("trim", err); return; }
    player().pause();
    startExport("trim", payload());
  });

  $("trim-reveal").addEventListener("click", function () {
    SV.revealInFinder("trim");
  });

  function open(filename) {
    state.filename = filename;
    state.duration = null;
    state.start = 0;
    state.end = 0;
    state.bad = {};
    stopClip();
    hideError("trim");
    $("trim-progress").hidden = true;
    $("trim-done").hidden = true;
    var p = player();
    p.classList.toggle("is-audio", /\.mp3$/i.test(filename));
    p.src = "/api/download/media/" + encodeURIComponent(filename);
    p.load();
    $("trim-panel").hidden = false;
    $("download-trim-open").hidden = true;
    writeFields();
    render();
    $("trim-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // Never leave a hidden player talking: the panel can disappear under
  // the volunteer (DOWNLOAD ANOTHER, a new link, BACK).
  function close() {
    var p = player();
    stopClip();
    if (!p.paused) p.pause();
    if (p.getAttribute("src")) {
      p.removeAttribute("src");
      p.load();
    }
    $("trim-panel").hidden = true;
    state.filename = "";
  }

  SV.registerTile("trim", {
    update: render,
    validate: validate,
    payload: payload,
    enter: function () {},
    // Called for every tile on every view change: pause, but keep the
    // panel — the download view's own enter() decides whether it stays.
    leave: function () { if (!player().paused) player().pause(); },
    done: function () { $("trim-status").textContent = "SAVED"; },
    busyText: function () { return "TRIMMING…"; },
    open: open,
    close: close
  });

})(window.SV);
