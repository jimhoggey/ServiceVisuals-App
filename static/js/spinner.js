/* Service Visuals — spinner tile: wheel preview, test spin, AI fill panel,
   and export. Loaded after core.js. */

"use strict";

(function (SV) {

  var $ = SV.$, PW = SV.PW, PH = SV.PH;
  var PALETTE = SV.PALETTE, BG_BASE = SV.BG_BASE, TRACK = SV.TRACK;
  var TEXT_LIGHT = SV.TEXT_LIGHT, TEXT_DARK = SV.TEXT_DARK;
  var HUB_FILL = SV.HUB_FILL, CARD_FILL = SV.CARD_FILL, FONT_LABEL = SV.FONT_LABEL;
  var paintBackground = SV.paintBackground, roundRectPath = SV.roundRectPath;
  var intFrom = SV.intFrom, luminance = SV.luminance;
  var currentAccent = SV.currentAccent;
  var exportBusy = SV.exportBusy;

  // =========================================================== SPINNER =====

  // zoomE: how far the Cinematic-zoom camera has pushed in, 0 (whole wheel)
  // to 1 (close-up). Already eased; only TEST SPIN moves it, and it holds at
  // 1 after landing like the render does. flick / dim / cardA are the other
  // zoom-only animated bits (the pointer's tick, the losers darkening, the
  // card fading in); prevRot/prevT/passedAt feed the flick.
  var spin = {
    rotDeg: 0, animating: false, winner: -1, raf: 0, zoomE: 0,
    flick: 0, dim: 0, cardA: 1, prevRot: 0, prevT: 0, passedAt: null,
    target: -1
  };
  // The wheel's entry limit (render/spinner.py MAX_ENTRIES; 300 for a
  // youth conference's 300 names) and the count above which slices carry
  // no names (LABELS_MAX). One place for each, instead of a bare 100 in
  // six spots.
  var WHEEL_MAX = 300;
  var WHEEL_LABELS_MAX = 100;
  // Cinematic zoom: mirrors _ZoomRenderer and the ZOOM_* constants in
  // render/spinner.py. The render works at 1080p and this canvas is half
  // that, so every pixel constant there is halved here (120 -> 60, 80 -> 40,
  // 500 -> 250, 24 -> 12, 9..14 -> 4.5..7, 16 -> 8); ratios are unchanged.
  var ZOOM_NAMES_IN_SHOT = 10;
  var ZOOM_MIN = 1.6, ZOOM_MAX = 24;
  var ZOOM_RIM_SCREEN_Y = 270;    // the rim's top ends in the middle (PH/2)
  var ZOOM_POINTER_MAX = 6.5;     // the arrow fills the top half
  var ZOOM_TIP_INTO_RIM = 10;     // its tip ends this far into the rim
  var ZOOM_FADE_LO = 4.5, ZOOM_FADE_HI = 7;
  var ZOOM_NAME_END_PX = 40, ZOOM_NAME_RUN_PX = 220, ZOOM_RIM_PAD_PX = 12;
  var ZOOM_DIM = 0.62;
  var ZOOM_CARD_TOP = 20;         // zoom: the card sits above the arrow
  var ZOOM_PUSH = 0.45;           // of the ZOOM phase is the push-in
  var CRAWL_NAMES_PER_S = 5;
  var TICK_DEG = 14;
  var CARD_DELAY = 0.2, CARD_FADE = 0.4;

  function readEntries() {
    return $("spinner-entries").value.split("\n")
      .map(function (s) { return s.trim(); })
      .filter(function (s) { return s.length > 0; });
  }

  function spinnerMode() {
    var el = document.querySelector('input[name="spinner-mode"]:checked');
    return el ? el.value : "random";
  }

  function readSpinnerTiming() {
    return {
      wait: intFrom($("spinner-wait")),
      spin: intFrom($("spinner-spin")),
      winner: intFrom($("spinner-winner-secs")),
      zoom: intFrom($("spinner-zoom-secs"))
    };
  }

  function validateSpinner() {
    var entries = readEntries();
    if (entries.length < 2) return "The wheel needs at least 2 non-empty entries.";
    if (entries.length > WHEEL_MAX) return "The wheel supports at most " + WHEEL_MAX + " entries — you have " + entries.length + ".";
    var tooLong = entries.filter(function (e) { return e.length > 40; });
    if (tooLong.length) return 'Each entry must be 40 characters or fewer — "' + tooLong[0].slice(0, 20) + '…" is too long.';
    if (spinnerMode() === "choose" && !$("spinner-winner").value) return "Pick a winner from the list.";
    var t = readSpinnerTiming();
    if (t.wait === null || t.wait < 0 || t.wait > 60) return "Wait must be a whole number from 0 to 60 seconds.";
    if (t.spin === null || t.spin < 2 || t.spin > 30) return "Spin must be a whole number from 2 to 30 seconds.";
    if (t.winner === null || t.winner < 1 || t.winner > 30) return "Winner must be a whole number from 1 to 30 seconds.";
    // Only checked while Cinematic zoom is on: a hidden field keeps its
    // value but must not block an export that doesn't use it.
    if ($("spinner-zoom").checked && (t.zoom === null || t.zoom < 2 || t.zoom > 30)) return "Zoom length must be a whole number between 2 and 30.";
    return null;
  }

  // Port of segment_colors() in spinner.py: adjacent segments (including the
  // last/first wrap-around pair) never share a colour.
  function segmentColors(n) {
    var m = PALETTE.length;
    var idxs = [];
    for (var i = 0; i < n; i++) {
      var base = (i + Math.floor(i / m) * 3) % m;
      var prev = idxs.length ? idxs[idxs.length - 1] : null;
      var first = idxs.length ? idxs[0] : null;
      var pick = base;
      for (var step = 0; step < m; step++) {
        var cand = (base + step) % m;
        if (cand === prev) continue;
        if (i === n - 1 && cand === first) continue;
        pick = cand;
        break;
      }
      idxs.push(pick);
    }
    return idxs.map(function (k) { return PALETTE[k]; });
  }

  function fitLabelText(ctx, text, maxW) {
    if (ctx.measureText(text).width <= maxW) return text;
    var t = text;
    while (t.length > 1 && ctx.measureText(t + "…").width > maxW) t = t.slice(0, -1);
    return t + "…";
  }

  function drawSpinnerPreview() {
    var canvas = $("spinner-canvas");
    var ctx = canvas.getContext("2d");
    paintBackground(ctx);

    var entries = readEntries();
    var cx = PW / 2, cy = PH / 2;
    var R = 215;                       // render: WHEEL_R 430 at 2x
    var hubR = 45;                     // render: HUB_R 90

    if (entries.length < 2) {
      ctx.setLineDash([10, 10]);
      ctx.lineWidth = 3;
      ctx.strokeStyle = TRACK;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = "600 22px " + FONT_LABEL;
      ctx.fillStyle = "#8b8e94";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("ADD AT LEAST 2 ENTRIES", cx, cy);
      return;
    }

    var n = entries.length;
    var segDeg = 360 / n;
    var segRad = Math.PI * 2 / n;
    var colors = segmentColors(n);
    var accent = currentAccent("spinner");

    // The renderer rotates the wheel CCW by rotDeg; segment 0 starts at the
    // 12 o'clock pointer when rotDeg = 0.
    var rot = -spin.rotDeg * Math.PI / 180;

    // ONE font size shared by every label (the size that fits the longest
    // entry): per-label sizing made an odd one out that could telegraph a
    // chosen winner. Mirrors _common_label_size() in render/spinner.py.
    // Sized up front because Cinematic zoom needs it to pick its Z_max.
    var zoomOn = $("spinner-zoom").checked;
    var maxLabelW = R - hubR - 30;
    var band = 2 * 0.62 * R * Math.sin(segRad / 2);
    var px = Math.max(10, Math.min(23, Math.floor(band * 0.5)));
    var i, a0;
    ctx.save();
    ctx.font = "600 " + px + "px " + FONT_LABEL;
    for (i = 0; i < n; i++) {
      while (px > 10 && ctx.measureText(entries[i]).width > maxLabelW) {
        px -= 1;
        ctx.font = "600 " + px + "px " + FONT_LABEL;
      }
    }
    ctx.restore();

    // Cinematic zoom, matching _ZoomRenderer in render/spinner.py.
    // Cinematic zoom, matching _ZoomRenderer in render/spinner.py. u is the
    // eased push-in (0..1); Z = zMax^u is an even dolly and everything else
    // (names, arrow) moves with u. The camera pushes straight in on ONE
    // world point, the top of the rim (cx, cy-R), which stays under the
    // arrow while it glides down from where it sits at 1x to the middle of
    // the shot. At u = 0 that is the identity camera: the plain 1x wheel.
    // labelPx is the font size in world units; on screen it is labelPx * Z.
    var labelPx = px, Z = 1, u = 0, fx = cx, fy = cy, sx = cx, sy = cy;
    var zs = null, ps = 1, tipIn = 17;
    if (zoomOn) {
      zs = zoomSizes(ctx, entries, px, R, hubR);
      u = spin.zoomE;
      Z = Math.pow(zs.zMax, u);
      fy = cy - R;
      sy = fy + u * (ZOOM_RIM_SCREEN_Y - fy);
      labelPx = zs.base + u * (zs.endPx / zs.zMax - zs.base);
      // The arrow grows to fill the top half; its tip (17 into the rim in
      // this canvas's pointer shape) ends 10 in.
      ps = 1 + u * (ZOOM_POINTER_MAX - 1);
      tipIn = 17 + u * (ZOOM_TIP_INTO_RIM - 17);
    }

    ctx.save();
    if (zoomOn) {
      ctx.translate(sx, sy);
      ctx.scale(Z, Z);
      ctx.translate(-fx, -fy);
    }
    ctx.translate(cx, cy);
    ctx.rotate(rot);

    for (i = 0; i < n; i++) {
      a0 = i * segRad - Math.PI / 2;
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.arc(0, 0, R, a0, a0 + segRad);
      ctx.closePath();
      ctx.fillStyle = colors[i];
      ctx.fill();
    }
    // 4px (2px here) gaps between segments — background shows through.
    // Zoomed they stay 2 px on SCREEN (like the render), so divide out the
    // camera scale: the slices widen as it closes in instead of the gaps
    // swallowing them.
    ctx.strokeStyle = BG_BASE;
    ctx.lineWidth = 2 / Z;
    for (i = 0; i < n; i++) {
      a0 = i * segRad - Math.PI / 2;
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(R * Math.cos(a0), R * Math.sin(a0));
      ctx.stroke();
    }

    // labels at 0.62R along each mid-angle, reading along the radius;
    // left-half labels flipped so nothing starts life upside down.
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    // Mirrors render/spinner.py's LABELS_MAX: past 100 entries the slices
    // are too thin to read, so the wheel is colour only (the winner card
    // still names who won). Cinematic zoom lifts that limit, but past 100
    // a name is only drawn once it is big enough on screen to read --
    // fading in between 4.5 and 7 px (9 and 14 at 1080p) -- so a 300-name
    // wheel at 1x is still colour only, not static. One alpha per frame,
    // because every label is the same size; at zero we skip the loop.
    var fade = 1;
    if (zoomOn && n > WHEEL_LABELS_MAX) {
      fade = Math.max(0, Math.min(1,
        (labelPx * Z - ZOOM_FADE_LO) / (ZOOM_FADE_HI - ZOOM_FADE_LO)));
    }
    var labelCount = zoomOn ? (fade > 0 ? n : 0)
                            : (n > WHEEL_LABELS_MAX ? 0 : n);
    if (zoomOn) ctx.globalAlpha = fade;
    // Zoomed, the room for a name runs from the 1x wheel's to run_end by u
    // (screen px, so /Z for world units); the label radius slides from
    // 0.62R out to hang below the arrow's tip (tipIn into the rim).
    var room = (R - hubR - 30) * Z;
    var labelW = zoomOn ? (room + u * (zs.runEnd - room)) / Z : maxLabelW;
    for (i = 0; i < labelCount; i++) {
      var mid = (i + 0.5) * segRad - Math.PI / 2;
      ctx.font = "600 " + labelPx + "px " + FONT_LABEL;
      var lr = 0.62 * R;
      var label;
      if (zoomOn) {
        // Only draw labels near the canvas: at 300 entries nearly all are
        // off-screen once zoomed, and measuring/painting 300 names per
        // frame is wasted work. The cull uses an estimated run, and a
        // margin of the longest possible run so nothing pops at the edge.
        var wa = rot + mid;
        var est = Math.min(labelPx * 0.6 * entries[i].length, labelW);
        var rimR = R - (tipIn + ZOOM_RIM_PAD_PX + est * Z / 2) / Z;
        lr = 0.62 * R + u * (rimR - 0.62 * R);
        var lsx = sx + Z * (cx + lr * Math.cos(wa) - fx);
        var lsy = sy + Z * (cy + lr * Math.sin(wa) - fy);
        var marg = labelW * Z;
        if (lsx < -marg || lsx > PW + marg || lsy < -marg || lsy > PH + marg) continue;
        label = fitLabelText(ctx, entries[i], labelW);
        // Exact centre now the real run is known (hang from the rim).
        rimR = R - (tipIn + ZOOM_RIM_PAD_PX + ctx.measureText(label).width * Z / 2) / Z;
        lr = 0.62 * R + u * (rimR - 0.62 * R);
      } else {
        label = fitLabelText(ctx, entries[i], maxLabelW);
      }
      ctx.save();
      ctx.rotate(mid);
      ctx.translate(lr, 0);
      if (Math.cos(mid) < 0) ctx.rotate(Math.PI);   // keep left half upright
      ctx.fillStyle = luminance(colors[i]) > 0.55 ? TEXT_DARK : TEXT_LIGHT;
      ctx.fillText(label, 0, 0);
      ctx.restore();
    }
    // The reveal (zoom only): everything but the winner's slice sinks into
    // shadow. Drawn over the whole frame, wheel and background alike, as in
    // the render; the hub and pointer go on top afterwards. Even-odd fill: a
    // huge rect (world units, covers the shot at any Z) minus the wedge.
    if (zoomOn && spin.dim > 0 && spin.target >= 0 && spin.target < n) {
      ctx.globalAlpha = 1;
      var wa0 = spin.target * segRad - Math.PI / 2;
      ctx.beginPath();
      ctx.rect(-6000, -6000, 12000, 12000);
      ctx.moveTo(0, 0);
      ctx.arc(0, 0, R, wa0, wa0 + segRad);
      ctx.closePath();
      ctx.fillStyle = "rgba(0,0,0," + (ZOOM_DIM * spin.dim) + ")";
      ctx.fill("evenodd");
    }
    ctx.restore();

    // hub: HUB_FILL disc with an accent ring (it zooms with the wheel)
    ctx.save();
    if (zoomOn) {
      ctx.translate(sx, sy);
      ctx.scale(Z, Z);
      ctx.translate(-fx, -fy);
    }
    ctx.beginPath();
    ctx.arc(cx, cy, hubR, 0, Math.PI * 2);
    ctx.fillStyle = HUB_FILL;
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.strokeStyle = accent;
    ctx.stroke();
    ctx.restore();

    // fixed pointer at 12 o'clock: light triangle, subtle dark outline.
    // Zoomed, it grows with the push-in (ps, to 6.5x: "the big triangle",
    // filling the top half) and is placed by its TIP: the tip sits tipIn
    // below the rim's screen position sy, so the shape's top (and its flick
    // pivot) rise above it as it grows. No top clamp is needed -- the rim
    // ends mid-screen. It flicks about the middle of its top edge as each
    // name passes (spin.flick). Drawn relative to the rim point so one set
    // of numbers serves both.
    ctx.save();
    ctx.translate(cx, cy - R);
    if (zoomOn) {
      // The shape's tip is 17 below its origin (the rim point at 1x).
      ctx.translate(sx - cx, sy + tipIn - 17 * ps - (cy - R));
      ctx.scale(ps, ps);
      if (spin.flick) {
        // Canvas rotate is clockwise; the render's PIL rotate(-TICK) is
        // the same direction (the tip swings left).
        ctx.translate(0, -9);
        ctx.rotate(TICK_DEG * spin.flick * Math.PI / 180);
        ctx.translate(0, 9);
      }
    }
    ctx.beginPath();
    ctx.moveTo(-16, -9);
    ctx.lineTo(16, -9);
    ctx.lineTo(0, 17);
    ctx.closePath();
    ctx.fillStyle = "rgba(10,11,13,0.86)";
    ctx.lineWidth = 5;
    ctx.strokeStyle = "rgba(10,11,13,0.86)";
    ctx.lineJoin = "round";
    ctx.stroke();
    ctx.fillStyle = TEXT_LIGHT;
    ctx.fill();
    ctx.restore();
    // This used to be set on the shared context and left that way; the
    // save/restore above would undo it, so keep it for the card's outline.
    ctx.lineJoin = "round";

    // winner card after the test spin lands (render: card centred at y=880)
    // (Zoom on: it fades in over the reveal while the test is still
    // "animating", so cardA rather than the animating flag gates it.)
    if (spin.winner >= 0 && spin.winner < n && spin.cardA > 0 &&
        (!spin.animating || spin.cardA < 1)) {
      ctx.save();
      ctx.globalAlpha = spin.cardA;
      // Zoom: the card goes at the TOP, above the big arrow, clear of the
      // names hanging down the bottom half.
      drawWinnerCard(ctx, entries[spin.winner], accent,
        zoomOn ? ZOOM_CARD_TOP : undefined);
      ctx.restore();
    }
  }

  // Cinematic zoom's name sizes, cached on the entries: the longest-name
  // fit measures every entry, which is wasteful at 60 fps. Mirrors
  // zoom_max() and _ZoomRenderer.__init__ in render/spinner.py, halved.
  var zoomSizeCache = { key: "", val: null };
  function zoomSizes(ctx, entries, px, R, hubR) {
    var n = entries.length;
    var key = px + "|" + entries.join("\n");
    if (zoomSizeCache.key === key) return zoomSizeCache.val;
    // About ten slices across the width at the rim.
    var zMax = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX,
      PW * n / (ZOOM_NAMES_IN_SHOT * 2 * Math.PI * R)));
    // 1x size: the wheel's own up to 100; beyond, 0.7 of a slice's arc at
    // the label radius, no floor (too small to read until the camera is in).
    var base = n > WHEEL_LABELS_MAX
      ? Math.max(0.25, 0.7 * 2 * Math.PI * 0.62 * R / n) : px;
    // Close-up size: big but within 45% of its slice, never below the
    // wheel's own names zoomed.
    var slice = 2 * Math.PI * R * zMax / n;
    var endPx = Math.max(Math.min(ZOOM_NAME_END_PX, 0.45 * slice), base * zMax);
    var runEnd = Math.min(ZOOM_NAME_RUN_PX, (R - hubR - 30) * zMax);
    // One size for every name, fitted to the longest (0.96: whole-pixel
    // fonts must not land a hair over).
    ctx.save();
    ctx.font = "600 " + endPx + "px " + FONT_LABEL;
    var longest = 0, i;
    for (i = 0; i < n; i++) longest = Math.max(longest, ctx.measureText(entries[i]).width);
    ctx.restore();
    if (longest > runEnd) {
      endPx = Math.max(Math.min(endPx, 20), 0.96 * endPx * runEnd / longest);
    }
    zoomSizeCache.key = key;
    zoomSizeCache.val = { zMax: zMax, base: base, endPx: endPx, runEnd: runEnd };
    return zoomSizeCache.val;
  }

  // The pointer's deflection, 0..1: pushed as a slice boundary comes up
  // under it, a damped snap back once it slips past; still while the wheel
  // is a blur. Port of pointer_flick() in render/spinner.py. pos is the
  // wheel position in slices, speed slices per 30 fps frame, sincePass
  // seconds since the last boundary (null before any).
  function pointerFlick(pos, speed, sincePass) {
    var gain = Math.max(0, Math.min(1, (0.4 - speed) / 0.25));
    if (gain <= 0) return 0;
    var frac = pos - Math.floor(pos);
    var push = Math.max(0, (frac - 0.7) / 0.3);
    var snap = 0;
    if (sincePass !== null) {
      snap = Math.exp(-sincePass / 0.07) * Math.cos(2 * Math.PI * sincePass / 0.22);
    }
    return gain * (push > Math.abs(snap) ? push : snap);
  }

  // topY: top edge of the card; omitted, it is centred at y=440 as always.
  function drawWinnerCard(ctx, name, accent, topY) {
    var namePx = 32;
    ctx.font = "600 " + namePx + "px " + FONT_LABEL;
    while (namePx > 12 && ctx.measureText(name).width > 600) {
      namePx -= 1;
      ctx.font = "600 " + namePx + "px " + FONT_LABEL;
    }
    var nameW = ctx.measureText(name).width;
    var capText = "WINNER";
    var capPx = 13, capTrack = 4;
    ctx.font = "500 " + capPx + "px " + FONT_LABEL;
    var capW = 0;
    capText.split("").forEach(function (ch) { capW += ctx.measureText(ch).width + capTrack; });
    capW -= capTrack;

    var padX = 32;
    var w = Math.max(210, Math.max(nameW, capW) + 2 * padX);
    var h = 15 + capPx + 6 + namePx + 17;
    var x = PW / 2 - w / 2;
    var y = topY === undefined ? 440 - h / 2 : topY;

    roundRectPath(ctx, x, y, w, h, 9);
    ctx.fillStyle = "rgba(20,22,25,0.95)";   // CARD_FILL at ~95%
    ctx.fill();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = accent;
    ctx.stroke();

    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    ctx.font = "500 " + capPx + "px " + FONT_LABEL;
    ctx.fillStyle = accent;
    var capX = PW / 2 - capW / 2;
    var capY = y + 15 + capPx;
    capText.split("").forEach(function (ch) {
      ctx.fillText(ch, capX, capY);
      capX += ctx.measureText(ch).width + capTrack;
    });

    ctx.textAlign = "center";
    ctx.font = "600 " + namePx + "px " + FONT_LABEL;
    ctx.fillStyle = TEXT_LIGHT;
    ctx.fillText(name, PW / 2, capY + 6 + namePx);
    ctx.textAlign = "left";
  }

  // Test spin: same motion profile as the renderer — 0.8s wind-up to -25deg,
  // then a cubic ease-out to 5 full CCW revolutions plus the landing angle
  // (jitter keeps it inside the central 70% of the winning segment).
  // Revolutions scale with the spin length (5 revs per default 7s, min 2) so
  // the wheel feels the same speed whatever the timing — mirrors spinner.py.
  var WINDUP_LEN = 0.8, WINDUP_DEG = -25, SPINS_PER_SEC = 5 / 7;

  function easeInOutQuad(u) {
    return u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
  }
  function easeOutCubic(u) {
    return 1 - Math.pow(1 - u, 3);
  }
  // Test spin honours the configured SPIN length (wait/winner phases are
  // render-only; a test spin should start immediately).
  function rotationAt(t, finalRotation, spinEnd) {
    if (t <= WINDUP_LEN) return WINDUP_DEG * easeInOutQuad(t / WINDUP_LEN);
    if (t < spinEnd) {
      var u = (t - WINDUP_LEN) / (spinEnd - WINDUP_LEN);
      return WINDUP_DEG + (finalRotation - WINDUP_DEG) * easeOutCubic(u);
    }
    return finalRotation;
  }

  // Cinematic zoom adds a ZOOM phase after the spin. Ports of zoom_travel,
  // zoom_rotation_at and zoom_full_spins in render/spinner.py. The slices
  // the wheel passes in the ZOOM phase: its rotation there eases out
  // cubically, so this is chosen to cross names at CRAWL_NAMES_PER_S when
  // the push-in ends.
  function zoomTravel(zoomS) {
    return CRAWL_NAMES_PER_S * zoomS / (3 * Math.pow(1 - ZOOM_PUSH, 2));
  }
  // The wind-up as always, then the SPIN seconds decelerating evenly into
  // the ZOOM seconds, which ease out over the last zoomTravel() slices to
  // stop on the winner. Speed is continuous where they meet (no lurch).
  function zoomRotationAt(t, finalRot, spinEnd, land, seg) {
    if (t <= WINDUP_LEN) return WINDUP_DEG * easeInOutQuad(t / WINDUP_LEN);
    if (t >= land) return finalRot;
    var zoomS = land - spinEnd;
    var dB = zoomTravel(zoomS) * seg;
    if (t >= spinEnd) {
      return finalRot - dB * Math.pow(1 - (t - spinEnd) / zoomS, 3);
    }
    var spinS = spinEnd - WINDUP_LEN;
    var dA = finalRot - dB - WINDUP_DEG;
    var v1 = 3 * dB / zoomS;                 // deg/s into the ZOOM phase
    var v0 = 2 * dA / spinS - v1;
    var s = t - WINDUP_LEN;
    return WINDUP_DEG + v0 * s + (v1 - v0) * s * s / (2 * spinS);
  }
  // Enough whole turns that the SPIN phase still slows into the ZOOM phase.
  function zoomFullSpins(fullSpins, n, spinS, zoomS) {
    var v1 = 3 * zoomTravel(zoomS) * (360 / n) / zoomS;
    while (fullSpins * 360 < 1.5 * v1 * spinS) fullSpins += 1;
    return fullSpins;
  }

  function cancelTestSpin() {
    if (spin.raf) cancelAnimationFrame(spin.raf);
    spin.raf = 0;
    spin.animating = false;
    spin.rotDeg = 0;
    spin.winner = -1;
    spin.target = -1;
    spin.zoomE = 0;
    spin.flick = 0;
    spin.dim = 0;
    spin.cardA = 1;
  }

  // Cinematic zoom progress: nothing until the spin ends, then an in-out
  // ease to the close-up over the first 45% of the ZOOM phase, then held
  // while the last names crawl past. Matches zoom_window() in the render.
  function zoomProgress(t, spinEnd, zoomS) {
    var end = spinEnd + ZOOM_PUSH * zoomS;
    if (t <= spinEnd) return 0;
    if (t >= end) return 1;
    return easeInOutQuad((t - spinEnd) / (end - spinEnd));
  }

  function testSpin() {
    var entries = readEntries();
    if (spin.animating || entries.length < 2 || entries.length > WHEEL_MAX) return;
    var n = entries.length;
    var segDeg = 360 / n;
    var timing = readSpinnerTiming();
    var spinEnd = WINDUP_LEN + (timing.spin === null ? 7 : timing.spin);

    var winnerIndex;
    if (spinnerMode() === "choose") {
      winnerIndex = entries.indexOf($("spinner-winner").value);
      if (winnerIndex < 0) winnerIndex = 0;
    } else {
      winnerIndex = Math.floor(Math.random() * n);
    }
    var frac = 0.15 + 0.7 * Math.random();     // never a segment boundary
    var fullSpins = Math.max(2, Math.round(SPINS_PER_SEC * (spinEnd - WINDUP_LEN)));
    var zoom = $("spinner-zoom").checked;
    var zoomS = (zoom && timing.zoom !== null) ? timing.zoom : 6;
    if (zoom) fullSpins = zoomFullSpins(fullSpins, n, spinEnd - WINDUP_LEN, zoomS);
    var finalRotation = fullSpins * 360 + (winnerIndex + frac) * segDeg;

    // Zoom: the wheel lands at the end of the ZOOM phase, then keeps
    // running through the reveal: card_in (0.2 s after landing) then 0.4 s
    // of darkening the losers while the card fades in.
    var land = zoom ? spinEnd + zoomS : spinEnd;
    var runEnd = zoom ? land + CARD_DELAY + CARD_FADE : spinEnd;
    var seg = 360 / n;

    spin.animating = true;
    spin.winner = -1;
    spin.target = winnerIndex;
    spin.rotDeg = 0;
    spin.zoomE = 0;
    spin.flick = 0;
    spin.dim = 0;
    spin.cardA = 1;
    spin.prevRot = 0;
    spin.prevT = 0;
    spin.passedAt = null;
    $("spinner-test").disabled = true;
    var t0 = performance.now();

    var frame = function (now) {
      var t = (now - t0) / 1000;
      spin.rotDeg = zoom ? zoomRotationAt(t, finalRotation, spinEnd, land, seg)
                         : rotationAt(t, finalRotation, spinEnd);
      if (zoom) {
        spin.zoomE = zoomProgress(t, spinEnd, zoomS);
        // The pointer's flick as each name passes. The render steps at
        // 30 fps; speed is normalised to slices per 30 fps frame so the
        // preview's own frame rate doesn't change when it kicks in.
        var dt = t - spin.prevT;
        var speed = dt > 0 ? (spin.rotDeg - spin.prevRot) / seg * (1 / 30) / dt : 0;
        if (Math.floor(spin.rotDeg / seg) !== Math.floor(spin.prevRot / seg)) {
          spin.passedAt = t;
        }
        spin.flick = pointerFlick(spin.rotDeg / seg, speed,
          spin.passedAt === null ? null : t - spin.passedAt);
        spin.prevRot = spin.rotDeg;
        spin.prevT = t;
        var cardIn = land + CARD_DELAY;
        spin.dim = Math.max(0, Math.min(1, (t - cardIn) / CARD_FADE));
        if (t >= land) spin.winner = winnerIndex;
        spin.cardA = spin.dim;
      }
      drawSpinnerPreview();
      if (t < runEnd) {
        spin.raf = requestAnimationFrame(frame);
      } else {
        spin.raf = 0;
        spin.animating = false;
        spin.winner = winnerIndex;
        spin.cardA = 1;
        spin.dim = zoom ? 1 : 0;
        // Landed and still: a push on the pointer only if the landing is
        // late in its slice (the render's hold keeps computing this).
        if (zoom) spin.flick = pointerFlick(spin.rotDeg / seg, 0, null);
        $("spinner-test").disabled = false;
        drawSpinnerPreview();
      }
    };
    spin.raf = requestAnimationFrame(frame);
  }

  function updateCountBadge(n) {
    var badge = $("spinner-count");
    var text = n === 1 ? "1 ENTRY" : n + " ENTRIES";
    var warn = n < 2 || n > WHEEL_MAX;
    if (n < 2) text += " — NEED 2+";
    if (n > WHEEL_MAX) text += " — MAX " + WHEEL_MAX;
    badge.textContent = text;
    badge.classList.toggle("badge-warn", warn);
  }

  function rebuildWinnerSelect(entries) {
    var sel = $("spinner-winner");
    var prev = sel.value;
    while (sel.firstChild) sel.removeChild(sel.firstChild);
    entries.forEach(function (name) {
      var opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    });
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].value === prev) { sel.selectedIndex = i; break; }
    }
  }

  // Proportional timeline bar + total-length hint for the editable phases.
  function updateSpinnerTimeline() {
    var t = readSpinnerTiming();
    var wait = t.wait === null ? 0 : t.wait;
    var spinS = t.spin === null ? 7 : t.spin;
    var winner = t.winner === null ? 4 : t.winner;
    $("tl-wait").hidden = wait === 0;
    $("tl-wait").style.flexGrow = String(Math.max(wait, 0.001));
    $("tl-spin").style.flexGrow = String(spinS + 0.8);  // includes the wind-up
    $("tl-winner").style.flexGrow = String(winner);
    // ZOOM is its own phase after the spin; its field and bar segment exist
    // only while Cinematic zoom is ticked (the value is kept when hidden).
    var zoomOn = $("spinner-zoom").checked;
    var zoomS = (!zoomOn || t.zoom === null) ? (zoomOn ? 6 : 0)
        : Math.max(2, Math.min(30, t.zoom));
    $("spinner-zoom-field").hidden = !zoomOn;
    // The hint names the ZOOM field only while it is on screen (UX check:
    // "Set how long under ZOOM" sent people looking for a hidden field).
    $("spinner-zoom-hint-more").hidden = !zoomOn;
    $("tl-zoom").hidden = !zoomOn;
    $("tl-zoom").style.flexGrow = String(Math.max(zoomS, 0.001));
    var total = wait + 0.8 + spinS + zoomS + winner;
    $("spinner-timing-hint").textContent =
      "All in seconds. Total video: " + total.toFixed(1) + "s";
  }

  function updateSpinner() {
    var entries = readEntries();
    updateCountBadge(entries.length);
    rebuildWinnerSelect(entries);
    $("spinner-winner-row").hidden = (spinnerMode() !== "choose");
    $("spinner-export").disabled = exportBusy["spinner"] || (!!validateSpinner());
    $("spinner-test").disabled = spin.animating || entries.length < 2 || entries.length > WHEEL_MAX;
    updateSpinnerTimeline();
    drawSpinnerPreview();
  }

  function spinnerPayload() {
    var mode = spinnerMode();
    var t = readSpinnerTiming();
    var options = {
      entries: readEntries(),
      accent: currentAccent("spinner"),
      mode: (mode === "choose") ? "rigged" : "random",
      wait_seconds: t.wait === null ? 0 : t.wait,
      spin_seconds: t.spin === null ? 7 : t.spin,
      winner_seconds: t.winner === null ? 4 : t.winner,
      zoom: $("spinner-zoom").checked
    };
    // Sent only with zoom on (the server defaults it to 6 and ignores it
    // otherwise).
    if (options.zoom) options.zoom_seconds = t.zoom === null ? 6 : t.zoom;
    if (mode === "choose") options.winner = $("spinner-winner").value;
    return { type: "spinner", options: options };
  }

  // ----------------------------------------------------- AI fill (spinner)

  var aiConfigured = false;
  var AI_CUSTOM = "__custom__";

  // Show the key-entry section or the generate section based on whether a key
  // is stored on the server (never fetches the key itself — just a boolean).
  // Also populates the model dropdown with the presets + current model.
  function refreshAiStatus() {
    return fetch("/api/ai/status", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        aiConfigured = !!(j && j.configured);
        $("ai-key-section").hidden = aiConfigured;
        $("ai-gen-section").hidden = !aiConfigured;
        // The key is never sent back to the browser, so the input is always
        // blank. Say so, or an empty box looks like the key didn't save.
        $("ai-key-state").hidden = !aiConfigured;
        populateModels((j && j.models) || [], (j && j.model) || "");
      })
      .catch(function () { /* offline — panel still opens to the key form */ });
  }

  function populateModels(models, current) {
    var sel = $("ai-model");
    while (sel.firstChild) sel.removeChild(sel.firstChild);
    var known = false;
    models.forEach(function (m) {
      var opt = document.createElement("option");
      opt.value = m; opt.textContent = m;
      if (m === current) { opt.selected = true; known = true; }
      sel.appendChild(opt);
    });
    var other = document.createElement("option");
    other.value = AI_CUSTOM; other.textContent = "Other (custom)…";
    sel.appendChild(other);
    var custom = $("ai-model-custom");
    if (current && !known) {
      other.selected = true;
      custom.value = current;
      custom.hidden = false;
    } else {
      custom.hidden = true;
    }
    updateModelHint();
  }

  function currentModel() {
    var v = $("ai-model").value;
    if (v === AI_CUSTOM) return $("ai-model-custom").value.trim();
    return v;
  }

  function updateModelHint() {
    var m = currentModel();
    var hint = $("ai-model-hint");
    // Recommend, then the cost: auto is the default because free models
    // were slow, and it spends credit, which a volunteer should know.
    if (m === "openrouter/auto")
      hint.textContent = "Fastest — picks a small, cheap model (a fraction of a cent per fill). With no credit on your account it uses free models instead.";
    else if (m === "openrouter/free")
      hint.textContent = "Free models only — no credit needed, but they can be slow.";
    else
      hint.textContent = "If this model is busy or offline, it falls back to openrouter/free.";
  }

  // Persist the chosen model so it sticks between sessions.
  function saveAiModel() {
    var model = currentModel();
    if (!model) return;
    fetch("/api/ai/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: model })
    }).catch(function () { /* best effort; generate still sends the model */ });
  }

  function setAiStatus(msg, bad) {
    var el = $("ai-status");
    el.textContent = msg || "";
    el.classList.toggle("is-bad", !!bad);
  }

  // ---------------------------------------------------- AI requests (busy)
  // TIDY MY LIST and GENERATE can take minutes on a big list (the owner
  // waited three minutes on 300 names with nothing to say it was alive and
  // no way out). While one is waiting: a ring on its button, the elapsed
  // seconds in its status line, a CANCEL beside it, and a hard stop at
  // AI_CAP_MS. Cancel and timeout abort the fetch and never touch the
  // entries box. busy blocks a second request from a re-click.
  var AI_CAP_MS = 150000;
  var tidyBusy = {
    btn: "spinner-format", cancel: "spinner-format-cancel",
    statusEl: "spinner-format-status",
    status: function (m, bad) { setFormatStatus(m, bad ? "bad" : null); },
    cancelMsg: "Cancelled — your text wasn't changed.",
    timeoutMsg: "The AI took too long — try again, or tidy it in two halves.",
    busy: false, ctl: null, why: ""
  };
  var fillBusy = {
    btn: "ai-generate", cancel: "ai-cancel",
    statusEl: "ai-status",
    status: setAiStatus,
    cancelMsg: "Cancelled.",
    timeoutMsg: "The AI took too long — try again.",
    busy: false, ctl: null, why: ""
  };

  // st: tidyBusy / fillBusy. cfg: url, body, busyText, onResult(res),
  // onNetworkError().
  function aiRequest(st, cfg) {
    if (st.busy) return;
    var btn = $(st.btn), cancelBtn = $(st.cancel);
    var ctl = new AbortController();
    var t0 = Date.now();
    st.busy = true;
    st.ctl = ctl;
    st.why = "";
    btn.disabled = true;
    btn.classList.add("is-busy");
    cancelBtn.hidden = false;
    // Brighter than the hints around it while it waits (UX check: the dim
    // grey status and CANCEL were easy to miss).
    $(st.statusEl).classList.add("is-live");
    function tick() {
      var secs = Math.floor((Date.now() - t0) / 1000);
      // Past 10 s, say it's normal, so nobody gives up on a 300-name tidy.
      st.status(cfg.busyText + (secs > 0 ? " " + secs + "s" : "") +
          (secs >= 10 ? " Still working — a long list can take 30 seconds." : ""),
          false);
    }
    tick();
    var timer = setInterval(tick, 1000);
    var cap = setTimeout(function () { st.why = "timeout"; ctl.abort(); }, AI_CAP_MS);
    function done() {
      clearInterval(timer);
      clearTimeout(cap);
      st.busy = false;
      st.ctl = null;
      btn.disabled = false;
      btn.classList.remove("is-busy");
      cancelBtn.hidden = true;
      $(st.statusEl).classList.remove("is-live");
    }
    fetch(cfg.url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg.body),
      signal: ctl.signal
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        done();
        cfg.onResult(res);
      })
      .catch(function (err) {
        var why = st.why;
        done();
        if (err && err.name === "AbortError") {
          if (why === "timeout") st.status(st.timeoutMsg, true);
          else st.status(st.cancelMsg, false);
        } else {
          cfg.onNetworkError();
        }
      });
  }

  [tidyBusy, fillBusy].forEach(function (st) {
    $(st.cancel).addEventListener("click", function () {
      if (!st.ctl) return;
      st.why = "cancel";
      st.ctl.abort();
    });
  });

  function toggleAiPanel() {
    var panel = $("spinner-ai-panel");
    var open = panel.hidden;
    panel.hidden = !open;
    $("spinner-ai-toggle").setAttribute("aria-expanded", String(open));
    if (open) {
      setAiStatus("");
      refreshAiStatus().then(function () {
        (aiConfigured ? $("ai-desc") : $("ai-key-input")).focus();
      });
    }
  }

  function testAiKey() {
    setAiStatus("Testing key…");
    $("ai-key-test").disabled = true;
    fetch("/api/ai/test", { method: "POST" })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        $("ai-key-test").disabled = false;
        if (!res.ok) { setAiStatus((res.j && res.j.error) || "The key test failed.", true); return; }
        setAiStatus((res.j && res.j.message) || "Key works.");
      })
      .catch(function () {
        $("ai-key-test").disabled = false;
        setAiStatus("Could not reach the server.", true);
      });
  }

  function saveAiKey() {
    var key = $("ai-key-input").value.trim();
    if (!key) { setAiStatus("Paste your OpenRouter key first.", true); return; }
    setAiStatus("Saving…");
    fetch("/api/ai/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: key })
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok) { setAiStatus((res.j && res.j.error) || "Could not save the key.", true); return; }
        $("ai-key-input").value = "";
        setAiStatus("Key saved. Describe what you need below.");
        refreshAiStatus().then(function () { $("ai-desc").focus(); });
      })
      .catch(function () { setAiStatus("Could not reach the server.", true); });
  }

  function aiGenerate() {
    var desc = $("ai-desc").value.trim();
    if (!desc) { setAiStatus("Say what entries you need.", true); return; }
    var full = $("ai-full").checked;
    var count = intFrom($("ai-count"));
    if (!full && (count === null || count < 1 || count > 100)) {
      setAiStatus("How many? must be 1 to 100.", true); return;
    }
    if (count === null) count = 10;      // ignored in full mode, but keep valid
    var model = currentModel();
    if (!model) { setAiStatus("Enter a model name (or pick openrouter/free).", true); return; }
    var existing = readEntries();
    var room = WHEEL_MAX - existing.length;
    if (room <= 0) { setAiStatus("The wheel is already full (" + WHEEL_MAX + " max).", true); return; }

    aiRequest(fillBusy, {
      url: "/api/ai/generate",
      body: { description: desc, count: count, existing: existing, model: model, full: full },
      busyText: full
        ? "Asking the AI for the full list… (can take a moment)"
        : "Asking the AI…",
      onNetworkError: function () { setAiStatus("Could not reach the server.", true); },
      onResult: function (res) {
        if (!res.ok) { setAiStatus((res.j && res.j.error) || "The AI request failed.", true); return; }
        var got = (res.j && res.j.entries) || [];
        // Merge: keep existing, add new (case-insensitive), cap at 20.
        var seen = {};
        existing.forEach(function (e) { seen[e.toLowerCase()] = true; });
        var added = 0;
        got.forEach(function (e) {
          var k = e.toLowerCase();
          if (!seen[k] && existing.length < WHEEL_MAX) { existing.push(e); seen[k] = true; added += 1; }
        });
        $("spinner-entries").value = existing.join("\n");
        cancelTestSpin();
        updateSpinner();
        setAiStatus(added
          ? ("Added " + added + " " + (added === 1 ? "entry" : "entries") + ".")
          : "No new entries to add — try rewording.");
      }
    });
  }


  // ------------------------------------------------------ FORMAT WITH AI
  // (spec: docs/specs/spinner-format.md) A messy paste -- 150 names on one
  // line, commas in some places and new lines in others -- sorted into one
  // entry per line by the AI. The box's previous text is kept for UNDO
  // until the volunteer edits it again: replacing someone's paste with no
  // way back would be the worst outcome of a bad answer.
  var formatUndo = null;

  // tone: "bad" (an error), "warn" (worked, but over the wheel's limit) or
  // nothing. A success must not be red -- it read as a failure (UX check).
  function setFormatStatus(msg, tone) {
    var el = $("spinner-format-status");
    el.textContent = msg || "";
    el.hidden = !msg;
    el.classList.toggle("is-bad", tone === true || tone === "bad");
    el.classList.toggle("is-warn", tone === "warn");
  }

  function formatEntries() {
    var box = $("spinner-entries");
    var text = box.value;
    if (!text.trim()) { setFormatStatus("Paste some entries first.", true); return; }
    // Said on screen, not only in the tooltip: it is often people's names.
    aiRequest(tidyBusy, {
      url: "/api/ai/format",
      body: { text: text },
      busyText: "Tidying — sending your list to OpenRouter…",
      onNetworkError: function () {
        setFormatStatus("Couldn't reach the app — try again.", true);
      },
      onResult: function (res) {
        if (!res.ok || !res.j.entries) {
          setFormatStatus((res.j && res.j.error) || "The AI couldn't sort that out — try again.", true);
          return;
        }
        formatUndo = text;
        box.value = res.j.entries.join("\n");
        var n = res.j.entries.length;
        var msg = "Tidied into " + n + " entr" + (n === 1 ? "y" : "ies") + ".";
        if (n > WHEEL_MAX) {
          msg = "Tidied into " + n + " entries — the wheel holds " +
              WHEEL_MAX + ", so remove " + (n - WHEEL_MAX) +
              " before exporting.";
        }
        if (res.j.dropped > 0) {
          msg += res.j.dropped === 1
              ? " Left out 1 entry the AI added that wasn't in your text."
              : " Left out " + res.j.dropped + " entries the AI added " +
                "that weren't in your text.";
        }
        // The owner asked which model did it: auto picks one per call.
        if (res.j.model) msg += " Model: " + res.j.model + ".";
        setFormatStatus(msg, n > WHEEL_MAX ? "warn" : null);
        $("spinner-format-undo").hidden = false;
        cancelTestSpin();
        updateSpinner();
      }
    });
  }

  $("spinner-format").addEventListener("click", formatEntries);
  $("spinner-format-undo").addEventListener("click", function () {
    if (formatUndo === null) return;
    $("spinner-entries").value = formatUndo;
    formatUndo = null;
    $("spinner-format-undo").hidden = true;
    setFormatStatus("Undone — your text is back as you pasted it.");
    cancelTestSpin();
    updateSpinner();
  });

  // ---------------------------------------------------------------- wiring
  // spinner form
  $("spinner-entries").addEventListener("input", function () {
    cancelTestSpin();
    updateSpinner();
    // Typing after a format: the undo copy is now out of date.
    if (formatUndo !== null) {
      formatUndo = null;
      $("spinner-format-undo").hidden = true;
      setFormatStatus("");
    }
  });
  document.querySelectorAll('input[name="spinner-mode"]').forEach(function (r) {
    r.addEventListener("change", function () {
      cancelTestSpin();
      updateSpinner();
    });
  });
  // Ticking it resets the preview to the whole wheel, like changing the mode
  // does, so a held close-up never lingers once the box is unticked.
  $("spinner-zoom").addEventListener("change", function () {
    cancelTestSpin();
    updateSpinner();
  });
  $("spinner-winner").addEventListener("change", function () {
    cancelTestSpin();
    updateSpinner();   // recomputes button state; ends with drawSpinnerPreview()
  });
  ["spinner-wait", "spinner-spin", "spinner-zoom-secs", "spinner-winner-secs"].forEach(function (id) {
    $(id).addEventListener("input", updateSpinner);
  });
  // ZOOM snaps into 2–30 when you leave the field, so the field, Total
  // video and the export all agree (UX check: 50 stayed 50 and Total said
  // 61.8 s; 1 stayed 1 while Total counted 6).
  $("spinner-zoom-secs").addEventListener("change", function () {
    var v = intFrom(this);
    var fixed = v === null ? 6 : Math.max(2, Math.min(30, v));
    if (v !== fixed) {
      this.value = String(fixed);
      this.classList.remove("is-snapped");
      void this.offsetWidth;            // restart the flash
      this.classList.add("is-snapped");
    }
    updateSpinner();
  });

  // AI fill panel
  $("spinner-ai-toggle").addEventListener("click", toggleAiPanel);
  $("ai-key-save").addEventListener("click", saveAiKey);
  $("ai-generate").addEventListener("click", aiGenerate);
  $("ai-model").addEventListener("change", function () {
    var custom = (this.value === AI_CUSTOM);
    $("ai-model-custom").hidden = !custom;
    if (custom) { $("ai-model-custom").focus(); }
    else { saveAiModel(); }
    updateModelHint();
  });
  $("ai-full").addEventListener("change", function () {
    // In full-list mode the AI decides the count, so grey the number out.
    $("ai-count").disabled = this.checked;
  });
  $("ai-model-custom").addEventListener("input", updateModelHint);
  $("ai-model-custom").addEventListener("change", saveAiModel);
  $("ai-model-custom").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); saveAiModel(); $("ai-desc").focus(); }
  });
  $("ai-key-test").addEventListener("click", testAiKey);
  $("ai-test-link").addEventListener("click", testAiKey);
  $("ai-change-key").addEventListener("click", function () {
    $("ai-key-section").hidden = false;
    $("ai-gen-section").hidden = true;
    setAiStatus("");            // don't carry a stale error into this view
    $("ai-key-input").focus();
  });
  // Enter in the AI text fields must act, not submit the export form.
  $("ai-desc").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); aiGenerate(); }
  });
  $("ai-key-input").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); saveAiKey(); }
  });
  $("spinner-test").addEventListener("click", testSpin);

  var spinnerTile = {
    update: updateSpinner, validate: validateSpinner, payload: spinnerPayload,
    enter: updateSpinner, leave: function () {}
  };
  SV.wireTileForm("spinner", spinnerTile, {});
  SV.registerTile("spinner", spinnerTile);

})(window.SV);
