// Factory Dash client. All run state is folded server-side (Rust); the client renders
// snapshots and appends live events to the timeline.
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STAGE_LABEL = {
    spec: "Spec", archaeologist: "Archaeologist", analyst: "Analyst", architect: "Architect",
    test_engineer: "Test Engineer", lock_tests: "Lock tests", firmware_engineer: "Firmware Eng.",
    debugger: "Debugger", test_auditor: "Test Auditor", reviewer: "Reviewer", integrator: "Integrator", report: "report.md",
    g1: "G1 build", g2: "G2 size", g3: "G3 static", g4: "G4 ztest", g5: "G5 Renode",
  };
  const label = (k) => STAGE_LABEL[k] || k;

  let runs = [];
  let boards = null;
  let selectedRun = null;
  let selectedNode = null;
  let snap = null;          // latest server snapshot for selectedRun
  let events = [];          // events for selectedRun (snapshot + live)
  let runES = null;
  let refetchTimer = null;

  // -------------------------------------------------------------------------------------
  // Connection + run list
  // -------------------------------------------------------------------------------------
  function setConn(state, text) {
    $("conn-led").className = "led " + state;
    $("conn").textContent = text;
  }

  function connectAll() {
    const es = new EventSource("/api/stream");
    es.addEventListener("runs", (e) => {
      runs = JSON.parse(e.data);
      setConn("live", "live");
      renderRunList();
      if (!selectedRun && runs.length) selectRun(fromHash() || runs[0].id);
      if (!runs.length) showEmpty();
    });
    es.onerror = () => setConn("down", "disconnected, retrying…");
    es.onopen = () => setConn("live", "live");
  }

  function renderRunList() {
    $("run-count").textContent = runs.length ? String(runs.length) : "";
    $("run-list").innerHTML = runs.map((r) => `
      <li><button type="button" class="run-item" data-id="${esc(r.id)}" aria-current="${r.id === selectedRun}">
        <span class="r1"><span class="dot ${esc(r.status)}"></span><span class="t">${esc(r.title || r.id)}</span></span>
        <span class="r2"><span>${esc(r.board)}</span><span>iter ${r.iteration}${r.max_iterations ? "/" + r.max_iterations : ""}</span>
        <span>$${(r.cost_usd || 0).toFixed(2)}</span><span>${ago(r.last_ts)}</span></span>
      </button></li>`).join("");
  }

  $("run-list").addEventListener("click", (e) => {
    const b = e.target.closest(".run-item");
    if (b) selectRun(b.dataset.id);
  });

  function fromHash() {
    const h = decodeURIComponent(location.hash.slice(1));
    return runs.some((r) => r.id === h) ? h : null;
  }
  window.addEventListener("hashchange", () => { const h = fromHash(); if (h && h !== selectedRun) selectRun(h); });

  function showEmpty() {
    $("empty").hidden = false;
    $("run").hidden = true;
  }

  // -------------------------------------------------------------------------------------
  // Selected run
  // -------------------------------------------------------------------------------------
  function selectRun(id) {
    if (runES) runES.close();
    selectedRun = id;
    selectedNode = null;
    timelineFilter = null;
    snap = null;
    events = [];
    if (location.hash.slice(1) !== id) history.replaceState(null, "", "#" + encodeURIComponent(id));
    renderRunList();
    $("empty").hidden = true;
    $("run").hidden = false;

    runES = new EventSource(`/api/runs/${encodeURIComponent(id)}/stream`);
    runES.addEventListener("snapshot", (e) => {
      const s = JSON.parse(e.data);
      snap = s.state;
      events = s.events;
      renderAll(false);
    });
    runES.addEventListener("event", (e) => {
      const ev = JSON.parse(e.data);
      if (events.length && ev.seq <= events[events.length - 1].seq) return;
      events.push(ev);
      appendTimeline(ev, true);
      scheduleRefetch();
    });
    runES.addEventListener("resync", () => selectRun(id));
  }

  function scheduleRefetch() {
    clearTimeout(refetchTimer);
    refetchTimer = setTimeout(async () => {
      const id = selectedRun;
      try {
        // Folded state only; the event history already arrived over the stream.
        const r = await fetch(`/api/runs/${encodeURIComponent(id)}/state`);
        if (!r.ok || id !== selectedRun) return;
        snap = await r.json();
        renderState();
      } catch (_) { /* transient; next event retries */ }
    }, 120);
  }

  function renderAll() {
    renderState();
    renderTimeline();
  }

  function renderState() {
    if (!snap) return;
    const s = snap;
    $("run-title").textContent = s.title || s.id;
    $("run-meta").textContent = `${s.mode === "change" ? "change request" : "new firmware"} · ${s.board} · ${s.id}`;
    $("run-reason").textContent = s.reason || (s.status === "running" ? "In progress" : "");
    const pill = $("run-status");
    pill.className = "status-pill " + s.status;
    pill.textContent = s.status;

    $("st-iter").textContent = `${s.iteration}${s.max_iterations ? " / " + s.max_iterations : ""}`;
    $("st-cost").textContent = "$" + (s.usage.cost_usd || 0).toFixed(3);
    $("st-tokens").textContent = `${kfmt(s.usage.input_tokens)} / ${kfmt(s.usage.output_tokens)}`;
    $("st-active").textContent = s.active_stage ? label(s.active_stage) : (s.status === "running" ? "waiting" : "done");
    tickElapsed();

    renderPipeline();
    renderInspector();
    renderCosts();
    $("artifacts").innerHTML = s.artifacts.length
      ? s.artifacts.map((a) => `<li>${esc(a)}</li>`).join("")
      : `<li class="muted" style="border:0;padding:0">None yet</li>`;
    renderBoard();
  }

  function tickElapsed() {
    if (!snap || snap.started_ts == null) { $("st-elapsed").textContent = "–"; return; }
    const end = snap.finished_ts ?? (snap.status === "running" ? Date.now() / 1000 : snap.last_ts);
    $("st-elapsed").textContent = dur(end - snap.started_ts);
  }
  setInterval(() => { tickElapsed(); if (runs.length) renderRunListAges(); }, 1000);
  function renderRunListAges() {
    document.querySelectorAll(".run-item").forEach((b) => {
      const r = runs.find((x) => x.id === b.dataset.id);
      const last = b.querySelector(".r2 span:last-child");
      if (r && last) last.textContent = ago(r.last_ts);
    });
  }

  // -------------------------------------------------------------------------------------
  // Pipeline SVG
  // -------------------------------------------------------------------------------------
  const NS = "http://www.w3.org/2000/svg";
  const H = 48;
  const LAYOUT = {
    spec:  { x: 20,  y: 30,  w: 110, s: "input" },
    analyst: { x: 168, y: 30, w: 132, s: "requirements" },
    architect: { x: 338, y: 30, w: 132, s: "design.md" },
    test_engineer: { x: 508, y: 30, w: 150, s: "tests/**" },
    lock_tests: { x: 696, y: 30, w: 134, s: "sha256" },
    firmware_engineer: { x: 800, y: 170, w: 140, s: "src/**" },
    g1: { x: 672, y: 170, w: 96, s: "west build", gate: true },
    g2: { x: 546, y: 170, w: 96, s: "ROM/RAM", gate: true },
    g3: { x: 420, y: 170, w: 96, s: "cppcheck", gate: true },
    g4: { x: 294, y: 170, w: 96, s: "twister", gate: true },
    g5: { x: 168, y: 170, w: 96, s: "robot", gate: true },
    reviewer: { x: 20, y: 170, w: 120, s: "review" },
    debugger: { x: 420, y: 310, w: 150, s: "root cause" },
    test_auditor: { x: 420, y: 420, w: 150, s: "test amendment" },
    integrator: { x: 20, y: 310, w: 120, s: "traceability" },
    report: { x: 168, y: 310, w: 110, s: "output", io: true },
  };
  LAYOUT.spec.io = true;
  const cx = (n) => n.x + n.w / 2, cy = (n) => n.y + H / 2;

  function svgEl(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    parent.appendChild(e);
    return e;
  }

  function buildPipeline() {
    const svg = $("pipeline");
    svg.innerHTML = "";
    const defs = svgEl("defs", {}, svg);
    for (const [id, color] of [["arr", "var(--ink-2)"], ["arrF", "var(--fail)"]]) {
      const m = svgEl("marker", { id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, defs);
      svgEl("path", { d: "M0,0 L10,5 L0,10 z", fill: color }, m);
    }
    const edges = svgEl("g", {}, svg);
    const line = (pts, fail, noArrow) => svgEl("polyline", {
      points: pts.map((p) => p.join(",")).join(" "), fill: "none",
      stroke: fail ? "var(--fail)" : "var(--ink-2)", "stroke-width": 1.5,
      "stroke-dasharray": fail ? "5 4" : "none",
      "marker-end": noArrow ? "none" : `url(#${fail ? "arrF" : "arr"})`,
    }, edges);
    const txt = (x, y, s, cls, anchor = "middle") => { const t = svgEl("text", { x, y, class: cls, "text-anchor": anchor }, edges); t.textContent = s; };
    const L = LAYOUT, right = (n) => [n.x + n.w, cy(n)], left = (n) => [n.x, cy(n)];

    [["spec", "analyst"], ["analyst", "architect"], ["architect", "test_engineer"], ["test_engineer", "lock_tests"]]
      .forEach(([a, b]) => line([right(L[a]), [L[b].x - 2, cy(L[b])]]));
    line([[cx(L.lock_tests), L.lock_tests.y + H], [cx(L.lock_tests), 124], [cx(L.firmware_engineer) - 20, 124], [cx(L.firmware_engineer) - 20, L.firmware_engineer.y - 2]]);
    txt(cx(L.lock_tests) + 8, 118, "tests frozen", "t-edge", "start");
    [["firmware_engineer", "g1"], ["g1", "g2"], ["g2", "g3"], ["g3", "g4"], ["g4", "g5"], ["g5", "reviewer"]]
      .forEach(([a, b]) => line([left(L[a]), [L[b].x + L[b].w + 2, cy(L[b])]]));
    txt((L.reviewer.x + L.reviewer.w + L.g5.x) / 2, cy(L.g5) - 8, "pass", "t-edge");
    const busY = 268;
    ["g1", "g2", "g3", "g4", "g5"].forEach((k) => line([[cx(L[k]), L[k].y + H], [cx(L[k]), busY]], true, true));
    line([[cx(L.g5), busY], [cx(L.g1), busY]], true, true);
    line([[cx(L.debugger), busY], [cx(L.debugger), L.debugger.y - 2]], true);
    txt(cx(L.g1) + 8, busY - 6, "any gate fails", "t-fail", "start");
    line([right(L.debugger), [cx(L.firmware_engineer) - 20, cy(L.debugger)], [cx(L.firmware_engineer) - 20, L.firmware_engineer.y + H + 2]], true);
    txt(L.debugger.x + L.debugger.w + 12, cy(L.debugger) - 7, "fix suggestion", "t-fail", "start");
    line([[L.reviewer.x + L.reviewer.w - 18, L.reviewer.y + H], [L.reviewer.x + L.reviewer.w - 18, 384], [cx(L.firmware_engineer) + 30, 384], [cx(L.firmware_engineer) + 30, L.firmware_engineer.y + H + 2]], true);
    txt(L.reviewer.x + L.reviewer.w - 8, 378, "high-severity review findings", "t-fail", "start");
    line([[L.reviewer.x + 30, L.reviewer.y + H], [L.reviewer.x + 30, L.integrator.y - 2]]);
    txt(L.reviewer.x + 38, 268, "clean", "t-edge", "start");
    line([right(L.integrator), [L.report.x - 2, cy(L.report)]]);
    line([[cx(L.debugger), L.debugger.y + H], [cx(L.debugger), L.test_auditor.y - 2]], true);
    txt(cx(L.debugger) - 8, 404, "blames a test", "t-fail", "end");
    txt(L.test_auditor.x + L.test_auditor.w + 12, cy(L.test_auditor) + 4, "approved: re-lock tests, re-run gates", "t-edge", "start");

    for (const key in L) {
      const n = L[key];
      const g = svgEl("g", { class: "node idle", "data-node": key, tabindex: 0, role: "button" }, svg);
      svgEl("rect", { class: "box", x: n.x, y: n.y, width: n.w, height: H, rx: n.gate ? 0 : n.io ? 24 : 4, "stroke-width": 1.5, "stroke-dasharray": n.io ? "3 3" : "none" }, g);
      if (n.gate) {
        svgEl("rect", { x: n.x - 3, y: cy(n) - 3, width: 6, height: 6, fill: "var(--ink)" }, g);
        svgEl("rect", { x: n.x + n.w - 3, y: cy(n) - 3, width: 6, height: 6, fill: "var(--ink)" }, g);
      }
      const t1 = svgEl("text", { x: cx(n), y: n.y + 21, class: "t-main", "text-anchor": "middle" }, g);
      t1.textContent = label(key);
      const t2 = svgEl("text", { x: cx(n), y: n.y + 36, class: "t-sub", "text-anchor": "middle" }, g);
      t2.textContent = n.s;
      const badge = svgEl("text", { x: n.x + n.w - 6, y: n.y - 5, class: "t-badge", "text-anchor": "end" }, g);
      badge.textContent = "";
    }
    svg.addEventListener("click", (e) => { const g = e.target.closest(".node"); if (g) selectNode(g.dataset.node); });
    svg.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const g = e.target.closest(".node"); if (g) { e.preventDefault(); selectNode(g.dataset.node); }
    });
  }

  function nodeState(key) {
    const s = snap;
    if (!s) return { status: "idle" };
    if (key === "spec") {
      const n = s.mode === "change" ? s.nodes.archaeologist : null;
      if (n) return n;
      return { status: s.started_ts != null ? "pass" : "idle", runs: 0, summary: s.title };
    }
    if (key === "report") {
      const done = s.artifacts.some((a) => a === "report.md" || a.endsWith("/report.md"));
      return { status: done ? "pass" : "idle", runs: 0, summary: done ? "report.md written" : "" };
    }
    return s.nodes[key] || { status: "idle", runs: 0 };
  }

  function renderPipeline() {
    if (!snap) return;
    const specLabel = snap.mode === "change" ? "Archaeologist" : "Spec";
    document.querySelectorAll("#pipeline .node").forEach((g) => {
      const key = g.dataset.node;
      const st = nodeState(key);
      g.setAttribute("class", `node ${st.status}${key === selectedNode ? " selected" : ""}`);
      const texts = g.querySelectorAll("text");
      if (key === "spec") texts[0].textContent = specLabel;
      const badge = texts[texts.length - 1];
      const runsN = st.runs || 0;
      badge.textContent = runsN > 1 || st.fails ? `×${runsN}${st.fails ? ` · ${st.fails} fail` : ""}` : "";
      g.setAttribute("aria-label", `${label(key)}: ${st.status}${st.summary ? ", " + st.summary : ""}`);
    });
  }

  // -------------------------------------------------------------------------------------
  // Inspector
  // -------------------------------------------------------------------------------------
  function selectNode(key) {
    selectedNode = selectedNode === key ? null : key;
    timelineFilter = selectedNode && selectedNode !== "spec" && selectedNode !== "report" ? selectedNode : null;
    renderPipeline();
    renderInspector();
    renderTimeline();
  }

  function renderInspector() {
    const box = $("inspector");
    $("insp-node").textContent = selectedNode ? label(selectedNode) : "";
    if (!selectedNode || !snap) { box.innerHTML = `<p class="muted">Select a step in the pipeline.</p>`; return; }
    const st = nodeState(selectedNode);
    const usage = snap.usage_by_stage[selectedNode];
    let html = `<dl class="kv">
      <dt>Status</dt><dd>${esc(st.status)}</dd>
      ${st.runs != null ? `<dt>Runs</dt><dd>${esc(st.runs)}${st.fails ? ` (${esc(st.fails)} failed)` : ""}</dd>` : ""}
      ${st.iteration ? `<dt>Iteration</dt><dd>${esc(st.iteration)}</dd>` : ""}
      ${usage ? `<dt>API-equiv. cost</dt><dd>$${usage.cost_usd.toFixed(3)} · ${kfmt(usage.input_tokens)} in / ${kfmt(usage.output_tokens)} out</dd>` : ""}
    </dl>`;
    if (st.summary) html += `<p style="margin:0">${esc(st.summary)}</p>`;
    html += renderDetail(st.detail);
    const notes = events.filter((e) => e.type === "note" && e.stage === selectedNode);
    if (notes.length) html += `<h3>Latest note</h3><p class="muted" style="font-style:italic">${esc(notes[notes.length - 1].text)}</p>`;
    box.innerHTML = html;
  }

  function renderDetail(d) {
    if (!d || typeof d !== "object" || !Object.keys(d).length) return "";
    let out = "";
    if (Array.isArray(d.diagnostics)) {
      out += `<h3>Diagnostics</h3><ul class="diag">${d.diagnostics.map((x) => `
        <li class="${esc(x.severity)}"><span class="loc">${esc(x.file || "")}${x.line ? ":" + esc(x.line) : ""}${x.column ? ":" + esc(x.column) : ""} · ${esc(x.severity)}</span>${esc(x.message)}</li>`).join("")}</ul>`;
    }
    if (Array.isArray(d.tests)) {
      out += `<h3>Tests</h3><table class="tests">${d.tests.map((t) => `
        <tr><td class="${t.passed ? "p" : "f"}">${t.passed ? "✓" : "✗"}</td><td>${esc(t.name)}${t.message ? `<span class="msg">${esc(t.message)}</span>` : ""}</td></tr>`).join("")}</table>`;
    }
    if (Array.isArray(d.regions)) {
      out += `<h3>Memory</h3><div class="bars">${d.regions.map((r) => {
        const pct = r.size_bytes ? (100 * r.used_bytes) / r.size_bytes : 0;
        return `<div class="bar"><div class="row"><span>${esc(r.name)}</span><span>${kib(r.used_bytes)} / ${kib(r.size_bytes)} · ${pct.toFixed(1)}%</span></div>
          <div class="track"><div class="fill" style="width:${Math.min(100, pct).toFixed(1)}%"></div></div></div>`;
      }).join("")}</div>`;
    }
    if (Array.isArray(d.findings)) {
      out += `<h3>Findings</h3><ul class="diag">${d.findings.map((f) => `
        <li class="${esc(f.severity)}"><span class="loc">${esc(f.file || "")}${f.line ? ":" + esc(f.line) : ""} · ${esc(f.severity)}</span>${esc(f.message)}</li>`).join("")}</ul>`;
    }
    if (!out) out = `<pre>${esc(JSON.stringify(d, null, 2))}</pre>`;
    return out;
  }

  // -------------------------------------------------------------------------------------
  // Timeline
  // -------------------------------------------------------------------------------------
  let timelineFilter = null;

  function eventStage(e) {
    if (e.type === "gate_result") return (e.gate || "").toLowerCase();
    return e.stage || "";
  }

  function describe(e) {
    switch (e.type) {
      case "run_started": return `Run started: <b>${esc(e.title)}</b> on ${esc(e.board)}`;
      case "stage_started": return `Started${e.iteration ? ` (iteration ${e.iteration})` : ""}`;
      case "stage_finished": return `<span class="${e.ok ? "ok" : "bad"}">${e.ok ? "Done" : "Failed"}</span> ${esc(e.summary)}`;
      case "gate_result": return `<span class="${e.ok ? "ok" : "bad"}">${e.ok ? "PASS" : "FAIL"}</span> ${esc(e.summary)}`;
      case "tool_call": return `<span class="tool">${esc(e.tool)}(${esc(e.args)})</span>`;
      case "note": return `<span class="note">${esc(e.text)}</span>`;
      case "artifact_written": return `Wrote <code>${esc(e.path)}</code>`;
      case "run_finished": return `<span class="${e.ok ? "ok" : "bad"}">Run ${e.ok ? "passed" : "failed"}</span> ${esc(e.reason)}`;
      default: return `<code>${esc(e.type)}</code>`;
    }
  }

  function timelineRow(e, fresh) {
    const t0 = snap?.started_ts ?? events[0]?.ts ?? e.ts;
    const stage = eventStage(e);
    return `<li class="${fresh ? "fresh" : ""}" data-stage="${esc(stage)}">
      <span class="ts">+${dur(e.ts - t0)}</span><span class="who">${esc(stage ? label(stage) : "run")}</span><span class="what">${describe(e)}</span></li>`;
  }

  function renderTimeline() {
    const list = events.filter((e) => !timelineFilter || eventStage(e) === timelineFilter);
    $("timeline").innerHTML = list.map((e) => timelineRow(e, false)).join("");
    const clear = $("tl-filter-clear");
    clear.hidden = !timelineFilter;
    clear.textContent = timelineFilter ? `${label(timelineFilter)} ✕` : "";
    scrollTimeline(true);
  }

  function appendTimeline(e, fresh) {
    if (timelineFilter && eventStage(e) !== timelineFilter) return;
    const tl = $("timeline");
    const atBottom = tl.scrollHeight - tl.scrollTop - tl.clientHeight < 40;
    tl.insertAdjacentHTML("beforeend", timelineRow(e, fresh));
    if (atBottom) scrollTimeline(true);
  }

  function scrollTimeline(force) { const tl = $("timeline"); if (force) tl.scrollTop = tl.scrollHeight; }

  $("tl-filter-clear").addEventListener("click", () => { timelineFilter = null; selectedNode = null; renderPipeline(); renderInspector(); renderTimeline(); });

  // -------------------------------------------------------------------------------------
  // Costs + board
  // -------------------------------------------------------------------------------------
  function renderCosts() {
    const rows = Object.entries(snap.usage_by_stage).sort((a, b) => b[1].cost_usd - a[1].cost_usd);
    if (!rows.length) { $("costs").innerHTML = `<p class="muted" style="padding:12px 14px">No model calls yet.</p>`; return; }
    const max = rows[0][1].cost_usd || 1;
    $("costs").innerHTML = `<div class="bars">${rows.map(([k, u]) => `
      <div class="bar"><div class="row"><span>${esc(label(k))}</span><span>$${u.cost_usd.toFixed(3)}</span></div>
      <div class="track"><div class="fill" style="width:${((100 * u.cost_usd) / max).toFixed(1)}%"></div></div></div>`).join("")}</div>`;
  }

  async function loadBoards() {
    try { boards = await (await fetch("/api/boards")).json(); } catch (_) { boards = null; }
  }

  function renderBoard() {
    $("board-id").textContent = snap.board || "";
    const b = boards && boards[snap.board];
    if (!b) { $("board").innerHTML = `<p class="muted">No registry entry for this board.</p>`; return; }
    const caps = (kind) => (b.sim?.[kind] || []).map((c) => `<span class="cap ${kind}">${esc(c)}</span>`).join("");
    $("board").innerHTML = `
      <dl class="kv"><dt>Name</dt><dd>${esc(b.name)}</dd><dt>Zephyr</dt><dd class="mono">${esc(b.zephyr_board)}</dd>
      <dt>Renode</dt><dd class="mono">${esc(b.renode_platform || "none (build-only)")}</dd><dt>Console</dt><dd class="mono">${esc(b.console_uart || "–")}</dd></dl>
      <div class="caps">${caps("supported")}${caps("partial")}${caps("unsupported")}</div>`;
  }

  // -------------------------------------------------------------------------------------
  // Formatting
  // -------------------------------------------------------------------------------------
  function dur(sec) {
    if (!isFinite(sec) || sec < 0) sec = 0;
    if (sec < 60) return sec.toFixed(1) + "s";
    const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    if (m < 60) return `${m}m${String(s).padStart(2, "0")}s`;
    return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}m`;
  }
  function ago(ts) {
    if (ts == null) return "";
    const d = Date.now() / 1000 - ts;
    if (d < 5) return "now";
    if (d < 60) return Math.floor(d) + "s ago";
    if (d < 3600) return Math.floor(d / 60) + "m ago";
    if (d < 86400) return Math.floor(d / 3600) + "h ago";
    return Math.floor(d / 86400) + "d ago";
  }
  const kfmt = (n) => (n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n || 0));
  const kib = (b) => (b >= 1048576 ? (b / 1048576).toFixed(1) + " MiB" : (b / 1024).toFixed(1) + " KiB");

  // -------------------------------------------------------------------------------------
  buildPipeline();
  loadBoards().then(connectAll);
})();
