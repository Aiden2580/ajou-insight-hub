/* 아주 인사이트 허브 - 프론트엔드 (빌드 도구 없는 단일 파일) */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------- 사용자 식별 (로그인 없는 프로토타입: 브라우저별 무작위 ID) ----------
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  };
  let UID = store.get("uid");
  if (!UID) {
    UID = "u-" + Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join("");
    store.set("uid", UID);
  }

  async function api(path, opts = {}) {
    const headers = { "X-User-Id": UID, ...(opts.headers || {}) };
    if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
    const r = await fetch(path, { ...opts, headers });
    if (!r.ok) throw new Error((await r.text()) || r.statusText);
    return r.json();
  }

  function toast(msg, ms = 2600) {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
  }

  // ---------- 탭 ----------
  const loaders = {};
  let current = "graph";
  function show(view) {
    current = view;
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
    $$("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    store.set("view", view);
    if (loaders[view]) loaders[view]();
    if (view === "graph" && G.fg) setTimeout(resizeGraph, 30);
  }
  $$("[data-view]").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
  document.addEventListener("click", (e) => { const g = e.target.closest("[data-goto]"); if (g) show(g.dataset.goto); });

  // ---------- 공용 렌더 ----------
  function dday(n) {
    if (!n.deadline) return "";
    const left = n.days_left ?? Math.ceil((new Date(n.deadline) - new Date(new Date().toDateString())) / 864e5);
    if (left < 0) return `<span class="muted">마감 ${esc(n.deadline)}</span>`;
    return `<span class="dday ${left <= 3 ? "hot" : ""}">D-${left === 0 ? "DAY" : left}</span><span>~${esc(n.deadline.slice(5))}</span>`;
  }
  function noticeHTML(n, cls = "item") {
    const tags = (n.tags || []).filter((t) => t !== "모집" && !(n.reasons || []).includes(t)).map((t) => `<span class="tag">${esc(t)}</span>`).join(" ");
    const reasons = (n.reasons || []).map((t) => `<span class="tag reason">#${esc(t)}</span>`).join(" ");
    const srcs = n.sources || [], vers = n.versions || [];
    const multi = srcs.length > 1, revs = vers.length > 1, grouped = multi || revs;
    const badges = (multi ? `<span class="badge">${srcs.length}곳 공지</span>` : "") + (revs ? `<span class="badge">${vers.length}회 공지</span>` : "");
    const srcLabel = multi ? `${esc(sourceName(n.source_id))} 외 ${srcs.length - 1}곳` : esc(sourceName(n.source_id));
    return `<div class="${cls}${n.expired ? " expired" : ""}${grouped ? " grouped" : ""}" data-id="${n.id}">
      <div class="row-head">
        <div class="meta">${n.expired ? '<span class="closed">마감</span>' : ""}${n.pinned ? '<span class="pin">고정</span>' : ""}${n.expired ? "" : dday(n)}<span>${srcLabel}</span><span>${esc(n.posted_at || "")}</span>${badges}</div>
        <button class="star ${n.bookmarked ? "on" : ""}" title="저장">${n.bookmarked ? "★" : "☆"}</button>
      </div>
      <a class="title" href="${esc(n.url)}" target="_blank" rel="noopener"${grouped ? ' data-group="1"' : ""}>${esc(n.title)}${grouped ? ' <span class="caret">▾</span>' : ""}</a>
      ${n.summary ? `<div class="summary">${esc(n.summary)}</div>` : ""}
      <div class="meta" style="margin-top:6px">${reasons} ${tags}</div>
      ${grouped ? groupPanel(n, srcs, vers) : ""}
    </div>`;
  }
  // 묶음 펼침: 어느 학과 공지를 볼지 / 몇 번째 공지를 볼지 버튼으로 고른다
  function groupPanel(n, srcs, vers) {
    const btn = (m, label, sub) => `<a class="gbtn" href="${esc(m.url)}" target="_blank" rel="noopener" data-nid="${m.id}">${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</a>`;
    const srcPart = srcs.length > 1 ? `<div class="gsec"><div class="glabel">어느 곳 공지를 볼까요?</div><div class="gbtns">${
      srcs.map((m) => btn(m, sourceName(m.source_id), m.posted_at || "")).join("")}</div></div>` : "";
    const verPart = vers.length > 1 ? `<div class="gsec"><div class="glabel">최초 공지부터 최근 공지까지</div><div class="gbtns">${
      vers.map((m, i) => btn(m, i === 0 ? "최초 공지" : i === vers.length - 1 ? "최근 공지" : `${i + 1}번째`,
        `${m.posted_at || ""} · ${m.title.length > 28 ? m.title.slice(0, 28) + "…" : m.title}`)).join("")}</div></div>` : "";
    return `<div class="gpanel"><div class="ginner">${srcPart}${verPart}</div></div>`;
  }
  // 공지 클릭/저장 → 관심 그래프에 자동 반영
  document.addEventListener("click", async (e) => {
    const box = e.target.closest("[data-id]");
    if (!box) return;
    const id = box.dataset.id;
    if (e.target.closest(".star")) {
      e.preventDefault();
      const r = await api(`/api/notices/${id}/bookmark`, { method: "POST" });
      const s = e.target.closest(".star"); s.classList.toggle("on", r.bookmarked); s.textContent = r.bookmarked ? "★" : "☆";
      toast(r.bookmarked ? "저장했어요. 관심 그래프에 반영됩니다." : "저장을 취소했어요.");
      G.dirty = true;
    } else if (e.target.closest("a.title[data-group]")) {
      e.preventDefault();  // 묶음은 바로 열지 않고 펼쳐서 고르게 한다
      box.classList.toggle("open");
    } else if (e.target.closest("a.title, a.gbtn")) {
      const a = e.target.closest("a.gbtn");
      api(`/api/notices/${a ? a.dataset.nid : id}/click`, { method: "POST" }).catch(() => {});
      G.dirty = true;
    }
  });

  let SOURCES = {};
  const sourceName = (id) => SOURCES[id]?.org || SOURCES[id]?.name || id;
  async function loadSources() {
    const d = await api("/api/sources");
    SOURCES = Object.fromEntries(d.sources.map((s) => [s.id, s]));
    return d;
  }

  // ---------- 관심 그래프 ----------
  const G = { fg: null, data: null, hover: null, neighbors: new Map(), dirty: true, showActs: true };
  const CAT_COLORS = ["#6ea8ff", "#b48cff", "#4fd1a5", "#ffb547", "#ff7eb6", "#5ce1e6", "#ff9b6b", "#a3e635", "#f472b6", "#60a5fa",
    "#c084fc", "#34d399", "#fbbf24", "#fb7185", "#22d3ee", "#fdba74", "#bef264", "#e879f9", "#93c5fd", "#fda4af", "#86efac"];
  const GROUP_COLORS = { "학습": "#6ea8ff", "교과": "#4fd1a5", "비교과": "#b48cff", "교내외 활동": "#ffb547" };
  let catColor = {};

  function resizeGraph() {
    const el = $("#graph"); if (!G.fg) return;
    G.fg.width(el.clientWidth).height(el.clientHeight);
  }

  async function loadGraph() {
    if (!G.dirty && G.data) return;
    G.dirty = false;
    const [g, recs] = await Promise.all([api("/api/graph"), api("/api/recommendations?limit=8").catch(() => [])]);
    // 색: 영역(학습·교과·비교과·교내외 활동) 고정색 → 분야는 소속 영역 색, 과목은 교과 색, 주제는 가장 강하게 연결된 분야/과목 색
    catColor = { ...GROUP_COLORS };
    g.categories.forEach((c, i) => { catColor[c] = GROUP_COLORS[(g.category_group || {})[c]] || CAT_COLORS[i % CAT_COLORS.length]; });
    const ids = new Set(g.nodes.map((n) => n.id));
    const kindOf = Object.fromEntries(g.nodes.map((n) => [n.id, n.kind]));
    g.nodes.forEach((n) => { if (n.kind === "course") catColor[n.id] = GROUP_COLORS["교과"]; });
    const rank = { category: 3, course: 2, group: 1 };
    const best = {};
    g.links.forEach((l) => {
      for (const [a, b] of [[l.source, l.target], [l.target, l.source]]) {
        const r = rank[kindOf[b]] || 0;
        if (kindOf[a] !== "topic" || !r) continue;
        const cur = best[a];
        if (!cur || r > cur.r || (r === cur.r && cur.w < l.weight)) best[a] = { c: b, w: l.weight, r };
      }
    });
    const nodes = g.nodes.map((n) => ({ ...n, color: n.kind === "topic" ? catColor[best[n.id]?.c] || "#c9d4ff" : catColor[n.id] || "#c9d4ff" }));
    const links = g.links.map((l) => ({ ...l }));
    if (G.showActs) {
      recs.forEach((r) => {
        const targets = (r.reasons || []).filter((x) => ids.has(x));
        if (!targets.length) return;
        nodes.push({ id: "act:" + r.id, label: r.title.length > 22 ? r.title.slice(0, 22) + "…" : r.title, kind: "act", weight: 0.35, notice: r, color: "#ffb547" });
        targets.forEach((t) => links.push({ source: "act:" + r.id, target: t, weight: 0.5, act: true }));
      });
    }
    G.recs = recs;
    G.data = { nodes, links };
    const maxW = Math.max(1e-6, ...g.links.map((l) => l.weight));
    links.forEach((l) => (l.norm = l.act ? 0.4 : l.weight / maxW));
    const empty = g.nodes.length === 0;
    $("#graph-empty").hidden = !empty;
    const cnt = (k) => g.nodes.filter((n) => n.kind === k).length;
    $("#graph-stats").textContent = empty ? "데이터 없음" : `영역 ${cnt("group")} · 분야 ${cnt("category")} · 과목 ${cnt("course")} · 주제 ${cnt("topic")} · 연결 ${g.links.length}`;
    drawGraph();
  }

  function drawGraph() {
    const el = $("#graph");
    G.neighbors = new Map();
    G.data.links.forEach((l) => {
      const a = typeof l.source === "object" ? l.source.id : l.source, b = typeof l.target === "object" ? l.target.id : l.target;
      if (!G.neighbors.has(a)) G.neighbors.set(a, new Set());
      if (!G.neighbors.has(b)) G.neighbors.set(b, new Set());
      G.neighbors.get(a).add(b); G.neighbors.get(b).add(a);
    });
    const radius = (n) => n.kind === "group" ? 16 + 8 * n.weight : n.kind === "category" ? 9 + 10 * n.weight
      : n.kind === "course" ? 7 + 7 * n.weight : n.kind === "act" ? 5 : 4 + 8 * n.weight;
    const big = (n) => n.kind === "group" || n.kind === "category" || n.kind === "course";
    const isLit = (id) => !G.hover || G.hover === id || G.neighbors.get(G.hover)?.has(id);
    const linkId = (x) => (typeof x === "object" ? x.id : x);

    if (!G.fg) {
      G.fg = ForceGraph()(el)
        .backgroundColor("rgba(0,0,0,0)")
        .nodeId("id")
        .nodeRelSize(1)
        .d3VelocityDecay(0.28)
        .cooldownTime(6000)
        .linkCurvature(0.12)
        .onNodeHover((n) => { G.hover = n ? n.id : null; el.style.cursor = n ? "pointer" : "grab"; })
        .onNodeClick((n) => { openNode(n); G.fg.centerAt(n.x, n.y, 600); G.fg.zoom(Math.max(G.fg.zoom(), 2.2), 600); })
        .onBackgroundClick(() => { $("#node-panel").hidden = true; G.hover = null; })
        .onNodeDragEnd((n) => { n.fx = n.x; n.fy = n.y; })
        .linkColor((l) => {
          const lit = !G.hover || linkId(l.source) === G.hover || linkId(l.target) === G.hover;
          if (l.act) return lit ? "rgba(255,181,71,.55)" : "rgba(255,181,71,.08)";
          return lit ? `rgba(160,180,255,${0.18 + 0.5 * l.norm})` : "rgba(160,180,255,.05)";
        })
        .linkWidth((l) => 0.6 + 2.4 * l.norm)
        .linkLineDash((l) => (l.act ? [2, 3] : null))
        .linkDirectionalParticles((l) => (G.hover && (linkId(l.source) === G.hover || linkId(l.target) === G.hover) ? 3 : l.norm > 0.6 ? 1 : 0))
        .linkDirectionalParticleWidth(2.2)
        .linkDirectionalParticleSpeed(0.006)
        .linkDirectionalParticleColor(() => "#e8ecff")
        .nodeCanvasObject((n, ctx, scale) => {
          if (!Number.isFinite(n.x) || !Number.isFinite(n.y)) return;
          const r = radius(n);
          const lit = isLit(n.id);
          ctx.globalAlpha = lit ? 1 : 0.15;
          if (n.kind === "act") {
            ctx.save(); ctx.translate(n.x, n.y); ctx.rotate(Math.PI / 4);
            ctx.fillStyle = n.color; ctx.shadowColor = n.color; ctx.shadowBlur = 10;
            ctx.fillRect(-r, -r, 2 * r, 2 * r); ctx.restore();
          } else if (n.kind === "group") {
            // 영역 허브: 반투명 원 + 테두리 링
            ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
            ctx.fillStyle = n.color + "33"; ctx.shadowColor = n.color; ctx.shadowBlur = 26; ctx.fill(); ctx.shadowBlur = 0;
            ctx.lineWidth = 2.5; ctx.strokeStyle = n.color; ctx.stroke();
            ctx.beginPath(); ctx.arc(n.x, n.y, r * 0.35, 0, 2 * Math.PI); ctx.fillStyle = n.color; ctx.fill();
          } else if (n.kind === "course") {
            ctx.beginPath(); ctx.roundRect ? ctx.roundRect(n.x - r, n.y - r * 0.75, 2 * r, 1.5 * r, r * 0.35) : ctx.rect(n.x - r, n.y - r * 0.75, 2 * r, 1.5 * r);
            ctx.fillStyle = n.color; ctx.shadowColor = n.color; ctx.shadowBlur = 12; ctx.fill(); ctx.shadowBlur = 0;
            if (G.hover === n.id) { ctx.lineWidth = 2 / scale; ctx.strokeStyle = "#fff"; ctx.stroke(); }
          } else {
            ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
            const grd = ctx.createRadialGradient(n.x - r / 3, n.y - r / 3, r / 5, n.x, n.y, r);
            grd.addColorStop(0, "#ffffff"); grd.addColorStop(0.35, n.color); grd.addColorStop(1, n.color);
            ctx.fillStyle = n.kind === "category" ? grd : n.color;
            ctx.shadowColor = n.color; ctx.shadowBlur = n.kind === "category" ? 22 : 8;
            ctx.fill(); ctx.shadowBlur = 0;
            if (G.hover === n.id) { ctx.lineWidth = 2 / scale; ctx.strokeStyle = "#fff"; ctx.stroke(); }
          }
          // 라벨: 분야는 항상, 주제는 확대하거나 강할 때
          const hoverLit = G.hover === n.id || (G.hover && lit);
          const showLabel = big(n) || hoverLit ||
            (n.kind === "act" ? scale > 2.6 : scale > 1.6 || (n.weight > 0.3 && scale > 0.9));
          if (showLabel) {
            const fs = (n.kind === "group" ? 15 : big(n) ? 13 : 11) / Math.min(scale, 2.5) * 1.15;
            ctx.font = `${big(n) ? 700 : 500} ${fs}px ${getComputedStyle(document.body).fontFamily}`;
            ctx.textAlign = "center"; ctx.textBaseline = "top";
            ctx.lineWidth = 3 / scale; ctx.strokeStyle = "rgba(11,16,32,.85)";
            ctx.strokeText(n.label, n.x, n.y + r + 2); ctx.fillStyle = lit ? "#e8ecff" : "rgba(232,236,255,.3)";
            ctx.fillText(n.label, n.x, n.y + r + 2);
          }
          ctx.globalAlpha = 1;
        })
        .nodePointerAreaPaint((n, color, ctx) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, radius(n) + 4, 0, 2 * Math.PI); ctx.fill(); });
      G.fg.d3Force("charge").strength((n) => (n.kind === "group" ? -260 : n.kind === "category" ? -160 : n.kind === "course" ? -110 : -45));
      // 흩어진 클러스터를 화면 중앙으로 부드럽게 모으는 중력
      let gNodes = [];
      const gravity = (alpha) => gNodes.forEach((n) => { n.vx -= n.x * 0.045 * alpha; n.vy -= n.y * 0.045 * alpha; });
      gravity.initialize = (ns) => (gNodes = ns);
      G.fg.d3Force("gravity", gravity);
      G.fg.d3Force("link").distance((l) => (l.act ? 40 : 30 + 70 * (1 - l.norm))).strength((l) => 0.15 + 0.5 * l.norm);
      G.fg.onEngineStop(() => { if (!G.fitted) { G.fg.zoomToFit(800, 70); G.fitted = true; } });
      window.addEventListener("resize", resizeGraph);
    }
    G.fitted = false;
    G.fg.graphData(G.data);
    resizeGraph();
    setTimeout(() => G.fg.zoomToFit(900, 70), 1800);
  }

  function openNode(n) {
    const p = $("#node-panel"), body = $("#np-body");
    p.hidden = false;
    if (n.kind === "act") {
      body.innerHTML = `<div class="muted small">추천 활동</div>${noticeHTML(n.notice, "card")}`;
      return;
    }
    const nb = [...(G.neighbors.get(n.id) || [])].filter((x) => !x.startsWith("act:"));
    const src = Object.entries(n.sources || {}).map(([k, v]) => `${{ history: "방문 기록", folder: "학습 폴더", keyword: "직접 입력", click: "공지 열람", bookmark: "저장" }[k] || k} ${v}`).join(" · ");
    const rel = (G.recs || []).filter((r) => (r.reasons || []).includes(n.id) || (r.tags || []).includes(n.id));
    body.innerHTML = `
      <div class="muted small">${{ group: "영역", category: "분야", course: "과목", topic: "주제" }[n.kind] || "주제"} · 관심도 ${Math.round(n.weight * 100)}</div>
      <h3>${esc(n.label)}</h3>
      <div class="small muted">출처: ${esc(src || "-")}</div>
      <p class="small">연결된 노드</p>
      <div class="chips">${nb.map((x) => `<button class="nb" data-node="${esc(x)}">${esc(x)}</button>`).join("") || '<span class="muted small">없음</span>'}</div>
      <p class="small">관련 추천 활동</p>
      <div class="list">${rel.map((r) => noticeHTML(r)).join("") || '<span class="muted small">아직 없음 · <a href="#" data-goto="all">모든 활동 보기</a></span>'}</div>`;
    $$(".nb", body).forEach((b) => b.addEventListener("click", () => {
      const t = G.data.nodes.find((x) => x.id === b.dataset.node);
      if (t) { G.hover = t.id; openNode(t); G.fg.centerAt(t.x, t.y, 600); }
    }));
  }
  $("#np-close").addEventListener("click", () => { $("#node-panel").hidden = true; G.hover = null; });
  $("#g-fit").addEventListener("click", () => G.fg && G.fg.zoomToFit(600, 70));
  $("#g-reheat").addEventListener("click", () => { if (!G.fg) return; G.data.nodes.forEach((n) => { n.fx = n.fy = undefined; }); G.fitted = false; G.fg.d3ReheatSimulation(); });
  $("#show-acts").addEventListener("change", (e) => { G.showActs = e.target.checked; G.dirty = true; loadGraph(); });
  $("#use-demo").addEventListener("click", () => {
    UID = "demo-user-0001"; store.set("uid", UID); G.dirty = true; loadGraph(); toast("데모 프로필로 전환했어요.");
  });
  loaders.graph = loadGraph;

  // ---------- 인사이트 ----------
  function md(src) {
    // 아주 작은 마크다운 렌더러 (헤더, 목록, 체크박스, 굵게, 링크, 인용, 단락)
    const inline = (s) => esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[((?:[^\[\]]|\[[^\]]*\])+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    const out = []; let list = null, para = [];
    const flushP = () => { if (para.length) { out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; } };
    const flushL = () => { if (list) { out.push(`</${list}>`); list = null; } };
    for (const raw of src.split("\n")) {
      const line = raw.replace(/\s+$/, "");
      let m;
      if (!line.trim()) { flushP(); flushL(); continue; }
      if ((m = line.match(/^(#{1,4})\s+(.*)/))) { flushP(); flushL(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); continue; }
      if ((m = line.match(/^>\s?(.*)/))) { flushP(); flushL(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); continue; }
      if ((m = line.match(/^\s*([-*]|\d+\.)\s+(.*)/))) {
        flushP(); const t = /\d/.test(m[1]) ? "ol" : "ul";
        if (list !== t) { flushL(); out.push(`<${t}>`); list = t; }
        const c = m[2].replace(/^\[( |x)\]\s*/i, (_, x) => (x.trim() ? "☑ " : "☐ "));
        out.push(`<li>${inline(c)}</li>`); continue;
      }
      if (list && /^\s{2,}/.test(raw)) { out[out.length - 1] = out[out.length - 1].replace(/<\/li>$/, "<br>" + inline(line.trim()) + "</li>"); continue; }
      flushL(); para.push(line);
    }
    flushP(); flushL();
    return out.join("\n");
  }
  function renderInsight(ins) {
    if (!ins) { $("#insight-body").innerHTML = '<p class="muted">아직 생성된 인사이트가 없어요. 데이터 연동 후 다시 열면 오늘 인사이트가 만들어져요.</p>'; $("#insight-meta").textContent = ""; return; }
    $("#insight-body").innerHTML = md(ins.content_md);
    const by = { template: "템플릿(비용 0)", reused: "어제와 동일 컨텍스트 → 재사용(비용 0)" }[ins.generated_by] || ins.generated_by;
    $("#insight-meta").textContent = `${ins.date} · ${by}${ins.ctx_tokens_est ? ` · 입력 컨텍스트 약 ${ins.ctx_tokens_est} 토큰` : ""}`;
  }
  const today = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);  // KST
  let autoTried = false;
  async function loadInsight(date) {
    let d = await api("/api/insight" + (date ? `?date=${date}` : ""));
    // 하루 1회 자동 생성: 오늘 것이 없을 때만 (서버도 오늘 것이 있으면 재생성하지 않음)
    if (!date && !autoTried && (!d.history.length || d.history[0].date !== today())) {
      autoTried = true;
      $("#insight-body").innerHTML = '<p class="muted">오늘의 인사이트를 만드는 중…</p>';
      try { await api("/api/insight/generate", { method: "POST" }); d = await api("/api/insight"); } catch (e) { /* 실패 시 기존 기록 표시 */ }
    }
    const sel = $("#insight-date");
    sel.innerHTML = d.history.map((h) => `<option value="${h.date}">${esc((h.title || h.date).slice(0, 34))}</option>`).join("") || "<option>기록 없음</option>";
    if (d.insight) sel.value = d.insight.date;
    renderInsight(d.insight);
  }
  $("#insight-date").addEventListener("change", (e) => loadInsight(e.target.value));
  $("#insight-gen").addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "생성 중…";
    try { const ins = await api("/api/insight/generate?force=true", { method: "POST" }); await loadInsight(ins.date); toast("인사이트를 다시 만들었어요."); }
    catch (err) { toast("생성 실패: " + err.message); }
    e.target.disabled = false; e.target.textContent = "다시 생성";
  });
  $("#insight-print").addEventListener("click", () => window.print());
  loaders.insight = () => loadInsight();

  // ---------- 추천 ----------
  loaders.recs = async () => {
    await loadSourcesOnce();
    const recs = await api("/api/recommendations?limit=30");
    $("#recs").innerHTML = recs.length ? recs.map((r) => noticeHTML(r, "card")).join("")
      : '<div class="panel">아직 추천할 활동이 없어요. <a href="#" data-goto="connect">데이터 연동</a>으로 관심사를 알려주세요.</div>';
  };

  // ---------- 모든 활동 ----------
  const A = { page: 1, tag: "", total: 0 };
  let srcLoaded = null;
  const loadSourcesOnce = () => (srcLoaded ||= loadSources().then((d) => {
    const groups = {};
    d.sources.filter((s) => s.count > 0 || s.enabled).forEach((s) => (groups[s.group] ||= []).push(s));
    $("#group").innerHTML = '<option value="">전체 기관</option>' + Object.entries(groups).map(([g, arr]) =>
      `<optgroup label="${esc(g)}">${arr.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</optgroup>`).join("");
    return d;
  }));
  async function loadAll(reset = true) {
    await loadSourcesOnce();
    if (reset) A.page = 1;
    const p = new URLSearchParams({ q: $("#q").value, tag: A.tag, source: $("#group").value, sort: $("#sort").value, page: A.page, size: 30, active_only: $("#active-only").checked, include_admin: $("#include-admin").checked });
    const d = await api("/api/notices?" + p);
    A.total = d.total;
    const html = d.items.map((n) => noticeHTML(n)).join("");
    if (reset) $("#all-list").innerHTML = html || '<div class="panel muted">조건에 맞는 활동이 없어요.</div>';
    else $("#all-list").insertAdjacentHTML("beforeend", html);
    $("#all-count").textContent = `총 ${d.total}건`;
    $("#more").hidden = A.page * 30 >= d.total;
  }
  async function loadTags() {
    const d = await api("/api/tags?include_admin=" + $("#include-admin").checked);
    const tags = d.tags || d;
    $("#admin-count").textContent = d.admin_count != null ? `(${d.admin_count})` : "";
    $("#tag-chips").innerHTML = `<button data-tag="" class="${A.tag ? "" : "on"}">전체</button>` +
      tags.map((t) => `<button data-tag="${esc(t.tag)}" class="${A.tag === t.tag ? "on" : ""}">${esc(t.tag)} <span class="muted">${t.count}</span></button>`).join("");
  }
  $("#tag-chips").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tag]"); if (!b) return;
    A.tag = b.dataset.tag; $$("#tag-chips button").forEach((x) => x.classList.toggle("on", x === b)); loadAll();
  });
  let qT; $("#q").addEventListener("input", () => { clearTimeout(qT); qT = setTimeout(loadAll, 250); });
  ["#sort", "#group", "#active-only"].forEach((s) => $(s).addEventListener("change", () => loadAll()));
  $("#include-admin").addEventListener("change", () => { loadTags(); loadAll(); });
  $("#more").addEventListener("click", () => { A.page++; loadAll(false); });
  loaders.all = () => { loadTags(); loadAll(); api("/api/status").then(updateLastCrawl).catch(() => {}); };

  // ---------- 데이터 연동 ----------
  async function sendSignals(kind, items, statusEl) {
    if (!items.length) return toast("보낼 항목이 없어요.");
    const r = await api("/api/signals", { method: "POST", json: { kind, items } });
    G.dirty = true;
    const msg = `${r.added}개 반영 → 노드 ${r.nodes.length}개`;
    if (statusEl) statusEl.textContent = msg;
    toast(msg + ". 관심 그래프를 확인해 보세요!");
    loadSigSummary();
  }
  $("#folder-input").addEventListener("change", (e) => {
    const paths = [...e.target.files].map((f) => f.webkitRelativePath || f.name).filter((p) => !/(^|\/)\./.test(p) && !/node_modules|__pycache__/.test(p));
    sendSignals("folder", paths.slice(0, 5000), $("#folder-status"));
  });
  $("#history-file").addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("kind", "history"); fd.append("file", f);
    const r = await fetch("/api/signals/upload", { method: "POST", body: fd, headers: { "X-User-Id": UID } });
    const d = await r.json(); G.dirty = true;
    $("#history-status").textContent = r.ok ? `${d.added}개 기록 반영` : "업로드 실패";
    loadSigSummary();
  });
  $("#history-send").addEventListener("click", () => {
    const items = $("#history-text").value.split("\n").map((s) => s.trim()).filter(Boolean);
    sendSignals("history", items, $("#history-status")); $("#history-text").value = "";
  });
  $("#kw-send").addEventListener("click", () => {
    const items = $("#kw-input").value.split(/[,，\n]/).map((s) => s.trim()).filter(Boolean);
    sendSignals("keyword", items); $("#kw-input").value = "";
  });
  $("#reset").addEventListener("click", async () => {
    if (!confirm("내 관심사 데이터, 인사이트, 저장 목록을 모두 삭제할까요?")) return;
    await api("/api/signals", { method: "DELETE" }); G.dirty = true; loadSigSummary(); toast("삭제했어요.");
  });
  async function loadSigSummary() {
    const s = await api("/api/signals/summary");
    const name = { history: "방문 기록", folder: "학습 폴더 파일", keyword: "키워드", click: "공지 열람", bookmark: "저장" };
    $("#sig-summary").innerHTML = Object.keys(s).length ? Object.entries(s).map(([k, v]) => `${name[k] || k}: <b>${v}</b>`).join("<br>") : "아직 연동된 데이터가 없어요.";
  }
  loaders.connect = loadSigSummary;

  // ---------- 관리 ----------
  async function loadAdmin() {
    const [st, src] = await Promise.all([api("/api/status"), loadSources()]);
    $("#crawl-state").textContent = `공지 ${st.notices}건 · 마지막 갱신 ${fmtTime(st.scheduler.last_crawl)}${st.scheduler.running ? " · 갱신 중…" : ""} · 자동 수집 ${st.scheduler.auto_crawl ? "켜짐" : "꺼짐(버튼으로만 갱신)"}`;
    const u = st.llm_usage;
    const tagRule = st.tagged_by.rule || 0, tagLLM = st.tagged_by.llm || 0;
    $("#usage").innerHTML = `LLM ${st.llm_enabled ? (st.llm_provider === "baze" ? "사용 중 (아주LLM BAZE)" : "사용 중 (Anthropic)") : "꺼짐(규칙/템플릿만 사용, 비용 0)"} · 태깅 모델 <code>${esc(st.models.tagging)}</code> · 인사이트 모델 <code>${esc(st.models.insight)}</code><br>
      태깅: 규칙만으로 처리 ${tagRule}건 / LLM 보강 ${tagLLM}건<br>` +
      (u.length ? u.map((x) => `${esc(x.purpose)} (${esc(x.model)}): 호출 ${x.calls}회 · 입력 ${x.input_tokens} · 출력 ${x.output_tokens} 토큰`).join("<br>") : "아직 LLM 호출 없음");
    const on = src.sources.filter((s) => s.enabled).length;
    $("#sources").innerHTML = `<p>활성 ${on} / 전체 ${src.sources.length}개 · 파서: ${src.parsers.join(", ")}</p>
      <table class="src"><tr><th>기관 · 게시판</th><th>분류</th><th>수집</th><th>상태</th></tr>` +
      src.sources.map((s) => `<tr><td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a></td><td>${esc(s.group)}</td><td>${s.count}</td>
        <td>${!s.enabled ? '<span class="muted">비활성</span>' : s.last ? (s.last.ok ? `<span class="ok">정상</span> ${s.last.found}건` : `<span class="bad" title="${esc(s.last.message)}">실패</span>`) : '<span class="muted">대기</span>'}</td></tr>`).join("") + "</table>";
  }
  // 공지 새로고침: 사용자가 누를 때만 수집한다 (자동 수집 꺼짐이 기본)
  const fmtTime = (s) => (s ? String(s).replace("T", " ").slice(0, 16) : "-");
  async function refreshNotices(btn) {
    const btns = $$(".refresh-btn");
    const st0 = await api("/api/crawl", { method: "POST" });
    if (!st0.started && !st0.running) return toast("새로고침을 시작하지 못했어요.");
    btns.forEach((b) => { b.disabled = true; b.dataset.label ||= b.textContent; b.textContent = "새로고침 중…"; });
    toast("공지를 새로 모으는 중이에요. 게시판이 많아 몇 분 걸립니다.");
    const poll = async () => {
      const st = await api("/api/status").catch(() => null);
      if (st && st.scheduler.running) return setTimeout(poll, 4000);
      btns.forEach((b) => { b.disabled = false; b.textContent = b.dataset.label; });
      const r = st?.scheduler.last_result || {};
      toast(`새로고침 완료: 새 공지 ${r.new ?? 0}건${r.tagging ? ` · LLM 태깅 ${r.tagging.llm_tagged ?? 0}건` : ""}`);
      updateLastCrawl(st); G.dirty = true;
      if (current === "all") { loadTags(); loadAll(); } else if (current === "admin") loadAdmin();
    };
    setTimeout(poll, 3000);
  }
  function updateLastCrawl(st) {
    if (!st) return;
    $$(".last-crawl").forEach((el) => (el.textContent = `마지막 갱신 ${fmtTime(st.scheduler.last_crawl)}${st.scheduler.running ? " · 갱신 중…" : ""}`));
  }
  $$(".refresh-btn").forEach((b) => b.addEventListener("click", () => refreshNotices(b)));
  loaders.admin = loadAdmin;

  // ---------- 시작 ----------
  loadSourcesOnce();
  show(store.get("view") || "graph");
})();
