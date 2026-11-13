/* Service Visuals — QR card tile: still preview, position grid, background
   upload, and export (video or a synchronous still PNG). Loaded after
   core.js. */

"use strict";

(function (SV) {

  var $ = SV.$;
  var currentAccent = SV.currentAccent, toInt = SV.toInt, intFrom = SV.intFrom;
  var showError = SV.showError, hideError = SV.hideError;
  var exportBusy = SV.exportBusy;
  var setStatus = SV.setStatus, setProgress = SV.setProgress;
  var setFormDisabled = SV.setFormDisabled;
  var finishExport = SV.finishExport, failExport = SV.failExport;

  // =============================================================== QR =======

  // The QR x/y and uploaded-background filename live outside the visible
  // form fields (x/y are hidden inputs the preview drag writes; background
  // is a server-side upload).
  var qrBackground = "";                 // uploaded filename, or "" for none
  var qrPreviewTimer = 0;                // debounce handle
  var qrPreviewUrl = null;               // current object URL (revoked on swap)
  var qrPreviewSeq = 0;                  // guards against out-of-order previews
  var qrInflight = false;                // a preview request is on the wire
  var qrQueued = false;                  // ...and another was asked for since
  var qrBox = null;                      // card box {l,t,w,h}, 1920x1080 px
  var qrDrag = null;                     // active drag, or null
  var qrDragTimer = 0;                   // throttle handle while dragging
  var QR_W = 1920, QR_H = 1080, QR_EDGE = 40;   // mirrors render/qr.py

  function unit(v) {
    var n = parseFloat(v);
    return (isFinite(n) && n >= 0 && n <= 1) ? n : 0.5;
  }

  function readQr() {
    var styleEl = document.querySelector('input[name="qr-style"]:checked');
    var bdEl = document.querySelector('input[name="qr-backdrop"]:checked');
    var backdrop = bdEl ? bdEl.value : "dark";
    return {
      url: $("qr-url").value.trim(),
      heading: $("qr-heading").value.trim(),
      caption: $("qr-caption").value.trim(),
      accent: currentAccent("qr"),
      duration: toInt($("qr-duration").value, 15),
      x: unit($("qr-x").value),
      y: unit($("qr-y").value),
      size: toInt($("qr-size").value, 100),
      // IMAGE is the only mode that carries a file; with nothing uploaded
      // yet it previews (and exports) as DARK, as the spec says.
      backdrop: backdrop === "image" ? "dark" : backdrop,
      bgDim: toInt($("qr-bg-dim").value, 0),
      bgFit: $("qr-bg-fit-fit").checked ? "fit" : "fill",
      background: backdrop === "image" ? qrBackground : "",
      style: styleEl ? styleEl.value : "squares",
      ring: $("qr-ring").checked
    };
  }

  var URL_EMPTY = "Enter a website or some text to encode.";

  // Everything validateQr checks except that a link was typed.
  function validateQrBesidesUrl() {
    var saved = $("qr-url").value;
    $("qr-url").value = "-";
    var err = validateQr();
    $("qr-url").value = saved;
    return err;
  }

  function validateQr() {
    var q = readQr();
    if (q.url.length < 1) return URL_EMPTY;
    if (q.url.length > 1000) return "The website or text must be 1000 characters or fewer.";
    if (q.heading.length > 30) return "The heading must be 30 characters or fewer.";
    if (q.caption.length > 60) return "The caption must be 60 characters or fewer.";
    var d = intFrom($("qr-duration"));
    if (d === null || d < 5 || d > 60) return "Clip length must be a whole number from 5 to 60 seconds.";
    return null;
  }

  function qrPayload() {
    var q = readQr();
    return {
      type: "qr",
      options: {
        url: q.url,
        heading: q.heading,
        caption: q.caption,
        accent: q.accent,
        duration_seconds: q.duration,
        x: q.x,
        y: q.y,
        size: q.size,
        backdrop: q.backdrop,
        bg_dim: q.bgDim,
        bg_fit: q.bgFit,
        background: q.background,
        style: q.style,
        ring: q.ring
      }
    };
  }

  // ---- the box over the preview ------------------------------------------
  // Percentages of the wrap (= the image), so no resize handling is needed.
  function drawQrBox(b) {
    var el = $("qr-box");
    if (!b) { el.hidden = true; return; }
    el.hidden = false;
    el.style.left = (100 * b.l / QR_W) + "%";
    el.style.top = (100 * b.t / QR_H) + "%";
    el.style.width = (100 * b.w / QR_W) + "%";
    el.style.height = (100 * b.h / QR_H) + "%";
  }

  function setPosCell(x, y) {
    Array.prototype.forEach.call(
      document.querySelectorAll("#qr-pos-grid .pos-cell"), function (c) {
        c.classList.toggle("is-active", x !== null &&
          parseFloat(c.dataset.x) === x && parseFloat(c.dataset.y) === y);
      });
  }

  function showQrSize() {
    $("qr-size-value").textContent = toInt($("qr-size").value, 100) + "%";
  }

  // Take the server's word for where the card landed and how big it is,
  // so a clamp is seen rather than silent. sentSize: what the slider said
  // when this request left -- if the volunteer has moved it since, don't
  // yank it back under their hand.
  function applyQrHeaders(r, sentSize) {
    var box = null;
    var parts = (r.headers.get("X-QR-Box") || "").split(",");
    if (parts.length === 4) {
      var n = parts.map(function (s) { return parseInt(s, 10); });
      if (n.every(function (v) { return isFinite(v); })) {
        box = { l: n[0], t: n[1], w: n[2], h: n[3] };
      }
    }
    qrBox = box;
    drawQrBox(box);
    // A card dragged (or clamped) into a corner IS at that snap: light the
    // cell, so the grid never says "nowhere" for a card sitting top-left.
    if (box) {
      var sx = snapOf(box.l, box.w, QR_W), sy = snapOf(box.t, box.h, QR_H);
      if (sx === null || sy === null) setPosCell(null, null);
      else setPosCell(sx, sy);
    }
    var sz = parseInt(r.headers.get("X-QR-Size"), 10);
    if (isFinite(sz) && toInt($("qr-size").value, 100) === sentSize) {
      $("qr-size").value = String(sz);
      showQrSize();
      // Say why the slider moved (UX check: 200% quietly became 131% and
      // read as a broken slider). Sizes step with whole-pixel modules, so a
      // few percent of rounding is not a limit.
      var note = "";
      if (sz < sentSize - 4) note = "That's the biggest that fits on screen.";
      else if (sz > sentSize + 4) note = "That's the smallest a phone can still scan.";
      $("qr-size-note").textContent = note;
      $("qr-size-note").hidden = !note;
    }
  }

  // Which snap (0, 0.5, 1) a card edge-to-edge [lo, lo+span] sits at, or
  // null for a free position. Centre first: a card as big as the frame
  // touches both edges and is still "centre".
  function snapOf(lo, span, frame) {
    if (Math.abs(lo + span / 2 - frame / 2) <= 2) return 0.5;
    if (lo <= QR_EDGE + 1) return 0;
    if (lo + span >= frame - QR_EDGE - 1) return 1;
    return null;
  }

  // Fetch a REAL still of the card from the server so the preview shows the
  // exact scannable code (and any background image / position). Debounced.
  function fetchQrPreview() {
    // No link yet is the one gap the server previews anyway, as the
    // background on its own: the owner picked a photo first and stared
    // at an empty pane. Any other problem still blanks the preview.
    var err = validateQr();
    if (err && !(err === URL_EMPTY && !validateQrBesidesUrl())) {
      // Nothing to preview yet: a blank pane, not a broken-image icon with
      // "Live QR card preview" alt text (which is what an <img> with no
      // src shows). visibility, not hidden — the img's display rule wins.
      $("qr-preview-img").style.visibility = "hidden";
      qrBox = null;
      drawQrBox(null);
      return;
    }
    // One request at a time: while dragging we ask every ~120 ms, and a
    // render slower than that would otherwise have every answer thrown away
    // as stale, so the preview would freeze until release.
    if (qrInflight) { qrQueued = true; return; }
    qrInflight = true;
    qrQueued = false;
    var seq = ++qrPreviewSeq;
    var opts = qrPayload().options;
    $("qr-preview-loading").hidden = false;
    function done() {
      qrInflight = false;
      if (qrQueued) fetchQrPreview(); else $("qr-preview-loading").hidden = true;
    }
    fetch("/api/qr-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(opts)
    })
      .then(function (r) {
        if (!r.ok) throw new Error("bad");
        return r.blob().then(function (blob) { return { r: r, blob: blob }; });
      })
      .then(function (res) {
        if (seq === qrPreviewSeq) {
          var url = URL.createObjectURL(res.blob);
          $("qr-preview-img").src = url;
          $("qr-preview-img").style.visibility = "";
          if (qrPreviewUrl) URL.revokeObjectURL(qrPreviewUrl);
          qrPreviewUrl = url;
          // The box and slider only follow the server once the pointer is
          // up and nothing newer is waiting; mid-drag they follow the
          // pointer instead.
          if (!qrDrag && !qrQueued) applyQrHeaders(res.r, opts.size);
        }
        done();
      })
      .catch(function () { done(); });
  }

  function scheduleQrPreview() {
    if (qrPreviewTimer) clearTimeout(qrPreviewTimer);
    qrPreviewTimer = setTimeout(fetchQrPreview, 350);
  }

  // The upload row and DIM only mean something on IMAGE, and the ring
  // colour only while the ring is on. Runs on every updateQr() call so it
  // always reflects the live controls — same pattern as the timer's
  // applyTimerMode().
  function applyQrModes() {
    var bdEl = document.querySelector('input[name="qr-backdrop"]:checked');
    $("qr-bg-image-row").hidden = !bdEl || bdEl.value !== "image";
    $("qr-ring-group").hidden = !$("qr-ring").checked;
    $("qr-bg-dim-value").textContent = toInt($("qr-bg-dim").value, 0) + "%";
    // Nothing to dim until an image is chosen (UX check: a live slider
    // that did nothing).
    $("qr-bg-dim").disabled = !qrBackground;
    showQrSize();
  }

  function updateQr() {
    applyQrModes();
    var err = validateQr();
    $("qr-export").disabled = exportBusy["qr"] || (!!err);
    $("qr-export-png").disabled = exportBusy["qr"] || (!!err);
    var hint = $("qr-url-hint");
    var urlErr = null;
    var url = $("qr-url").value.trim();
    if (url.length < 1) urlErr = "Enter a website or some text to encode.";
    else if (url.length > 1000) urlErr = "Must be 1000 characters or fewer.";
    hint.textContent = urlErr || "A web address, or any plain text — 1 to 1000 characters";
    hint.classList.toggle("is-bad", !!urlErr);
    var dHint = $("qr-duration-hint");
    var d = intFrom($("qr-duration"));
    var dErr = (d === null || d < 5 || d > 60) ? "5 to 60 seconds only." : null;
    dHint.textContent = dErr || "5 to 60 seconds";
    dHint.classList.toggle("is-bad", !!dErr);
    scheduleQrPreview();
  }


  // ---------------------------------------------------------------- wiring
  $("qr-refresh").addEventListener("click", function () {
    if (qrPreviewTimer) clearTimeout(qrPreviewTimer);
    fetchQrPreview();
  });

  // PNG export: fast enough to be synchronous, so it skips the render queue
  // and reuses the normal done panel (Download / Reveal / Make another).
  $("qr-export-png").addEventListener("click", function () {
    var err = validateQr();
    if (err) { showError("qr", err); return; }
    hideError("qr");
    setFormDisabled("qr", true);
    $("qr-done").hidden = true;
    $("qr-progress").hidden = false;
    setProgress("qr", 100);
    setStatus("qr", "SAVING IMAGE…");
    fetch("/api/qr-image", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(qrPayload().options)
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok || !res.j.filename) {
          failExport("qr", (res.j && res.j.error) || "Could not save the image.");
          return;
        }
        setStatus("qr", "DONE");
        finishExport("qr", res.j.filename);
      })
      .catch(function () {
        failExport("qr", "Could not reach the server. Is it still running?");
      });
  });

  // position 3x3 grid: nine quick snaps of the same x/y a drag writes
  Array.prototype.forEach.call(
    document.querySelectorAll("#qr-pos-grid .pos-cell"),
    function (cell) {
      cell.addEventListener("click", function () {
        $("qr-x").value = cell.dataset.x;
        $("qr-y").value = cell.dataset.y;
        setPosCell(parseFloat(cell.dataset.x), parseFloat(cell.dataset.y));
        fetchQrPreview();
      });
    }
  );

  // RESET puts back the size as well as the place, like the timer's.
  $("qr-place-reset").addEventListener("click", function () {
    $("qr-x").value = "0.5";
    $("qr-y").value = "0.5";
    $("qr-size").value = "100";
    showQrSize();
    setPosCell(0.5, 0.5);
    fetchQrPreview();
  });

  // ---- drag to move, corner to resize ------------------------------------
  // House style follows the timer's digits drag: pointer capture, grab point
  // kept under the pointer. While dragging, the box follows the pointer on
  // its own (clamped like the renderer's EDGE margin) and the preview image
  // is re-fetched about every 120 ms; on release one more fetch brings the
  // server's final word on box and size.
  function qrPoint(e) {
    var r = $("qr-preview-img").getBoundingClientRect();
    var k = QR_W / (r.width || 1);
    return { x: (e.clientX - r.left) * k, y: (e.clientY - r.top) * k };
  }

  function qrDragFetch() {
    if (qrDragTimer) return;
    qrDragTimer = setTimeout(function () {
      qrDragTimer = 0;
      fetchQrPreview();
    }, 120);
  }

  function wireQrDrag() {
    var box = $("qr-box");
    box.addEventListener("pointerdown", function (e) {
      if (!qrBox || e.button > 0) return;
      var p = qrPoint(e);
      var cx = qrBox.l + qrBox.w / 2, cy = qrBox.t + qrBox.h / 2;
      var corner = e.target.getAttribute && e.target.getAttribute("data-corner");
      // Resizing pins the OPPOSITE corner, as every photo app does (UX
      // check: scaling about the centre moved the corner you weren't
      // holding).
      var west = corner && corner.charAt(1) === "w";
      var north = corner && corner.charAt(0) === "n";
      var ax = west ? qrBox.l + qrBox.w : qrBox.l;
      var ay = north ? qrBox.t + qrBox.h : qrBox.t;
      qrDrag = {
        mode: corner ? "resize" : "move",
        cx: cx, cy: cy, w: qrBox.w, h: qrBox.h,
        dx: p.x - cx, dy: p.y - cy,
        ax: ax, ay: ay, west: west, north: north,
        d0: Math.max(1, Math.sqrt((p.x - ax) * (p.x - ax) +
                                  (p.y - ay) * (p.y - ay))),
        size0: toInt($("qr-size").value, 100)
      };
      box.setPointerCapture(e.pointerId);
      box.classList.add("is-dragging");
      e.preventDefault();
    });
    box.addEventListener("pointermove", function (e) {
      if (!qrDrag) return;
      var d = qrDrag, p = qrPoint(e);
      if (d.mode === "move") {
        var cx = Math.min(Math.max(p.x - d.dx, QR_EDGE + d.w / 2),
                          QR_W - QR_EDGE - d.w / 2);
        var cy = Math.min(Math.max(p.y - d.dy, QR_EDGE + d.h / 2),
                          QR_H - QR_EDGE - d.h / 2);
        $("qr-x").value = (cx / QR_W).toFixed(4);
        $("qr-y").value = (cy / QR_H).toFixed(4);
        setPosCell(null, null);
        qrBox = { l: cx - d.w / 2, t: cy - d.h / 2, w: d.w, h: d.h };
        d.cx = cx; d.cy = cy;
      } else {
        // From the pinned corner, so the corner you hold stays under the
        // pointer. The ratio is bounded by the slider's range and by the
        // room between the pinned corner and the frame; the renderer's
        // 9 px module floor is reported after.
        var dist = Math.sqrt((p.x - d.ax) * (p.x - d.ax) +
                             (p.y - d.ay) * (p.y - d.ay));
        var s = dist / d.d0;
        var room = (d.west ? d.ax - QR_EDGE : QR_W - QR_EDGE - d.ax) / d.w;
        var roomY = (d.north ? d.ay - QR_EDGE : QR_H - QR_EDGE - d.ay) / d.h;
        s = Math.min(s, room, roomY, 200 / d.size0);
        s = Math.max(s, 50 / d.size0);
        var size = Math.round(d.size0 * s);
        var r = size / d.size0;
        var w = d.w * r, h = d.h * r;
        $("qr-size").value = String(size);
        showQrSize();
        qrBox = { l: d.west ? d.ax - w : d.ax, t: d.north ? d.ay - h : d.ay,
                  w: w, h: h };
        // The card's centre moved with it; the renderer places by centre.
        $("qr-x").value = ((qrBox.l + w / 2) / QR_W).toFixed(4);
        $("qr-y").value = ((qrBox.t + h / 2) / QR_H).toFixed(4);
        setPosCell(null, null);
      }
      drawQrBox(qrBox);
      qrDragFetch();
    });
    function endDrag() {
      if (!qrDrag) return;
      qrDrag = null;
      box.classList.remove("is-dragging");
      if (qrDragTimer) { clearTimeout(qrDragTimer); qrDragTimer = 0; }
      if (qrPreviewTimer) { clearTimeout(qrPreviewTimer); qrPreviewTimer = 0; }
      fetchQrPreview();
    }
    box.addEventListener("pointerup", endDrag);
    box.addEventListener("pointercancel", endDrag);
  }
  wireQrDrag();

  // background image upload
  $("qr-bg-file").addEventListener("change", function () {
    var file = this.files && this.files[0];
    if (!file) return;
    $("qr-bg-name").textContent = "Uploading…";
    var fd = new FormData();
    fd.append("image", file);
    fetch("/api/upload-bg", { method: "POST", body: fd })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok || !res.j.filename) {
          $("qr-bg-name").textContent = qrBackground ? "Image chosen" : "No image yet";
          showError("qr", (res.j && res.j.error) || "Could not use that image.");
          return;
        }
        qrBackground = res.j.filename;
        $("qr-bg-name").textContent = file.name;
        $("qr-bg-clear").hidden = false;
        hideError("qr");
        fetchQrPreview();
      })
      .catch(function () {
        $("qr-bg-name").textContent = qrBackground ? "Image chosen" : "No image yet";
        showError("qr", "Could not upload the image — is the server running?");
      });
  });
  $("qr-bg-clear").addEventListener("click", function () {
    qrBackground = "";
    $("qr-bg-file").value = "";
    $("qr-bg-name").textContent = "No image yet";
    $("qr-bg-clear").hidden = true;
    fetchQrPreview();
  });


  var qrTile = {
    update: updateQr, validate: validateQr, payload: qrPayload,
    enter: updateQr, leave: function () {}
  };
  SV.wireTileForm("qr", qrTile, { autoUpdate: true });
  SV.registerTile("qr", qrTile);

})(window.SV);
