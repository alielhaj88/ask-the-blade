(() => {
  "use strict";

  // ---------- elements ----------
  const $ = (s) => document.querySelector(s);
  const els = {
    sidebar: $("#sidebar"), scrim: $("#scrim"), menuBtn: $("#menuBtn"),
    newChat: $("#newChat"), history: $("#history"),
    spend: $("#spend"), spendBar: $("#spendBar"), spendCap: $("#spendCap"),
    chatTitle: $("#chatTitle"), modeChip: $("#modeChip"), themeBtn: $("#themeBtn"),
    thread: $("#thread"), empty: $("#empty"), messages: $("#messages"), examples: $("#examples"),
    composer: $("#composer"), input: $("#input"), sendBtn: $("#sendBtn"),
  };

  // ---------- solver vocabulary ----------
  const TOOL_META = {
    design_blade: { label: "Design blade" },
    sweep_blade_count: { label: "Blade count sweep", key: "num_blades" },
    sweep_tsr: { label: "Tip-speed ratio sweep", key: "lambda_design" },
    sweep_aoa: { label: "Angle of attack sweep", key: "alpha_design_deg" },
    sweep_chord_cap: { label: "Chord cap sweep", key: "chord_cap_m" },
  };
  const PARAM = {
    lambda_design: { axis: "Tip-speed ratio", name: "λ", fmt: (v) => `λ ${v}` },
    alpha_design_deg: { axis: "Angle of attack", name: "α", unit: "°", fmt: (v) => `α ${v}°` },
    num_blades: { axis: "Blade count", name: "B", fmt: (v) => `${v} blade${Number(v) === 1 ? "" : "s"}` },
    chord_cap_m: { axis: "Chord cap", name: "cap", unit: " m", fmt: (v) => `cap ${v} m` },
  };
  const EXAMPLES = [
    { tag: "Single design", text: "What Cp and thrust do I get at tip-speed ratio 7, 6° angle of attack, 3 blades and a 2.0 m chord cap?" },
    { tag: "Blade count", text: "At tip-speed ratio 7, 6° angle of attack and a 2.0 m chord cap, which blade count gives the best Cp?" },
    { tag: "Sensitivity", text: "At tip-speed ratio 7 with 3 blades, 6° and a 2.0 m cap, which matters more for Cp: the angle of attack or the chord cap?" },
    { tag: "Tip-speed ratio", text: "With 3 blades, 6° angle of attack and a 2.0 m chord cap, which tip-speed ratio is best?" },
  ];

  // Baseline blade (λ 7, α 6°, 3 blades, 2.0 m cap) from bem_core, used to draw the rotor mark.
  const BASE = {
    r: [2.9, 4.7, 6.5, 8.3, 10.1, 11.9, 13.7, 15.5, 17.3, 19.1],
    c: [2.0, 2.0, 2.0, 2.0, 1.675, 1.429, 1.245, 1.103, 0.99, 0.898],
  };

  // ---------- small utils ----------
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  const fmtCp = (v) => Number(v).toFixed(4);
  const fmtNum = (v) => (Number.isInteger(Number(v)) ? String(v) : String(Number(v)));
  const inputsText = (input) =>
    Object.keys(PARAM).filter((k) => input && k in input).map((k) => PARAM[k].fmt(input[k])).join("  ·  ");

  // ---------- storage (per-browser, optional) ----------
  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage unavailable */ }
    },
  };

  // ---------- rotor mark ----------
  function bladePath(k = 2.1) {
    const rs = [2, ...BASE.r, 20];
    const cs = [BASE.c[0], ...BASE.c, BASE.c[BASE.c.length - 1] * 0.55];
    const le = rs.map((r, i) => [r, -0.28 * cs[i] * k]);
    const te = rs.map((r, i) => [r, 0.72 * cs[i] * k]).reverse();
    return "M" + [...le, ...te].map((p) => `${p[0].toFixed(2)} ${p[1].toFixed(2)}`).join(" L") + " Z";
  }
  const BLADE_D = bladePath();
  function rotorMarkup({ spin = true, sweep = true } = {}) {
    const blades = [0, 120, 240].map((a) => `<path class="blade" d="${BLADE_D}" transform="rotate(${a})"/>`).join("");
    return `${sweep ? '<circle class="sweep" r="21.6"/>' : ""}<g${spin ? ' class="spin"' : ""}>${blades}<circle class="hub" r="2.5"/><circle class="hub-dot" r=".9"/></g>`;
  }
  document.querySelector(".rotor-hero").innerHTML = rotorMarkup();
  document.querySelector(".brand .rotor").innerHTML = rotorMarkup({ sweep: false });

  const motion = { angle: 0, speed: 10, target: 10, last: 0 };
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function tick(t) {
    const dt = Math.min(0.05, (t - (motion.last || t)) / 1000);
    motion.last = t;
    motion.speed += (motion.target - motion.speed) * Math.min(1, dt * 2.2);
    motion.angle = (motion.angle + motion.speed * dt) % 360;
    const transform = `rotate(${motion.angle.toFixed(2)})`;
    document.querySelectorAll("svg.rotor g.spin").forEach((g) => g.setAttribute("transform", transform));
    requestAnimationFrame(tick);
  }
  if (!reducedMotion) requestAnimationFrame(tick);

  // ---------- markdown (small, safe subset) ----------
  function wrapNumbers(html) {
    return html.split(/(<[^>]+>)/).map((part) =>
      part.startsWith("<") ? part : part.replace(/(?<![\w.])(\d(?:[\d,]*\d)?(?:\.\d+)?)(?![\w])/g, '<span class="num">$1</span>')
    ).join("");
  }
  function inline(text) {
    let s = esc(text);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>");
    return wrapNumbers(s);
  }
  function mdTable(rows) {
    const cells = (r) => r.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
    const body = rows.filter((r) => r.replace(/[|\s:-]/g, "") !== "");
    if (!body.length) return "";
    const [head, ...rest] = body;
    return `<table><thead><tr>${cells(head).map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${
      rest.map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")
    }</tbody></table>`;
  }
  function md(text) {
    const lines = String(text || "").replace(/\r/g, "").split("\n");
    const out = [];
    const isTable = (l) => /^\s*\|.*\|\s*$/.test(l);
    const isUl = (l) => /^\s*[-*•]\s+/.test(l);
    const isOl = (l) => /^\s*\d+[.)]\s+/.test(l);
    const isHead = (l) => /^\s*#{1,4}\s+/.test(l);
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      if (isTable(line)) {
        const rows = [];
        while (i < lines.length && isTable(lines[i])) rows.push(lines[i++]);
        out.push(mdTable(rows));
      } else if (isUl(line)) {
        const items = [];
        while (i < lines.length && isUl(lines[i])) items.push(lines[i++].replace(/^\s*[-*•]\s+/, ""));
        out.push(`<ul>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</ul>`);
      } else if (isOl(line)) {
        const items = [];
        while (i < lines.length && isOl(lines[i])) items.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ""));
        out.push(`<ol>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</ol>`);
      } else if (isHead(line)) {
        out.push(`<h4>${inline(line.replace(/^\s*#{1,4}\s+/, ""))}</h4>`);
        i++;
      } else {
        const para = [];
        while (i < lines.length && lines[i].trim() && !isTable(lines[i]) && !isUl(lines[i]) && !isOl(lines[i]) && !isHead(lines[i])) {
          para.push(lines[i++]);
        }
        out.push(`<p>${para.map(inline).join("<br>")}</p>`);
      }
    }
    return out.join("");
  }

  // ---------- charts (hand-drawn SVG) ----------
  function niceRange(values, padFrac = 0.22, minSpan = 0.004) {
    let lo = Math.min(...values), hi = Math.max(...values);
    let span = Math.max(hi - lo, minSpan);
    const mid = (hi + lo) / 2;
    if (hi - lo < minSpan) { lo = mid - span / 2; hi = mid + span / 2; }
    return [lo - span * padFrac, hi + span * padFrac];
  }

  let gradId = 0;
  function sweepChart(data, W) {
    const H = 196, pl = 50, pr = 18, pt = 24, pb = 30, inset = 26;
    const xs = data.xs, ys = data.ys;
    const [ylo, yhi] = niceRange(ys);
    const xmin = Math.min(...xs), xmax = Math.max(...xs);
    const x = (v) => pl + inset + (xmax === xmin ? 0.5 : (v - xmin) / (xmax - xmin)) * (W - pl - pr - 2 * inset);
    const gid = `fade${++gradId}`;
    const y = (v) => pt + (1 - (v - ylo) / (yhi - ylo)) * (H - pt - pb);
    const compact = W < 420;

    let g = "";
    for (let k = 0; k <= 3; k++) {
      const v = ylo + ((yhi - ylo) * k) / 3;
      g += `<line class="grid" x1="${pl}" x2="${W - pr}" y1="${y(v)}" y2="${y(v)}"/>`;
      g += `<text class="axis-label" x="${pl - 8}" y="${y(v) + 3.5}" text-anchor="end">${v.toFixed(3)}</text>`;
    }
    xs.forEach((v) => {
      g += `<text class="axis-label" x="${x(v)}" y="${H - 9}" text-anchor="middle">${esc(fmtNum(v))}${esc(data.unit || "")}</text>`;
    });
    const pts = xs.map((v, i) => [x(v), y(ys[i])]);
    const line = "M" + pts.map((p) => p.join(" ")).join(" L");
    const area = `${line} L${pts[pts.length - 1][0]} ${H - pb} L${pts[0][0]} ${H - pb} Z`;
    g = `<defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--signal)" stop-opacity=".22"/><stop offset="1" stop-color="var(--signal)" stop-opacity="0"/></linearGradient></defs>` + g;
    g += `<path d="${area}" fill="url(#${gid})"/><path class="line" d="${line}"/>`;
    pts.forEach((p, i) => {
      const best = i === data.best, nc = data.conv && data.conv[i] === false;
      g += `<circle class="pt${best ? " best" : ""}${nc ? " nc" : ""}" cx="${p[0]}" cy="${p[1]}" r="${best ? 4.6 : 3.6}"><title>${esc(fmtNum(xs[i]))}: Cp ${fmtCp(ys[i])}${nc ? " (not converged)" : ""}</title></circle>`;
      if (!compact || best) {
        g += `<text class="pt-label${best ? " best" : ""}" x="${p[0]}" y="${p[1] - 10}" text-anchor="middle">${fmtCp(ys[i])}</text>`;
      }
    });
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Cp against ${esc(data.axis)}">${g}</svg>`;
  }

  function planformChart(out, W) {
    const H = 132, pl = 18, pr = 14, pt = 10, pb = 26;
    const r = [2, ...out.radius_m, 20];
    const c = [out.chord_m[0], ...out.chord_m, out.chord_m[out.chord_m.length - 1] * 0.6];
    const cmax = Math.max(...c);
    const x = (v) => pl + (v / 20) * (W - pl - pr);
    const ky = (H - pt - pb) / cmax;
    const y0 = pt + 0.28 * cmax * ky;
    const le = r.map((rv, i) => [x(rv), y0 - 0.28 * c[i] * ky]);
    const te = r.map((rv, i) => [x(rv), y0 + 0.72 * c[i] * ky]).reverse();
    let g = "";
    out.radius_m.forEach((rv) => { g += `<line class="ring" x1="${x(rv)}" x2="${x(rv)}" y1="${pt}" y2="${H - pb}"/>`; });
    g += `<path class="planform" d="M${[...le, ...te].map((p) => p.join(" ")).join(" L")} Z"/>`;
    g += `<line class="pitch-axis" x1="${x(0)}" x2="${x(20)}" y1="${y0}" y2="${y0}"/>`;
    [0, 5, 10, 15, 20].forEach((v) => {
      g += `<text class="axis-label" x="${x(v)}" y="${H - 8}" text-anchor="middle">${v} m</text>`;
    });
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Blade planform, chord along the span">${g}</svg>`;
  }

  function twistChart(out, W) {
    const H = 132, pl = 44, pr = 14, pt = 12, pb = 26;
    const xs = out.radius_m, ys = out.twist_deg;
    const [ylo, yhi] = niceRange(ys, 0.12, 1);
    const x = (v) => pl + (v / 20) * (W - pl - pr);
    const y = (v) => pt + (1 - (v - ylo) / (yhi - ylo)) * (H - pt - pb);
    let g = "";
    for (let k = 0; k <= 2; k++) {
      const v = ylo + ((yhi - ylo) * k) / 2;
      g += `<line class="grid" x1="${pl}" x2="${W - pr}" y1="${y(v)}" y2="${y(v)}"/>`;
      g += `<text class="axis-label" x="${pl - 8}" y="${y(v) + 3.5}" text-anchor="end">${v.toFixed(0)}°</text>`;
    }
    [0, 5, 10, 15, 20].forEach((v) => {
      g += `<text class="axis-label" x="${x(v)}" y="${H - 8}" text-anchor="middle">${v} m</text>`;
    });
    const d = "M" + xs.map((v, i) => `${x(v)} ${y(ys[i])}`).join(" L");
    g += `<path class="twist" d="${d}"/>`;
    xs.forEach((v, i) => { g += `<circle cx="${x(v)}" cy="${y(ys[i])}" r="2.4" fill="var(--cool)"><title>r ${v} m: ${ys[i]}°</title></circle>`; });
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Twist along the span">${g}</svg>`;
  }

  // Charts are drawn at the container's real width so text stays a constant size.
  const chartData = new WeakMap();
  function chartHost(kind, data) {
    const host = document.createElement("div");
    host.className = "chart-host";
    chartData.set(host, { kind, data });
    return host;
  }
  function drawChart(host) {
    const entry = chartData.get(host);
    if (!entry) return;
    const W = Math.max(260, Math.round(host.clientWidth || 600));
    if (host.dataset.w === String(W)) return;
    host.dataset.w = String(W);
    host.innerHTML =
      entry.kind === "sweep" ? sweepChart(entry.data, W) :
      entry.kind === "planform" ? planformChart(entry.data, W) :
      twistChart(entry.data, W);
  }
  function drawCharts(root) { root.querySelectorAll(".chart-host").forEach(drawChart); }
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => drawCharts(document), 120);
  });

  // ---------- run cards ----------
  const ICON_OK = '<svg viewBox="0 0 16 16"><path class="ok" d="M3.5 8.5 6.5 11.5 12.5 4.5"/></svg>';
  const ICON_BAD = '<svg viewBox="0 0 16 16"><path class="bad" d="M4.5 4.5l7 7M11.5 4.5l-7 7"/></svg>';
  const ICON_STOP = '<svg viewBox="0 0 16 16"><path class="bad" d="M4 8h8"/></svg>';
  const CHEVRON = '<svg class="run-chev" viewBox="0 0 16 16"><path d="M4 6l4 4 4-4"/></svg>';

  function readout(label, value, cls = "") {
    return `<div class="readout"><dt>${label}</dt><dd class="${cls}">${value}</dd></div>`;
  }

  function runBody(tool) {
    const body = document.createElement("div");
    body.className = "run-body";
    const out = tool.output;

    if (tool.status === "running") return body;
    if (tool.status === "stopped") {
      body.innerHTML = `<div class="run-error">Stopped before the solver finished.</div>`;
      return body;
    }
    if (tool.is_error || typeof out !== "object" || out === null) {
      body.innerHTML = `<div class="run-error">${esc(typeof out === "string" ? out : "The tool returned no result.")}</div>`;
      return body;
    }

    if (tool.name === "design_blade") {
      const thrust = Number(out.thrust_N);
      body.innerHTML =
        `<dl class="readouts">` +
        readout("Cp", fmtCp(out.cp)) +
        readout("Thrust", `${(thrust / 1000).toFixed(1)}<small>kN</small>`) +
        readout("Rings converged", out.all_rings_converged ? "All 10" : "Not all", out.all_rings_converged ? "good" : "warn") +
        `</dl>`;
      const t1 = document.createElement("div");
      t1.className = "chart-title";
      t1.innerHTML = `<span>Planform, chord along the span</span><span class="mono">${out.chord_m[0]} m → ${out.chord_m[out.chord_m.length - 1]} m</span>`;
      body.append(t1, chartHost("planform", out));
      const t2 = document.createElement("div");
      t2.className = "chart-title";
      t2.innerHTML = `<span>Twist along the span</span><span class="mono">${out.twist_deg[0]}° → ${out.twist_deg[out.twist_deg.length - 1]}°</span>`;
      body.append(t2, chartHost("twist", out));
    } else {
      const key = TOOL_META[tool.name] && TOOL_META[tool.name].key;
      const xs = key && out[key];
      if (Array.isArray(xs) && Array.isArray(out.cp)) {
        const p = PARAM[key];
        const bestV = out["best_" + key];
        const t = document.createElement("div");
        t.className = "chart-title";
        t.innerHTML = `<span>Cp against ${esc(p.axis.toLowerCase())}</span><span class="mono">${xs.length} designs</span>`;
        body.append(t, chartHost("sweep", {
          xs, ys: out.cp, conv: out.all_rings_converged, best: xs.indexOf(bestV), axis: p.axis, unit: p.unit || "",
        }));
        const nc = (out.all_rings_converged || []).filter((v) => v === false).length;
        const line = document.createElement("div");
        line.className = "best-line";
        line.innerHTML =
          `<span>Best of the values tested <b>${esc(p.fmt(bestV))}</b></span>` +
          `<span>Cp <b>${fmtCp(out.best_cp)}</b></span>` +
          (out.cp_spread != null ? `<span>Spread <b>${fmtCp(out.cp_spread)}</b></span>` : "") +
          (nc ? `<span>${nc} design${nc === 1 ? "" : "s"} did not converge (dashed)</span>` : "");
        body.append(line);
      }
    }

    const raw = document.createElement("details");
    raw.className = "raw";
    raw.innerHTML = `<summary>Raw tool output</summary><pre>${esc(JSON.stringify(out, null, 2))}</pre>`;
    body.append(raw);
    return body;
  }

  function runSummary(tool) {
    const out = tool.output;
    if (!out || typeof out !== "object") return "";
    if (tool.name === "design_blade") return `Cp ${fmtCp(out.cp)} · ${(Number(out.thrust_N) / 1000).toFixed(1)} kN`;
    const key = TOOL_META[tool.name] && TOOL_META[tool.name].key;
    if (!key || out["best_" + key] == null) return "";
    return `best ${PARAM[key].fmt(out["best_" + key])} · Cp ${fmtCp(out.best_cp)}`;
  }

  function runCard(tool) {
    const meta = TOOL_META[tool.name] || { label: tool.name };
    const card = document.createElement("div");
    const failed = tool.status === "error" || tool.status === "stopped";
    card.className = `run${failed ? " error" : ""}${tool.open && tool.status !== "running" ? " open" : ""}`;
    const state =
      tool.status === "running" ? '<span class="spinner" aria-label="Running"></span>' :
      tool.status === "stopped" ? ICON_STOP : failed ? ICON_BAD : ICON_OK;
    const head = document.createElement("button");
    head.type = "button";
    head.className = "run-head";
    head.setAttribute("aria-expanded", String(card.classList.contains("open")));
    head.innerHTML =
      `<span class="run-state">${state}</span>` +
      `<span class="run-name">${esc(meta.label)}</span>` +
      `<span class="run-inputs">${esc(inputsText(tool.input))}</span>` +
      (tool.status === "done" ? `<span class="run-result">${esc(runSummary(tool))}</span>` : "") +
      (tool.status === "running" ? "" : CHEVRON);
    head.addEventListener("click", () => {
      if (tool.status === "running") return;
      tool.open = !tool.open;
      tool.touched = true;
      card.classList.toggle("open", tool.open);
      head.setAttribute("aria-expanded", String(tool.open));
      if (tool.open) drawCharts(card);
      saveChats();
    });
    card.append(head, runBody(tool));
    return card;
  }

  // ---------- message rendering ----------
  function userEl(turn) {
    const el = document.createElement("div");
    el.className = "msg-user";
    el.dataset.turn = turn.id;
    el.textContent = turn.text;
    return el;
  }

  function assistantEl(turn) {
    const el = document.createElement("div");
    el.className = "msg-ai";
    el.dataset.turn = turn.id;

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.innerHTML = `<svg class="rotor" viewBox="-24 -24 48 48" aria-hidden="true">${rotorMarkup({ spin: !!turn.pending, sweep: false })}</svg>`;

    const body = document.createElement("div");
    body.className = "msg-body";

    if (turn.tools && turn.tools.length) {
      const runs = document.createElement("div");
      runs.className = "runs";
      turn.tools.forEach((t) => runs.append(runCard(t)));
      body.append(runs);
    }

    if (turn.pending && !turn.text && !turn.error) {
      const running = (turn.tools || []).some((t) => t.status === "running");
      const status = document.createElement("div");
      status.className = "status-line";
      status.innerHTML = `<span class="dots"><i></i><i></i><i></i></span>${
        running ? "Running the solver" : (turn.tools || []).length ? "Writing the answer" : "Choosing a solver run"
      }`;
      body.append(status);
    }

    if (turn.text) {
      const answer = document.createElement("div");
      answer.className = "answer";
      answer.innerHTML = md(turn.text);
      body.append(answer);
    }

    if (turn.error) {
      const notice = document.createElement("div");
      notice.className = "notice";
      notice.innerHTML = `<b>No answer.</b>${esc(turn.error)}`;
      body.append(notice);
    }

    if (!turn.pending) {
      const n = (turn.tools || []).length;
      const parts = [`${n} solver run${n === 1 ? "" : "s"}`];
      if (turn.mock) parts.push("mock, no API cost");
      else parts.push(`$${(turn.cost || 0).toFixed(4)}`);
      if (turn.seconds != null) parts.push(`${turn.seconds} s`);
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.innerHTML = parts.map((p) => `<span>${esc(p)}</span>`).join("");
      body.append(meta);
    }

    el.append(avatar, body);
    return el;
  }

  function nearBottom() {
    const t = els.thread;
    return t.scrollHeight - t.scrollTop - t.clientHeight < 140;
  }
  function scrollToBottom() { els.thread.scrollTop = els.thread.scrollHeight; }

  function renderThread() {
    els.messages.innerHTML = "";
    const turns = current ? current.turns : [];
    els.empty.hidden = turns.length > 0;
    turns.forEach((t) => els.messages.append(t.role === "user" ? userEl(t) : assistantEl(t)));
    drawCharts(els.messages);
    els.chatTitle.textContent = current ? current.title : "New chat";
    if (turns.length) scrollToBottom(); else els.thread.scrollTop = 0;
  }

  function updateTurn(turn) {
    const stick = nearBottom();
    const old = els.messages.querySelector(`[data-turn="${turn.id}"]`);
    const fresh = assistantEl(turn);
    if (old) old.replaceWith(fresh); else els.messages.append(fresh);
    drawCharts(fresh);
    if (stick) scrollToBottom();
  }

  // ---------- chats ----------
  let chats = store.get("atb.chats", []);
  if (!Array.isArray(chats)) chats = [];
  chats.forEach((c) => (c.turns || []).forEach((t) => {
    if (t.pending) { t.pending = false; t.error = t.error || "Interrupted before the answer finished."; }
    (t.tools || []).forEach((tool) => { if (tool.status === "running") tool.status = "stopped"; });
  }));
  let current = null;

  function saveChats() {
    chats.sort((a, b) => b.updated - a.updated);
    store.set("atb.chats", chats.slice(0, 40));
  }

  function renderHistory() {
    els.history.innerHTML = "";
    if (!chats.length) {
      els.history.innerHTML = '<div class="history-empty">Your chats will show up here.</div>';
      return;
    }
    chats.forEach((chat) => {
      const item = document.createElement("div");
      item.className = "history-item" + (current && current.id === chat.id ? " active" : "");
      const open = document.createElement("button");
      open.type = "button";
      open.className = "open";
      open.textContent = chat.title;
      open.title = chat.title;
      open.addEventListener("click", () => { if (busy) return; current = chat; renderAll(); closeSidebar(); });
      const del = document.createElement("button");
      del.type = "button";
      del.className = "del";
      del.setAttribute("aria-label", `Delete chat: ${chat.title}`);
      del.innerHTML = '<svg viewBox="0 0 12 12"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7"/></svg>';
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        if (busy && current === chat) return;
        chats = chats.filter((c) => c !== chat);
        if (current === chat) current = null;
        saveChats();
        renderAll();
      });
      item.append(open, del);
      els.history.append(item);
    });
  }

  function renderAll() { renderHistory(); renderThread(); }

  function buildHistory(turns) {
    const out = [];
    turns.forEach((t) => {
      if (t.role === "user") out.push({ role: "user", content: t.text });
      else if (t.text) out.push({ role: "assistant", content: t.text.replace(/^\*Mock mode:[^\n]*\n+/, "") });
      else out.push({ role: "assistant", content: "" });
    });
    return out;
  }

  // ---------- server status + spend ----------
  let status = { mock: false, budget: 0.1, spent: 0, model: "Claude Haiku 4.5", online: false };
  function updateSpend(spent, budget) {
    if (typeof spent === "number") status.spent = spent;
    if (typeof budget === "number") status.budget = budget;
    els.spend.textContent = `$${status.spent.toFixed(4)}`;
    els.spendBar.style.width = `${Math.min(100, (status.spent / (status.budget || 1)) * 100)}%`;
    els.spendCap.textContent = status.mock
      ? "Mock mode · no API calls"
      : `Cap $${status.budget.toFixed(2)} · resets when the server restarts`;
  }
  async function loadStatus() {
    try {
      const res = await fetch("/api/status");
      const s = await res.json();
      status = { ...status, ...s, online: true };
      els.modeChip.textContent = s.mock ? "Mock mode" : s.model.replace("Claude ", "");
      els.modeChip.classList.toggle("mock", !!s.mock);
      els.modeChip.title = s.mock ? "No model is called; the solver still runs" : "Live: answers use the Anthropic API";
      updateSpend(s.spent, s.budget);
    } catch (e) {
      els.modeChip.textContent = "Server offline";
      els.modeChip.title = "Start it with: python3 app.py";
    }
  }

  // ---------- sending ----------
  let busy = false;
  let controller = null;

  function setBusy(b) {
    busy = b;
    motion.target = b ? 340 : 10;
    els.sendBtn.classList.toggle("stop", b);
    els.sendBtn.setAttribute("aria-label", b ? "Stop" : "Send");
    syncSend();
  }
  function syncSend() { els.sendBtn.disabled = !busy && !els.input.value.trim(); }

  function handleEvent(turn, raw) {
    let kind = "message", data = "";
    raw.split("\n").forEach((line) => {
      if (line.startsWith("event:")) kind = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    });
    let d = {};
    try { d = JSON.parse(data || "{}"); } catch (e) { return; }

    if (kind === "tool_start") {
      turn.tools.push({ id: d.id, name: d.name, input: d.input, status: "running", open: true });
      // One run stays open; with several, collapse them so the answer stays in view.
      if (turn.tools.length > 1) turn.tools.forEach((t) => { if (!t.touched) t.open = false; });
    } else if (kind === "tool_result") {
      let tool = turn.tools.find((t) => t.id === d.id);
      if (!tool) { tool = { id: d.id, name: d.name, input: d.input, open: turn.tools.length === 0 }; turn.tools.push(tool); }
      tool.output = d.output;
      tool.is_error = !!d.is_error;
      tool.status = d.is_error ? "error" : "done";
    } else if (kind === "usage") {
      turn.cost = (turn.cost || 0) + (d.cost || 0);
      updateSpend(d.session_spent, d.budget);
    } else if (kind === "answer") {
      turn.text = d.text || "";
    } else if (kind === "error") {
      turn.error = d.message || "Something went wrong.";
    } else if (kind === "done") {
      turn.seconds = d.seconds;
    }
    updateTurn(turn);
  }

  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;

    if (!current) {
      current = { id: uid(), title: text.length > 64 ? text.slice(0, 61) + "…" : text, updated: Date.now(), turns: [] };
      chats.unshift(current);
    }
    const history = buildHistory(current.turns);
    const userTurn = { id: uid(), role: "user", text };
    const aiTurn = { id: uid(), role: "assistant", text: "", tools: [], pending: true, cost: 0, mock: !!status.mock };
    current.turns.push(userTurn, aiTurn);
    current.updated = Date.now();
    saveChats();

    els.input.value = "";
    autosize();
    renderAll();
    setBusy(true);

    controller = new AbortController();
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history }),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`Server returned ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          if (raw.trim()) handleEvent(aiTurn, raw);
        }
      }
    } catch (err) {
      if (err.name === "AbortError") aiTurn.error = aiTurn.error || "Stopped before the answer finished.";
      else aiTurn.error = "Could not reach the Ask the Blade server. Check that python3 app.py is still running.";
    } finally {
      aiTurn.pending = false;
      aiTurn.tools.forEach((t) => { if (t.status === "running") t.status = "stopped"; });
      if (!aiTurn.text && !aiTurn.error) aiTurn.error = "The server closed the connection without an answer.";
      controller = null;
      setBusy(false);
      current && (current.updated = Date.now());
      saveChats();
      updateTurn(aiTurn);
      renderHistory();
      els.input.focus();
    }
  }

  // ---------- composer ----------
  function autosize() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 200) + "px";
    syncSend();
  }
  els.input.addEventListener("input", autosize);
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send(els.input.value);
    }
  });
  els.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    if (busy) { controller && controller.abort(); return; }
    send(els.input.value);
  });

  // ---------- examples ----------
  EXAMPLES.forEach((ex) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "example";
    b.innerHTML = `<span>${esc(ex.tag)}</span>${esc(ex.text)}`;
    b.addEventListener("click", () => send(ex.text));
    els.examples.append(b);
  });

  // ---------- chrome: new chat, theme, sidebar ----------
  els.newChat.addEventListener("click", () => {
    if (busy) return;
    current = null;
    renderAll();
    closeSidebar();
    els.input.focus();
  });

  function applyThemeLabel() {
    const dark = document.documentElement.dataset.theme !== "light";
    els.themeBtn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  }
  els.themeBtn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("atb.theme", next); } catch (e) { /* ignore */ }
    applyThemeLabel();
  });
  applyThemeLabel();

  function openSidebar() { els.sidebar.classList.add("open"); els.scrim.classList.add("show"); }
  function closeSidebar() { els.sidebar.classList.remove("open"); els.scrim.classList.remove("show"); }
  els.menuBtn.addEventListener("click", openSidebar);
  els.scrim.addEventListener("click", closeSidebar);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSidebar(); });

  // ---------- start ----------
  renderAll();
  syncSend();
  // A link like /?ask=your+question opens a new chat and sends it.
  const asked = new URLSearchParams(location.search).get("ask");
  loadStatus().then(() => {
    if (asked) {
      history.replaceState(null, "", location.pathname);
      send(asked);
    }
  });
  els.input.focus();
})();
