// Factory overview client. The server folds everything into one `overview` payload and
// pushes it on every change (and every few seconds); this file only renders it.
(() => {
  "use strict";

  // Old inspector links were /#<run-id>; send them to the inspector's new home.
  if (location.hash.length > 1) { location.replace("/runs" + location.hash); return; }

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STAGE_LABEL = {
    archaeologist: "Archaeologist", analyst: "Analyst", architect: "Architect",
    test_engineer: "Test Engineer", lock_tests: "Lock tests", firmware_engineer: "Firmware Eng.",
    debugger: "Debugger", test_auditor: "Test Auditor", reviewer: "Reviewer", integrator: "Integrator",
    g1: "G1 build", g2: "G2 size", g3: "G3 static", g4: "G4 ztest", g5: "G5 Renode",
  };
  const SHORT = {
    archaeologist: "Arc", analyst: "Ana", architect: "Arch", test_engineer: "TE", lock_tests: "Lock",
    firmware_engineer: "FW", g1: "G1", g2: "G2", g3: "G3", g4: "G4", g5: "G5",
    debugger: "Dbg", test_auditor: "Aud", reviewer: "Rev", integrator: "Int",
  };
  const GATES = ["g1", "g2", "g3", "g4", "g5"];
  const label = (k) => STAGE_LABEL[k] || k || "run";
  const runHref = (id) => "/runs#" + encodeURIComponent(id);

  let ov = null;
  let skew = 0;                 // server clock minus browser clock, seconds
  let seenTs = Infinity;        // newest feed ts already shown (Infinity until first render)
  let feedRun = null;
  let showTools = load("ov.tools", "1") === "1";
  const now = () => Date.now() / 1000 + skew;

  function load(k, d) { try { return localStorage.getItem(k) ?? d; } catch (_) { return d; } }
  function save(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } }

  // -------------------------------------------------------------------------------------
  // Connection
  // -------------------------------------------------------------------------------------
  function setConn(state, text) { $("conn-led").className = "led " + state; $("conn").textContent = text; }

  function connect() {
    const es = new EventSource("/api/overview/stream");
    es.addEventListener("overview", (e) => {
      ov = JSON.parse(e.data);
      skew = ov.now - Date.now() / 1000;
      setConn("live", "live");
      render();
    });
    es.onopen = () => setConn("live", "live");
    es.onerror = () => setConn("down", "disconnected, retrying…");
  }

  function render() {
    renderHero();
    renderKpis();
    renderFloor();
    renderFeed();
    renderPersonas();
    renderBoards();
    renderThroughput();
    renderGates();
    renderRecent();
  }

  // -------------------------------------------------------------------------------------
  // Hero + KPIs
  // -------------------------------------------------------------------------------------
  function nowLabel(r) {
    const k = r.active_stage;
    const nextGate = () => GATES.find((g) => !["pass", "fail"].includes(r.nodes[g])) || "g1";
    if (k && !GATES.includes(k)) return { stage: label(k), sub: "in stage" };
    if (k) {
      // Gates report only results, so after a verdict the next gate (if any) is the one running.
      const st = r.nodes[k];
      return { stage: "Gates", sub: `since ${k.toUpperCase()} ${st === "pass" ? "passed" : st === "fail" ? "failed" : "ran"}` };
    }
    if (r.mode === "gates" || r.last_stage === "firmware_engineer" || r.last_stage === "test_auditor") {
      return { stage: "Gates", sub: `running ${nextGate().toUpperCase()} · {t}` };
    }
    if (!r.last_stage) return { stage: "Starting", sub: "" };
    return { stage: "Between steps", sub: `since ${label(r.last_stage)} finished` };
  }

  function renderHero() {
    const t = ov.totals;
    const live = ov.active.filter((r) => r.liveness === "active");
    const pill = $("hero-pill");
    let title, sub, state;
    if (live.length) {
      state = "running";
      title = `${live.length} run${live.length > 1 ? "s" : ""} in progress`;
      sub = live.slice(0, 4).map((r) => `${nowLabel(r).stage} on “${r.title || r.id}” (${r.board})`).join(" · ")
        + (live.length > 4 ? ` · +${live.length - 4} more` : "")
        + (t.stalled ? ` · ${t.stalled} stalled` : "");
    } else if (t.stalled) {
      state = "stalled";
      title = `Nothing progressing: ${t.stalled} stalled run${t.stalled > 1 ? "s" : ""}`;
      sub = `No events for over ${dur(ov.stall_after_s)}. The process probably exited (crash, kill or exhausted quota) without writing run_finished.`;
    } else if (t.runs) {
      state = "idle";
      title = "Idle";
      const last = ov.recent[0];
      sub = `Last activity ${ago(t.last_activity_ts)}` + (last ? ` · “${last.title || last.id}” ${last.ok ? "passed" : "failed"} on ${last.board}` : "");
    } else {
      state = "idle";
      title = "No runs yet";
      sub = "Start one with `factory new --spec … --board …`, or `make dash-demo` to replay a recorded run.";
    }
    $("hero-title").textContent = title;
    $("hero-sub").textContent = sub;
    pill.className = "status-pill " + state;
    pill.textContent = state;
    document.title = live.length ? `(${live.length}) Factory Overview` : "Factory Overview";
  }

  function renderKpis() {
    const t = ov.totals;
    $("k-active").textContent = t.active;
    $("k-stalled").innerHTML = t.stalled ? `<span class="bad">${t.stalled}</span>` : "0";
    $("k-24h").innerHTML = `${t.passed_24h + t.failed_24h}<small><span class="ok">${t.passed_24h}✓</span> <span class="bad">${t.failed_24h}✗</span></small>`;
    $("k-rate").innerHTML = t.pipeline_finished
      ? `${Math.round((100 * t.pipeline_passed) / t.pipeline_finished)}%<small>${t.pipeline_passed}/${t.pipeline_finished}</small>` : "–";
    $("k-dur").textContent = t.median_duration_s != null ? dur(t.median_duration_s) : "–";
    $("k-cost").textContent = "$" + t.cost_24h.toFixed(2);
    $("k-tok").textContent = kfmt(t.tokens_24h);
  }

  // -------------------------------------------------------------------------------------
  // Floor: one card per unfinished run
  // -------------------------------------------------------------------------------------
  function stripKeys(r) {
    const keys = [];
    if (r.mode === "change") keys.push("archaeologist");
    if (r.mode !== "gates") keys.push("analyst", "architect", "test_engineer", "lock_tests", "firmware_engineer");
    keys.push(...GATES);
    if (r.mode !== "gates") {
      keys.push("debugger");
      if (r.nodes.test_auditor) keys.push("test_auditor");
      keys.push("reviewer", "integrator");
    }
    return keys;
  }

  function card(r) {
    const stalled = r.liveness === "stalled";
    const n = nowLabel(r);
    const since = r.stage_since ?? r.last_ts;
    const strip = stripKeys(r).map((k) => {
      const st = r.nodes[k] || "idle";
      return `<span class="cell ${st}${GATES.includes(k) ? " gate" : ""}" title="${esc(label(k))}: ${st}">${SHORT[k] || k}</span>`;
    }).join("");
    const iterPct = r.max_iterations ? Math.min(100, (100 * r.iteration) / r.max_iterations) : 0;
    const a = r.last_action;
    const mode = r.mode === "change" ? "change request" : r.mode === "gates" ? "gate check" : "new firmware";
    return `<article class="card ${stalled ? "stalled" : "active"}" data-id="${esc(r.id)}">
      <div class="card-top">
        <div class="card-title">
          <a href="${runHref(r.id)}">${esc(r.title || r.id)}</a>
          <span class="card-meta">${esc(r.board)} · ${mode} · ${esc(r.id)}</span>
        </div>
        <span class="status-pill ${stalled ? "stalled" : "running"}">${stalled ? "stalled" : "running"}</span>
      </div>
      <div class="now">
        <span class="label">${stalled ? "Last" : "Now"}</span>
        <span class="now-stage">${esc(n.stage)}</span>
        <span class="now-for" data-since="${since ?? ""}" data-sub="${esc(n.sub)}">${tickText(since, n.sub)}</span>
      </div>
      ${stalled ? `<p class="warn">Silent for <span data-ago="${r.last_ts ?? ""}">${ago(r.last_ts)}</span>, so the process has probably exited without finishing the run.</p>` : ""}
      <div class="strip" role="img" aria-label="Pipeline progress">${strip}</div>
      <div class="facts">
        <span>iter <b>${r.iteration}${r.max_iterations ? "/" + r.max_iterations : ""}</b>${r.max_iterations ? `<span class="iterbar"><i style="width:${iterPct.toFixed(0)}%"></i></span>` : ""}</span>
        <span>elapsed <b data-elapsed="${r.started_ts ?? ""}" data-end="${stalled ? r.last_ts ?? "" : ""}">${elapsedText(r.started_ts, stalled ? r.last_ts : null)}</b></span>
        <span title="API-equivalent cost">$<b>${r.cost_usd.toFixed(2)}</b></span>
        <span><b>${kfmt(r.input_tokens + r.output_tokens)}</b> tok</span>
        <span><b>${r.tool_calls}</b> tool calls</span>
      </div>
      ${a ? `<div class="last" title="${esc(a.text)}"><span class="who">${esc(label(a.stage))}</span><span class="txt">${esc(a.text) || esc(a.kind)}</span><span class="when" data-ago="${a.ts}">${ago(a.ts)}</span></div>` : ""}
    </article>`;
  }

  function renderFloor() {
    const runs = ov.active;
    const live = runs.filter((r) => r.liveness === "active").length;
    $("floor-count").textContent = runs.length ? `${live} running${runs.length > live ? ` · ${runs.length - live} stalled` : ""}` : "";
    if (!runs.length) {
      const last = ov.recent[0];
      $("floor").innerHTML = `<div class="idle-card">
        <strong>No runs in progress.</strong>
        ${last ? `<p>Most recent: <a href="${runHref(last.id)}">${esc(last.title || last.id)}</a> on ${esc(last.board)}, ${last.ok ? "passed" : "failed"} ${ago(last.finished_ts)}.</p>` : ""}
        <p>New runs appear here as soon as they write their first event to <code>runs/&lt;id&gt;/events.jsonl</code>.</p>
      </div>`;
      return;
    }
    $("floor").innerHTML = runs.map(card).join("");
  }

  function tickText(since, sub) {
    if (!since) return "";
    const t = dur(now() - since);
    return sub.includes("{t}") ? sub.replace("{t}", t) : `${t} ${sub}`.trim();
  }
  function elapsedText(start, end) { return start == null ? "–" : dur((end ?? now()) - start); }

  // -------------------------------------------------------------------------------------
  // Feed
  // -------------------------------------------------------------------------------------
  function describe(e) {
    switch (e.kind) {
      case "stage_finished": return `<span class="${e.ok ? "ok" : "bad"}">${e.ok ? "Done" : "Failed"}</span> ${esc(e.text)}`;
      case "gate_result": return `<span class="${e.ok ? "ok" : "bad"}">${e.ok ? "PASS" : "FAIL"}</span> ${esc(e.text)}`;
      case "run_finished": return `<span class="${e.ok ? "ok" : "bad"}">Run ${e.ok ? "passed" : "failed"}</span> ${esc(e.text)}`;
      case "tool_call": return `<span class="tool">${esc(e.text)}</span>`;
      case "note": return `<span class="note">${esc(e.text)}</span>`;
      case "run_started": return `<b>${esc(e.text)}</b>`;
      default: return esc(e.text);
    }
  }

  function renderFeed() {
    let items = ov.feed;
    if (!showTools) items = items.filter((e) => e.kind !== "tool_call");
    if (feedRun) items = items.filter((e) => e.run_id === feedRun);
    const chip = $("feed-run");
    chip.hidden = !feedRun;
    if (feedRun) {
      const r = ov.feed.find((e) => e.run_id === feedRun);
      chip.textContent = `${r ? r.title || feedRun : feedRun} ✕`;
    }
    const prevSeen = seenTs;
    $("feed").innerHTML = items.length ? items.map((e) => `
      <li class="${e.ts > prevSeen ? "fresh" : ""}">
        <span class="ts" title="${esc(new Date(e.ts * 1000).toLocaleString())}">${clock(e.ts)}</span>
        <button type="button" class="run" data-run="${esc(e.run_id)}" title="${esc(e.title)} · ${esc(e.board)}">${esc(e.title || e.run_id)}</button>
        <span class="who">${esc(label(e.stage))}</span>
        <span class="what">${describe(e)}</span>
      </li>`).join("") : `<li><span class="empty-row">Nothing to show.</span></li>`;
    if (ov.feed.length) seenTs = Math.max(...ov.feed.map((e) => e.ts));
    else seenTs = 0;
  }

  $("feed").addEventListener("click", (e) => {
    const b = e.target.closest(".run");
    if (!b) return;
    feedRun = feedRun === b.dataset.run ? null : b.dataset.run;
    renderFeed();
  });
  $("feed-run").addEventListener("click", () => { feedRun = null; renderFeed(); });
  $("feed-tools").checked = showTools;
  $("feed-tools").addEventListener("change", (e) => { showTools = e.target.checked; save("ov.tools", showTools ? "1" : "0"); renderFeed(); });

  // -------------------------------------------------------------------------------------
  // Personas + boards
  // -------------------------------------------------------------------------------------
  function renderPersonas() {
    const rows = ov.personas;
    if (!rows.length) { $("personas").innerHTML = `<tr><td class="muted">No persona sessions yet.</td></tr>`; return; }
    const max = Math.max(...rows.map((p) => p.cost_usd), 1e-9);
    $("personas").innerHTML = `<thead><tr><th>Persona</th><th>Now</th><th class="num">Sessions</th><th class="num">Failed</th><th class="num">Time</th><th class="num" title="API-equivalent cost">Cost</th></tr></thead>
      <tbody>${rows.map((p) => `<tr class="${p.busy_now ? "is-busy" : ""}">
        <td>${esc(label(p.stage))}</td>
        <td>${p.busy_now ? `<span class="busy"><span class="dot active"></span>${p.busy_now}</span>` : `<span class="muted">–</span>`}</td>
        <td class="num">${p.sessions}</td>
        <td class="num">${p.fails || "–"}</td>
        <td class="num">${p.secs ? dur(p.secs) : "–"}</td>
        <td class="num"><span class="costcell"><span>$${p.cost_usd.toFixed(2)}</span><span class="track"><span class="fill" style="display:block;width:${((100 * p.cost_usd) / max).toFixed(1)}%"></span></span></span></td>
      </tr>`).join("")}</tbody>`;
  }

  function renderBoards() {
    const rows = [...ov.boards].sort((a, b) => (b.active + b.stalled) - (a.active + a.stalled) || b.runs - a.runs);
    $("boards").innerHTML = rows.map((b) => {
      const busy = ov.active.filter((r) => r.board === b.board);
      let sub;
      if (busy.length) {
        sub = busy.map((r) => `<span class="dot ${r.liveness}" style="display:inline-block"></span> <a href="${runHref(r.id)}">${esc(r.title || r.id)}</a>: ${esc(nowLabel(r).stage)}${r.liveness === "stalled" ? " (stalled)" : ""}`).join(" · ");
      } else if (b.last) {
        sub = `last: <a href="${runHref(b.last.id)}">${esc(b.last.title || b.last.id)}</a> ${b.last.liveness === "passed" ? "passed" : "failed"} <span data-ago="${b.last.ts ?? ""}">${ago(b.last.ts)}</span>`;
      } else {
        sub = "no runs yet";
      }
      const dot = busy.some((r) => r.liveness === "active") ? "active" : busy.length ? "stalled" : "idle";
      return `<li>
        <span class="bname"><span class="dot ${dot}"></span><span>${esc(b.name)}</span><span class="tag">${b.simulated ? "Renode" : "build-only"}</span></span>
        <span class="tally">${b.runs} runs · <span class="ok">${b.passed}✓</span> <span class="bad">${b.failed}✗</span></span>
        <span class="bsub">${sub}</span>
      </li>`;
    }).join("") || `<li class="muted">No boards registered.</li>`;
  }

  // -------------------------------------------------------------------------------------
  // Throughput chart: pipeline runs finished per local day, last 14 days, stacked pass/fail
  // -------------------------------------------------------------------------------------
  const DAYS = 14;
  let thruDays = [];

  function dayKey(d) { return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; }

  function renderThroughput() {
    const today = new Date(now() * 1000); today.setHours(0, 0, 0, 0);
    thruDays = [];
    const idx = new Map();
    for (let i = DAYS - 1; i >= 0; i--) {
      const d = new Date(today); d.setDate(today.getDate() - i);
      idx.set(dayKey(d), thruDays.length);
      thruDays.push({ date: d, ok: 0, fail: 0 });
    }
    for (const o of ov.outcomes) {
      if (o.mode === "gates") continue;
      const i = idx.get(dayKey(new Date(o.ts * 1000)));
      if (i != null) thruDays[i][o.ok ? "ok" : "fail"]++;
    }
    drawThroughput();
    $("thru-table").innerHTML = `<thead><tr><th>Day</th><th class="num">Passed</th><th class="num">Failed</th></tr></thead><tbody>${
      thruDays.filter((d) => d.ok || d.fail).map((d) => `<tr><td>${d.date.toLocaleDateString(undefined, { month: "short", day: "numeric" })}</td><td class="num">${d.ok}</td><td class="num">${d.fail}</td></tr>`).join("")
      || `<tr><td colspan="3" class="muted">No finished pipeline runs in the last ${DAYS} days.</td></tr>`}</tbody>`;
  }

  function drawThroughput() {
    const svg = $("thru");
    const W = Math.max(280, svg.clientWidth || 600), H = 200;
    const m = { l: 26, r: 4, t: 8, b: 22 };
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    const maxV = Math.max(1, ...thruDays.map((d) => d.ok + d.fail));
    const step = maxV <= 5 ? 1 : Math.ceil(maxV / 4);
    const top = Math.ceil(maxV / step) * step;
    const y = (v) => m.t + ph - (v / top) * ph;
    const slot = pw / DAYS, bw = Math.min(28, slot * 0.62);
    let out = "";
    for (let v = 0; v <= top; v += step) {
      out += `<line class="${v ? "grid-line" : "base"}" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>`;
      out += `<text x="${m.l - 6}" y="${y(v) + 3.5}" text-anchor="end">${v}</text>`;
    }
    thruDays.forEach((d, i) => {
      const x = m.l + i * slot + (slot - bw) / 2;
      out += `<rect class="hit" data-i="${i}" x="${m.l + i * slot}" y="${m.t}" width="${slot}" height="${ph}"/>`;
      const GAP = 2;
      const okH = (d.ok / top) * ph, failH = (d.fail / top) * ph;
      // Only the top end of the stack is rounded; the baseline end stays square.
      const segs = [];
      if (d.ok) segs.push({ cls: "seg-ok", y0: y(0), h: okH });
      if (d.fail) segs.push({ cls: "seg-fail", y0: y(0) - okH, h: failH });
      segs.forEach((s, j) => {
        const isTop = j === segs.length - 1;
        const gap = j > 0 ? GAP : 0;
        const yb = s.y0 - gap, h = Math.max(1, s.h - gap);
        out += isTop ? `<path class="${s.cls}" pointer-events="none" d="${roundTop(x, yb - h, bw, h, Math.min(4, h, bw / 2))}"/>`
          : `<rect class="${s.cls}" pointer-events="none" x="${x}" y="${yb - h}" width="${bw}" height="${h}"/>`;
      });
      const lastDay = i === DAYS - 1;
      if (lastDay || i % 2 === (DAYS - 1) % 2) {
        const txt = lastDay ? "today" : d.date.getDate();
        out += `<text x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${txt}</text>`;
      }
    });
    svg.innerHTML = out;
  }

  function roundTop(x, y, w, h, r) {
    return `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z`;
  }

  $("thru").addEventListener("mousemove", (e) => {
    const hit = e.target.closest(".hit");
    const tip = $("thru-tip");
    document.querySelectorAll("#thru .hit.on").forEach((h) => h !== hit && h.classList.remove("on"));
    if (!hit) { tip.hidden = true; return; }
    hit.classList.add("on");
    const d = thruDays[+hit.dataset.i];
    const wrap = $("thru-wrap").getBoundingClientRect();
    const r = hit.getBoundingClientRect();
    tip.innerHTML = `${d.date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}<br><b>${d.ok}</b> passed · <b>${d.fail}</b> failed`;
    tip.style.left = `${Math.min(Math.max(r.left + r.width / 2 - wrap.left, 70), wrap.width - 70)}px`;
    tip.style.top = `${r.top - wrap.top + 8}px`;
    tip.hidden = false;
  });
  $("thru").addEventListener("mouseleave", () => {
    $("thru-tip").hidden = true;
    document.querySelectorAll("#thru .hit.on").forEach((h) => h.classList.remove("on"));
  });
  let resizeT;
  window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => ov && drawThroughput(), 100); });

  // -------------------------------------------------------------------------------------
  // Gates + recent
  // -------------------------------------------------------------------------------------
  function renderGates() {
    $("gates").innerHTML = ov.gates.map((g) => {
      const pct = g.runs ? (100 * g.first_try_pass) / g.runs : 0;
      return `<div class="bar">
        <div class="row"><span>${esc(label(g.gate))}</span><span>${g.runs ? `${pct.toFixed(0)}%` : "–"}</span></div>
        <div class="track"><div class="fill" style="width:${pct.toFixed(1)}%"></div></div>
        <div class="row gsub"><span>${g.first_try_pass}/${g.runs} runs first try</span><span>${g.fails} fail${g.fails === 1 ? "" : "s"} in ${g.evals} evals</span></div>
      </div>`;
    }).join("");
  }

  function renderRecent() {
    const rows = ov.recent;
    if (!rows.length) { $("recent").innerHTML = `<tr><td class="muted">No finished runs yet.</td></tr>`; return; }
    $("recent").innerHTML = `<thead><tr><th>Result</th><th>Run</th><th>Board</th><th>Mode</th><th class="num">Iter</th><th class="num">Took</th><th class="num" title="API-equivalent cost">Cost</th><th class="num">Finished</th><th>Reason</th></tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td><span class="st"><span class="dot ${r.ok ? "passed" : "failed"}"></span>${r.ok ? "pass" : "fail"}</span></td>
        <td><a href="${runHref(r.id)}" title="${esc(r.id)}">${esc(r.title || r.id)}</a></td>
        <td class="mono">${esc(r.board)}</td>
        <td>${esc(r.mode)}</td>
        <td class="num">${r.iteration}</td>
        <td class="num">${r.started_ts != null && r.finished_ts != null ? dur(r.finished_ts - r.started_ts) : "–"}</td>
        <td class="num">$${r.cost_usd.toFixed(2)}</td>
        <td class="num" data-ago="${r.finished_ts ?? ""}">${ago(r.finished_ts)}</td>
        <td class="reason" title="${esc(r.reason)}">${esc(r.reason)}</td>
      </tr>`).join("")}</tbody>`;
  }

  // -------------------------------------------------------------------------------------
  // Clocks: relative times tick locally between server pushes
  // -------------------------------------------------------------------------------------
  setInterval(() => {
    $("clock").textContent = new Date(now() * 1000).toLocaleTimeString();
    if (!ov) return;
    document.querySelectorAll("[data-since]").forEach((el) => {
      const s = parseFloat(el.dataset.since);
      if (isFinite(s)) el.textContent = tickText(s, el.dataset.sub);
    });
    document.querySelectorAll("[data-elapsed]").forEach((el) => {
      const s = parseFloat(el.dataset.elapsed), end = parseFloat(el.dataset.end);
      if (isFinite(s)) el.textContent = elapsedText(s, isFinite(end) ? end : null);
    });
    document.querySelectorAll("[data-ago]").forEach((el) => {
      const s = parseFloat(el.dataset.ago);
      if (isFinite(s)) el.textContent = ago(s);
    });
  }, 1000);

  // -------------------------------------------------------------------------------------
  // Formatting
  // -------------------------------------------------------------------------------------
  function dur(sec) {
    if (!isFinite(sec) || sec < 0) sec = 0;
    if (sec < 60) return Math.floor(sec) + "s";
    const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    if (m < 60) return `${m}m${String(s).padStart(2, "0")}s`;
    const h = Math.floor(m / 60);
    if (h < 48) return `${h}h${String(m % 60).padStart(2, "0")}m`;
    return `${Math.floor(h / 24)}d${String(h % 24).padStart(2, "0")}h`;
  }
  function ago(ts) {
    if (ts == null || !isFinite(ts)) return "";
    const d = now() - ts;
    if (d < 5) return "just now";
    if (d < 60) return Math.floor(d) + "s ago";
    if (d < 3600) return Math.floor(d / 60) + "m ago";
    if (d < 86400) return Math.floor(d / 3600) + "h ago";
    return Math.floor(d / 86400) + "d ago";
  }
  function clock(ts) {
    const d = new Date(ts * 1000);
    const sameDay = new Date(now() * 1000).toDateString() === d.toDateString();
    return sameDay ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })
      : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  const kfmt = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n || 0));

  connect();
})();
