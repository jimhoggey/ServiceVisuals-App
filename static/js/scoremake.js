/* Service Visuals — scoreboard tile, "make one" half: type team names and
   scores, pick a look, get a 1920x1080 PNG. Sits beside board.js (which
   reads an existing points screen) inside the same Scoreboard view; the
   spec is docs/specs/scoreboard-make.md. Loaded after board.js.

   Its export/progress/done ids use the kind "scoreboard" (not "board") so
   they never clash with the editor's, and so the session list reads
   SCOREBOARD. */

"use strict";

(function (SV) {

  var $ = SV.$;
  var toInt = SV.toInt, currentAccent = SV.currentAccent;
  var showError = SV.showError, hideError = SV.hideError;
  var exportBusy = SV.exportBusy;
  var setStatus = SV.setStatus, setProgress = SV.setProgress;
  var setFormDisabled = SV.setFormDisabled;
  var finishExport = SV.finishExport, failExport = SV.failExport;
  var clearExportState = SV.clearExportState;

  var STORE_KEY = "sv-scoremake";
  var KIND = "scoreboard";

  // The eight team colours, in the order a swatch click cycles them. Must
  // match render/scoremake.py (the server draws these exact values).
  var COLORS = ["#e2564b", "#4f8fe6", "#5bbf6a", "#f2c94c",
                "#9b7ede", "#f08a3c", "#3cc3c9", "#e66fb0"];
  var COLOR_NAMES = ["Red", "Blue", "Green", "Yellow",
                     "Violet", "Orange", "Teal", "Pink"];
  var MIN_TEAMS = 2, MAX_TEAMS = 8;

  // One line per look. They say which to pick, not what it is.
  var LOOK_HINTS = {
    bars: "Best for a race: every team's bar grows with its score, so a close game looks close.",
    cards: "Best for 2 to 4 teams: one big tile each, easy to read from the back.",
    podium: "Best for a final result: a ranked list with 1st, 2nd and 3rd picked out."
  };

  var DEFAULT_TEAMS = [
    { name: "Red team", score: "0", color: COLORS[0] },
    { name: "Blue team", score: "0", color: COLORS[1] },
    { name: "Green team", score: "0", color: COLORS[2] },
    { name: "Yellow team", score: "0", color: COLORS[3] }
  ];

  var background = "";           // uploaded filename, or "" for none
  var previewTimer = 0;          // debounce handle
  var previewUrl = null;         // current object URL (revoked on swap)
  var previewSeq = 0;            // guards against out-of-order previews
  var inflight = false;          // a preview request is on the wire
  var queued = false;            // ...and another was asked for since
  var valShown = false;          // the error panel is showing a validation msg
  var prevShown = false;         // ...or a preview failure

  // ------------------------------------------------------- team rows ------

  function colorIndex(c) {
    var i = COLORS.indexOf(c);
    return i < 0 ? 0 : i;
  }

  function teamRows() {
    return Array.prototype.slice.call(
      $("scoreboard-teams").querySelectorAll(".sb-team"));
  }

  function paintDot(btn, color, n) {
    btn.style.setProperty("--c", color);
    btn.setAttribute("data-color", color);
    btn.setAttribute("aria-label", "Team " + n + " colour: " +
      COLOR_NAMES[colorIndex(color)] + ". Click for the next colour.");
  }

  function makeRow(team, n) {
    var row = document.createElement("div");
    row.className = "sb-team";
    row.setAttribute("role", "listitem");

    var dot = document.createElement("button");
    dot.type = "button";
    dot.className = "sb-dot";
    paintDot(dot, team.color, n);
    dot.addEventListener("click", function () {
      // Skip colours another team already has: two blue bars can't be told
      // apart on the screen (UX check). With 8 teams every colour is taken,
      // so the click does nothing rather than make a twin.
      var used = teamRows().filter(function (r) { return r !== row; })
        .map(function (r) {
          return r.querySelector(".sb-dot").getAttribute("data-color");
        });
      var at = colorIndex(dot.getAttribute("data-color"));
      for (var k = 1; k < COLORS.length; k++) {
        var next = COLORS[(at + k) % COLORS.length];
        if (used.indexOf(next) < 0) {
          paintDot(dot, next, rowNumber(row));
          update();
          return;
        }
      }
    });

    var name = document.createElement("input");
    name.type = "text";
    name.className = "sb-name";
    name.maxLength = 24;
    name.value = team.name;
    name.autocomplete = "off";
    name.spellcheck = false;
    name.setAttribute("aria-label", "Team " + n + " name");

    var score = document.createElement("input");
    score.type = "text";
    score.className = "sb-score";
    score.inputMode = "numeric";
    score.maxLength = 6;
    score.value = team.score;
    score.autocomplete = "off";
    score.spellcheck = false;
    score.setAttribute("aria-label", "Team " + n + " score");
    // Typing a score is nearly always replacing the last one.
    score.addEventListener("focus", function () { score.select(); });

    var rm = document.createElement("button");
    rm.type = "button";
    rm.className = "sb-remove";
    rm.innerHTML = "&times;";
    rm.setAttribute("aria-label", "Remove team " + n);
    rm.addEventListener("click", function () { removeTeam(row); });

    row.appendChild(dot);
    row.appendChild(name);
    row.appendChild(score);
    row.appendChild(rm);
    return row;
  }

  function rowNumber(row) {
    return teamRows().indexOf(row) + 1;
  }

  // Numbers in the aria-labels follow the row order, so renumber after any
  // add or remove.
  function relabel() {
    teamRows().forEach(function (row, i) {
      var n = i + 1;
      paintDot(row.querySelector(".sb-dot"),
        row.querySelector(".sb-dot").getAttribute("data-color"), n);
      row.querySelector(".sb-name").setAttribute("aria-label", "Team " + n + " name");
      row.querySelector(".sb-score").setAttribute("aria-label", "Team " + n + " score");
      row.querySelector(".sb-remove").setAttribute("aria-label", "Remove team " + n);
    });
  }

  function setTeams(teams) {
    var host = $("scoreboard-teams");
    while (host.firstChild) host.removeChild(host.firstChild);
    teams.forEach(function (t, i) { host.appendChild(makeRow(t, i + 1)); });
  }

  function addTeam() {
    var rows = teamRows();
    if (rows.length >= MAX_TEAMS) return;
    // Next colour nobody is using yet, so a new team never starts as a
    // twin of one already on the board.
    var used = rows.map(function (r) {
      return r.querySelector(".sb-dot").getAttribute("data-color");
    });
    var idx = rows.length % COLORS.length;
    for (var i = 0; i < COLORS.length; i++) {
      if (used.indexOf(COLORS[i]) < 0) { idx = i; break; }
    }
    var row = makeRow({ name: COLOR_NAMES[idx] + " team", score: "0",
                        color: COLORS[idx] }, rows.length + 1);
    $("scoreboard-teams").appendChild(row);
    row.querySelector(".sb-name").focus();
    row.querySelector(".sb-name").select();
    update();
  }

  function removeTeam(row) {
    var rows = teamRows();
    if (rows.length <= MIN_TEAMS) return;
    var at = rows.indexOf(row);
    row.parentNode.removeChild(row);
    relabel();
    // Keep the keyboard where it was instead of dropping it on <body>.
    var left = teamRows();
    left[Math.min(at, left.length - 1)].querySelector(".sb-name").focus();
    update();
  }

  // ----------------------------------------------------- reading the form --

  function readForm() {
    var look = document.querySelector('input[name="scoreboard-look"]:checked');
    var bd = document.querySelector('input[name="scoreboard-backdrop"]:checked');
    return {
      title: $("scoreboard-title").value.trim(),
      teams: teamRows().map(function (row) {
        return {
          name: row.querySelector(".sb-name").value.trim(),
          score: row.querySelector(".sb-score").value.trim(),
          color: row.querySelector(".sb-dot").getAttribute("data-color")
        };
      }),
      look: look ? look.value : "bars",
      sort: $("scoreboard-sort").checked,
      accent: currentAccent(KIND),
      backdrop: bd ? bd.value : "dark",
      bgDim: toInt($("scoreboard-bg-dim").value, 40)
    };
  }

  // The message for one team row, or null. Mirrors validate_scoreboard_make
  // so the volunteer sees the same words whether the browser or the server
  // catches it. Returns {msg, field} so the bad box can be outlined.
  // Names the team the way the volunteer sees it -- the name they typed --
  // not by a row number they'd have to count to (UX check).
  function teamProblem(t, n) {
    if (t.name.length < 1 || t.name.length > 24) {
      return { msg: "Team " + n + " needs a name (up to 24 characters).", field: "name" };
    }
    if (!/^\d{1,6}$/.test(t.score)) {
      return { msg: t.name + "'s score must be a whole number from 0 to 999999.", field: "score" };
    }
    if (!/^#[0-9a-f]{6}$/i.test(t.color)) {
      return { msg: "Team " + n + "'s colour must be a hex colour like \"#e2564b\".", field: "dot" };
    }
    return null;
  }

  function validate() {
    var f = readForm();
    if (f.title.length > 40) return "The title must be 40 characters or fewer.";
    if (f.teams.length < MIN_TEAMS || f.teams.length > MAX_TEAMS) {
      return "Add between 2 and 8 teams.";
    }
    for (var i = 0; i < f.teams.length; i++) {
      var p = teamProblem(f.teams[i], i + 1);
      if (p) return p.msg;
    }
    return null;
  }

  function payload() {
    var f = readForm();
    return {
      title: f.title,
      teams: f.teams.map(function (t) {
        return { name: t.name, score: parseInt(t.score, 10), color: t.color };
      }),
      look: f.look,
      sort: f.sort,
      accent: f.accent,
      // IMAGE with nothing uploaded yet previews and exports as dark.
      background: f.backdrop === "image" ? background : "",
      bg_dim: f.bgDim
    };
  }

  // ------------------------------------------------------------ remember --
  // Next week's scores start from last week's teams. Browser only; every
  // access is guarded because storage can be blocked or full.

  function save() {
    try {
      var f = readForm();
      localStorage.setItem(STORE_KEY, JSON.stringify({
        title: $("scoreboard-title").value,
        teams: teamRows().map(function (row) {
          return {
            name: row.querySelector(".sb-name").value,
            score: row.querySelector(".sb-score").value,
            color: row.querySelector(".sb-dot").getAttribute("data-color")
          };
        }),
        look: f.look, sort: f.sort, accent: f.accent,
        backdrop: f.backdrop, bgDim: f.bgDim
      }));
    } catch (e) { /* private window or blocked storage: just don't remember */ }
  }

  function str(v, max) {
    return (typeof v === "string") ? v.slice(0, max) : "";
  }

  function load() {
    var d = null;
    try {
      d = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
    } catch (e) { d = null; }
    if (!d || typeof d !== "object") return;

    if (Array.isArray(d.teams) && d.teams.length >= MIN_TEAMS &&
        d.teams.length <= MAX_TEAMS) {
      setTeams(d.teams.map(function (t, i) {
        t = (t && typeof t === "object") ? t : {};
        return {
          name: str(t.name, 24),
          score: str(String(t.score === undefined ? "0" : t.score), 6),
          color: COLORS.indexOf(t.color) >= 0 ? t.color : COLORS[i % COLORS.length]
        };
      }));
    }
    $("scoreboard-title").value = str(d.title, 40);
    if (d.look && LOOK_HINTS[d.look]) $("scoreboard-look-" + d.look).checked = true;
    if (typeof d.sort === "boolean") $("scoreboard-sort").checked = d.sort;
    if (d.backdrop === "image") $("scoreboard-backdrop-image").checked = true;
    var dim = parseInt(d.bgDim, 10);
    if (isFinite(dim) && dim >= 0 && dim <= 80) $("scoreboard-bg-dim").value = String(dim);

    if (typeof d.accent === "string" && /^#[0-9a-f]{6}$/i.test(d.accent)) {
      var radios = document.querySelectorAll('input[name="scoreboard-accent"]');
      var hit = false;
      Array.prototype.forEach.call(radios, function (r) {
        r.checked = (r.value.toLowerCase() === d.accent.toLowerCase());
        if (r.checked) hit = true;
      });
      $("scoreboard-accent-custom").value = d.accent;
      $("scoreboard-accent-custom").closest(".swatch-custom")
        .classList.toggle("is-active", !hit);
    }
  }

  // ------------------------------------------------------------- preview --

  function fetchPreview() {
    if ($("board-make").hidden) return;
    if (validate()) return;       // the error panel already says why
    // One request at a time: a render slower than the typing would otherwise
    // have every answer thrown away as stale.
    if (inflight) { queued = true; return; }
    inflight = true;
    queued = false;
    var seq = ++previewSeq;
    $("scoreboard-preview-loading").hidden = false;
    function done() {
      inflight = false;
      if (queued) fetchPreview(); else $("scoreboard-preview-loading").hidden = true;
    }
    fetch("/api/scoreboard/make/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload())
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (j) {
            var e = new Error("bad");
            e.userMessage = (j && j.error) ||
              "Couldn't draw the preview. Is the server still running?";
            throw e;
          });
        }
        return r.blob();
      })
      .then(function (blob) {
        if (seq === previewSeq) {
          var url = URL.createObjectURL(blob);
          $("scoreboard-preview-img").src = url;
          $("scoreboard-preview-img").style.visibility = "";
          if (previewUrl) URL.revokeObjectURL(previewUrl);
          previewUrl = url;
          if (prevShown) { hideError(KIND); prevShown = false; }
        }
        done();
      })
      .catch(function (err) {
        if (seq === previewSeq && !valShown) {
          showError(KIND, (err && err.userMessage) ||
            "Couldn't draw the preview. Is the server still running?");
          prevShown = true;
        }
        done();
      });
  }

  function schedulePreview() {
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(fetchPreview, 300);
  }

  // ------------------------------------------------------------- updating --

  function applyModes() {
    var f = readForm();
    var n = f.teams.length;

    $("scoreboard-look-hint").textContent = LOOK_HINTS[f.look] || "";

    // PODIUM always ranks, so Sort would be a switch that does nothing.
    var podium = f.look === "podium";
    $("scoreboard-sort").disabled = podium;
    $("scoreboard-sort-note").hidden = !podium;

    $("scoreboard-bg-image-row").hidden = f.backdrop !== "image";
    $("scoreboard-bg-dim-value").textContent = f.bgDim + "%";
    // Nothing to dim until an image is chosen.
    $("scoreboard-bg-dim").disabled = !background;

    $("scoreboard-add").disabled = n >= MAX_TEAMS;
    $("scoreboard-team-note").textContent = n >= MAX_TEAMS
      ? "8 is the most that fit. Remove one to add another."
      : "2 to 8 teams. Click a colour dot to change it.";
    teamRows().forEach(function (row) {
      row.querySelector(".sb-remove").disabled = n <= MIN_TEAMS;
    });
  }

  // Outline whichever box the message is about, so the volunteer is not left
  // hunting through eight rows for it.
  function markBadFields() {
    var f = readForm();
    teamRows().forEach(function (row, i) {
      var p = teamProblem(f.teams[i], i + 1);
      row.querySelector(".sb-name").classList.toggle("is-bad", !!p && p.field === "name");
      row.querySelector(".sb-score").classList.toggle("is-bad", !!p && p.field === "score");
    });
    $("scoreboard-title").classList.toggle("is-bad", f.title.length > 40);
  }

  function update() {
    applyModes();
    markBadFields();
    var err = validate();
    $("scoreboard-export").disabled = !!exportBusy[KIND] || !!err;
    // The problem shows right under the teams, where it was typed -- the
    // error panel sits under EXPORT, ~500 px away and off screen (UX
    // check) -- and the preview says it is waiting rather than silently
    // keeping an old picture.
    $("scoreboard-team-error").textContent = err || "";
    $("scoreboard-team-error").hidden = !err;
    $("scoreboard-preview-paused").hidden = !err;
    $("scoreboard-preview-img").classList.toggle("is-paused", !!err);
    if (valShown) {
      // Only clear what we put there: an export failure or an upload error
      // is not ours to wipe on the next keystroke.
      hideError(KIND);
      valShown = false;
    }
    save();
    schedulePreview();
  }

  // ------------------------------------------------------- opening/closing --

  function openMake() {
    hideError("board");
    $("board-picker").hidden = true;
    $("board-make").hidden = false;
    window.scrollTo(0, 0);
    clearExportState(KIND);       // also runs update() and the first preview
    $("scoreboard-title").focus();
  }

  // Back to the two-way choice. The editor (an opened board) is left alone:
  // if one is open the picker stays hidden, exactly as before.
  function showChoices() {
    if (previewTimer) { clearTimeout(previewTimer); previewTimer = 0; }
    $("board-make").hidden = true;
    if ($("board-editor").hidden) $("board-picker").hidden = false;
  }

  // ---------------------------------------------------------------- wiring --

  $("scoreboard-open").addEventListener("click", openMake);
  $("scoreboard-back").addEventListener("click", showChoices);
  // Coming back into Scoreboard from home always starts at the choice, not
  // wherever the form was left. (Runs after board.js's own tile handler.)
  $("tile-board").addEventListener("click", showChoices);

  $("scoreboard-add").addEventListener("click", addTeam);

  var form = $("scoreboard-form");
  form.addEventListener("input", update);
  form.addEventListener("change", update);
  // Enter in a name box must not submit anything; the form has no submit.
  form.addEventListener("submit", function (e) { e.preventDefault(); });
  SV.wireAccent(KIND, update);

  // background image upload (same route and patterns as the QR card)
  function bgNameReset() {
    $("scoreboard-bg-name").textContent = background ? "Image chosen" : "No image yet";
  }
  $("scoreboard-bg-file").addEventListener("change", function () {
    var file = this.files && this.files[0];
    if (!file) return;
    $("scoreboard-bg-name").textContent = "Uploading…";
    var fd = new FormData();
    fd.append("image", file);
    fetch("/api/upload-bg", { method: "POST", body: fd })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok || !res.j.filename) {
          bgNameReset();
          showError(KIND, (res.j && res.j.error) || "Could not use that image.");
          return;
        }
        background = res.j.filename;
        $("scoreboard-bg-name").textContent = file.name;
        $("scoreboard-bg-clear").hidden = false;
        hideError(KIND);
        valShown = false;
        update();
      })
      .catch(function () {
        bgNameReset();
        showError(KIND, "Could not upload the image. Is the server running?");
      });
  });
  $("scoreboard-bg-clear").addEventListener("click", function () {
    background = "";
    $("scoreboard-bg-file").value = "";
    $("scoreboard-bg-name").textContent = "No image yet";
    $("scoreboard-bg-clear").hidden = true;
    update();
  });

  // Like the QR still: fast enough to be synchronous, so it skips the render
  // queue and goes straight to the normal done panel.
  $("scoreboard-export").addEventListener("click", function () {
    var err = validate();
    if (err) { showError(KIND, err); valShown = true; return; }
    hideError(KIND);
    valShown = false;
    prevShown = false;
    setFormDisabled(KIND, true);
    $("scoreboard-done").hidden = true;
    $("scoreboard-progress").hidden = false;
    setProgress(KIND, 100);
    setStatus(KIND, "SAVING IMAGE…");
    fetch("/api/scoreboard/make/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload())
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          return { ok: r.ok, j: j };
        });
      })
      .then(function (res) {
        if (!res.ok || !res.j.filename) {
          failExport(KIND, (res.j && res.j.error) || "Could not save the image.");
          return;
        }
        setStatus(KIND, "DONE");
        finishExport(KIND, res.j.filename);
        // The done panel is beside the preview, far from the button that
        // was pressed: bring it into view so the click visibly did
        // something (UX check).
        $("scoreboard-progress").scrollIntoView({ block: "nearest", behavior: "smooth" });
      })
      .catch(function () {
        failExport(KIND, "Could not reach the server. Is it still running?");
      });
  });

  $("scoreboard-reveal").addEventListener("click", function () {
    SV.revealInFinder(KIND);
  });

  // Rows first (defaults), then whatever was remembered on top.
  setTeams(DEFAULT_TEAMS);
  load();
  applyModes();

  // Not wired through wireTileForm: the tile card and BACK belong to the
  // Scoreboard view (board.js). It only needs to be known to the export
  // helpers, which call update() when an export finishes.
  SV.registerTile(KIND, {
    update: update, validate: validate, payload: payload,
    enter: function () {}, leave: function () {}
  });

})(window.SV);
