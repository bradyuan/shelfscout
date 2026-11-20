// ShelfScout dock: on an Amazon search page, find the best value (vs best price and Amazon's pick).
// Reads the cards already on the page (no extra requests), asks the local backend to analyze them,
// and shows the result. Parsing lives in parsers.js. Everything stays in this tab.
(() => {
  const page = SSPageKind.classify(location.href);
  if (page.kind === "never" || window.__shelfscout) return;
  window.__shelfscout = true;

  const api = (path, method, body) =>
    new Promise((res) => {
      try { chrome.runtime.sendMessage({ type: "api", path, method, body }, (r) => res(chrome.runtime.lastError ? null : r)); }
      catch { res(null); }
    });

  const ICON = `<svg viewBox="0 0 64 64" width="56" height="56" fill="none" stroke="#e8eaed" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M30 8H12a4 4 0 0 0-4 4v18l24 24a4 4 0 0 0 5.7 0l12.6-12.6a4 4 0 0 0 0-5.7Z"/>
    <circle cx="19" cy="19" r="3.5" fill="#6fcf97" stroke="none"/>
    <circle cx="44" cy="44" r="9" fill="#1e2329"/><path d="m50.5 50.5 6 6"/>
    <path d="m40 44 3 3 5-6" stroke="#6fcf97"/></svg>`;

  // ---------- dock (closed shadow root: Amazon's CSS and scripts can't reach in) ----------
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
  <style>
    :host{all:initial}
    *{box-sizing:border-box;font-family:-apple-system,system-ui,sans-serif;margin:0}
    .pill-open{background:#2f9e61;color:#fff;border:0;border-radius:999px;padding:9px 15px;font:700 13px/1 inherit;cursor:pointer;box-shadow:0 4px 16px rgba(0,0,0,.25)}
    .card{position:relative;width:360px;max-height:82vh;background:#2b313a;color:#e8eaed;border-radius:16px;box-shadow:0 10px 32px rgba(0,0,0,.35);font-size:13px;line-height:1.4;overflow:auto}
    .inner{padding:18px 16px 16px;display:grid;grid-template-columns:minmax(0,1fr);gap:12px}
    .inner>*{min-width:0}
    .x{position:absolute;top:10px;right:12px;border:0;background:none;font-size:22px;color:#9aa0a6;cursor:pointer;line-height:1}
    .brand{display:grid;justify-items:center;gap:6px;padding-bottom:2px}
    .brand h1{font-size:22px;font-weight:800;letter-spacing:-.01em}
    .stats{display:grid;grid-template-columns:repeat(3,1fr);background:#363d47;border-radius:12px;padding:12px 4px;text-align:center}
    .stats b{display:block;font-size:22px;color:#6fcf97;font-variant-numeric:tabular-nums}
    .stats span{font-size:12px;color:#aab0b6}
    .find{justify-self:center;background:linear-gradient(90deg,#5cb85c,#3f9a52);color:#fff;border-radius:999px;padding:9px 16px;font-weight:600;font-size:13px;text-align:center}
    .picks{display:grid;gap:6px}
    .pick{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 10px;background:#363d47;border-radius:10px;padding:9px 11px;text-decoration:none;color:inherit}
    .pick:hover{background:#3e4651}
    .pick.best{background:#24553b;outline:1px solid #3f9a52}
    .pick .k{font-size:10px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:#aab0b6}
    .pick.best .k{color:#9fe0b9}
    .pick .t{grid-column:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
    .pick .n{grid-column:2;grid-row:1/3;align-self:center;text-align:right;font-variant-numeric:tabular-nums;font-size:12px;color:#d0d4d8;white-space:nowrap}
    .pick .why{grid-column:1/3;font-size:12px;color:#c7e9d4}
    .warn{color:#f2c94c}
    .ins{display:grid;gap:6px}
    details{background:#363d47;border-radius:10px;overflow:hidden}
    summary{list-style:none;display:grid;grid-template-columns:62px minmax(0,1fr) 12px;gap:8px;align-items:center;padding:9px 11px;cursor:pointer}
    summary::-webkit-details-marker{display:none}
    summary::after{content:"›";color:#9aa0a6;font-size:16px;transition:transform .15s}
    details[open] summary::after{transform:rotate(90deg)}
    .pr{font-size:10px;font-weight:800;border-radius:4px;padding:2px 0;text-align:center}
    .HIGH{background:#2f6e48;color:#d8f2e3}.MEDIUM{background:#6a5414;color:#f6e0a0}.LOW{background:#4a515b;color:#d0d4d8}
    .dt{padding:0 11px 9px 81px;color:#aab0b6;font-size:12px;display:grid;gap:5px}
    .dt a{color:#cfe3ff;text-decoration:none;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px}
    .dt a span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .dt a span:last-child{color:#aab0b6;font-variant-numeric:tabular-nums}
    .dt a:hover span:first-child{text-decoration:underline}
    .status{display:flex;gap:8px;justify-content:center;align-items:center;background:#33493d;color:#a8dcb9;border-radius:10px;padding:11px;font-size:13px}
    .status.err{background:#4a2f2a;color:#f4b3a5}
    .ai{display:grid;gap:8px;background:#363d47;border-radius:10px;padding:10px 11px}
    .ai input[type=text]{width:100%;background:#2b313a;border:1px solid #4a515b;border-radius:8px;color:#e8eaed;padding:8px 10px;font:13px inherit}
    .ai input[type=text]::placeholder{color:#8a9097}
    .airow{display:flex;align-items:center;gap:10px;font-size:13px}
    .airow small{color:#aab0b6;font-size:11px}
    .sw{position:relative;width:40px;height:24px;flex:none}
    .sw input{opacity:0;width:0;height:0}
    .sw span{position:absolute;inset:0;background:#5a616b;border-radius:999px;transition:.15s;cursor:pointer}
    .sw span::before{content:"";position:absolute;width:20px;height:20px;left:2px;top:2px;background:#fff;border-radius:50%;transition:.15s}
    .sw input:checked+span{background:#34c759}.sw input:checked+span::before{transform:translateX(16px)}
    .sw input:disabled+span{opacity:.45;cursor:default}
    .filt{background:#3d8fe0;color:#fff;border:0;border-radius:10px;padding:11px;font-weight:700;font-size:14px;cursor:pointer}
    .filt:disabled{opacity:.55;cursor:default}
    .gear{margin-left:auto;background:none;border:0;color:#aab0b6;cursor:pointer;font-size:12px;text-decoration:underline}
    .rel{grid-column:1/3;font-size:12px;color:#c7e9d4}.rel q{color:#e8eaed}
    .go{background:linear-gradient(90deg,#5cb85c,#3f9a52);color:#fff;border:0;border-radius:10px;padding:14px;font-weight:700;font-size:16px;cursor:pointer}
    .go:disabled{opacity:.6;cursor:default}
    .row{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
    .sec{background:#454c57;color:#fff;border:0;border-radius:10px;padding:11px 6px;font-weight:700;font-size:14px;cursor:pointer}
    .sec.xl{background:#3d8fe0}
    .sec:disabled{opacity:.45;cursor:default}
    .foot{display:flex;justify-content:space-between;color:#8a9097;font-size:11px}
    .foot a{color:#8a9097}
    .dbg{display:flex;gap:5px;align-items:center;cursor:pointer;user-select:none}
    .dbg input{accent-color:#3f9a52;margin:0}
    .debug{background:#1b1f25;border:1px dashed #4a515b;border-radius:10px;padding:10px;font:11px/1.5 ui-monospace,Menlo,monospace;color:#c4c8cc;display:grid;gap:3px}
    .debug b{color:#e8eaed;font-family:-apple-system,system-ui,sans-serif;font-size:12px}
    .debug .bad{color:#f4a99a}.debug .ok{color:#9fe0b9}
    .debug a{color:#9cc4ff}
    .note{color:#f2c94c;font-size:11px;margin-top:-4px}
    .note.ok{color:#9fe0b9}
    .lh{font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:#aab0b6;margin:2px 2px 0}
    .li{display:grid;grid-template-columns:20px minmax(0,1fr) auto;gap:2px 8px;background:#363d47;border-radius:10px;padding:8px 10px;text-decoration:none;color:inherit;font-size:12.5px}
    .li:hover{background:#3e4651}.li.hr{box-shadow:inset 3px 0 0 #e5534b}
    .li .rk{color:#8a9097;font-variant-numeric:tabular-nums}
    .li .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
    .li .n{color:#d0d4d8;font-variant-numeric:tabular-nums;white-space:nowrap;font-size:12px}
    .tags{grid-column:2/4;display:flex;flex-wrap:wrap;gap:4px 8px;font-size:11px}
    .pick .tags{grid-column:1/3}
    .risk{color:#f4a99a}.risk b{background:#7a2a22;color:#ffd9d3;border-radius:4px;padding:0 5px;margin-right:3px;font-size:10px}
    .adtag{background:#4a515b;color:#c4c8cc;border-radius:4px;padding:0 5px;font-size:10px}
    .tags .rel{color:#9fe0b9}
    .more{background:none;border:1px solid #4a515b;color:#c4c8cc;border-radius:8px;padding:7px;cursor:pointer;font-size:12px}
    details.matches{outline:1px solid #3f9a52;background:#2b3d33}
    [hidden]{display:none!important}
  </style>
  <button class="pill-open">ShelfScout</button>
  <div class="card" hidden>
    <button class="x" title="Minimize">–</button>
    <div class="inner">
      <div class="brand">${ICON}<h1>ShelfScout</h1></div>
      <div class="stats">
        <div><b class="s-items">–</b><span>Items</span></div>
        <div><b class="s-rating">–</b><span>★ Avg rating</span></div>
        <div><b class="s-price">–</b><span>$ Median price</span></div>
      </div>
      <div class="find" hidden></div>
      <div class="picks" hidden></div>
      <div class="ins" hidden></div>
      <div class="status"></div>
      <button class="go start">▶&nbsp; Find best value</button>
      <div class="ai" hidden>
        <button class="filt">Filter off-topic results</button>
        <input type="text" class="notes" placeholder="Specific instructions (optional): no plastic, for a large dog…">
        <div class="airow"><small class="ai-sub"></small><button class="gear">settings</button></div>
      </div>
      <div class="row"><button class="sec xl" disabled>Excel</button><button class="sec csv" disabled>CSV</button><button class="sec json" disabled>JSON</button></div>
      <div class="debug" hidden></div>
      <div class="foot"><span class="ver"></span><label class="dbg"><input type="checkbox" class="dbg-on"> Debug</label></div>
    </div>
  </div>`;
  document.documentElement.appendChild(host);
  const $ = (s) => root.querySelector(s);
  const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };
  const open = (v) => { $(".pill-open").hidden = v; $(".card").hidden = !v; };
  $(".pill-open").onclick = () => open(true);
  $(".x").onclick = () => open(false);
  chrome.runtime.onMessage.addListener((msg) => { if (msg.type === "open-dock") open(true); });
  $(".ver").textContent = `v${chrome.runtime.getManifest?.().version ?? ""}`;

  const money = (n) => (n == null ? "–" : `$${Number(n).toFixed(2)}`);
  const count = (n) => (n == null ? "?" : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
  const facts = (p) => `${money(p.price)} · ${p.rating ?? "?"}★ (${count(p.n_ratings)})`;
  const status = (msg, err = false) => { const s = $(".status"); s.className = "status" + (err ? " err" : ""); s.textContent = msg; };

  if (page.kind !== "search") {
    status("Open an Amazon search to find its best value");
    $(".start").disabled = true;
    return;
  }
  const visible = SSParsers.searchCards(document).length;
  status(`Ready: ${visible} products on this page`);

  // ---------- debug mode: show the run's stages right here, link to the factory view ----------
  const BACKEND = "http://localhost:8100";
  let runs = [];       // [{phase, run_id}] for the current analysis
  let fetchLog = [];
  chrome.storage.local.get("debug").then(({ debug }) => { $(".dbg-on").checked = !!debug; showDebug(); }).catch(() => {});
  $(".dbg-on").onchange = () => { chrome.storage.local.set({ debug: $(".dbg-on").checked }).catch(() => {}); showDebug(); };

  async function showDebug() {
    const box = $(".debug");
    if (!$(".dbg-on").checked || !runs.length) { box.hidden = true; return; }
    const parts = [el("b", { textContent: "Debug" })];
    for (const { phase, run_id } of runs) {
      const res = await api(`/api/runs/${run_id}`, "GET");
      parts.push(el("div", {}, `${phase} run `, el("a", { href: `${BACKEND}/runs/${run_id}`, target: "_blank", textContent: `${run_id} ↗` })));
      for (const s of res?.data?.stages || []) {
        const m = s.metric ? ` · ${s.metric.label}: ${s.metric.value}` : "";
        parts.push(el("div", { className: s.flags.length ? "bad" : "" },
          `${s.idx}. ${s.name}  ${s.n_in}→${s.n_out}${m}  ${(s.seconds * 1000).toFixed(0)}ms${s.flags.length ? "  ⚠ " + s.flags.join(", ") : ""}`));
      }
    }
    if (fetchLog.length) {
      parts.push(el("b", { textContent: "Feature reads" }));
      for (const l of fetchLog)
        parts.push(el("div", { className: l.status === "failed" ? "bad" : l.status === "fetched" ? "ok" : "" },
          `${l.asin}  ${l.status}${l.n_features != null ? `  ${l.n_features} features` : ""}${l.ms ? `  ${l.ms}ms` : ""}${l.reason ? "  " + l.reason : ""}`));
    }
    parts.push(el("div", {}, el("a", { href: `${BACKEND}/runs`, target: "_blank", textContent: "all runs ↗" })));
    box.replaceChildren(...parts);
    box.hidden = false;
  }

  // ---------- per-product feature data, cached for a day ----------
  const DAY = 864e5;
  async function cached(asins) {
    try {
      const got = await chrome.storage.local.get(asins.map((a) => "f:" + a));
      return Object.fromEntries(asins.filter((a) => got["f:" + a] && Date.now() - got["f:" + a].t < DAY).map((a) => [a, got["f:" + a].f]));
    } catch { return {}; }
  }
  const remember = (asin, f) => chrome.storage.local.set({ ["f:" + asin]: { t: Date.now(), f } }).catch(() => {});
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function readFeatures(targets) {
    const features = await cached(targets);
    fetchLog.push(...targets.filter((a) => features[a]).map((asin) => ({ asin, status: "cached" })));
    const todo = targets.filter((a) => !features[a]);
    for (const [i, asin] of todo.entries()) {
      status(`Reading review features ${i + 1}/${todo.length}…`);
      if (i) await sleep(1500 + Math.random() * 1500);  // one page every 1.5-3 s
      const t0 = performance.now();
      try {
        const resp = await fetch(`/dp/${asin}`, { credentials: "include" });
        const doc = new DOMParser().parseFromString(await resp.text(), "text/html");
        const ms = Math.round(performance.now() - t0);
        const blocked = SSParsers.blockKind(doc, resp.url);
        if (blocked) { fetchLog.push({ asin, status: "failed", reason: `Amazon ${blocked}`, ms }); break; }
        const f = { features: SSParsers.features(doc), stars: SSParsers.starBreakdown(doc) };
        features[asin] = f;
        remember(asin, f);
        fetchLog.push({ asin, status: "fetched", n_features: f.features.length, ms });
      } catch (e) {
        fetchLog.push({ asin, status: "failed", reason: String(e) });
      }
    }
    return features;
  }

  let last = null, cards = [];
  async function analyze(body) {
    const res = await api("/api/analyze", "POST", { url: location.href, keyword: page.keyword, products: cards, ...body });
    if (!res) { status("Can't reach the ShelfScout backend (localhost:8100)", true); return null; }
    if (!res.ok || res.data.status !== "ok") { status(`Backend problem: ${JSON.stringify(res.data).slice(0, 120)}`, true); return null; }
    return res.data;
  }

  // ---------- limits (settings page) and the user's own AI key ----------
  // 0 = default: products -> the first page only; aiChecks -> every product analyzed
  const DEFAULT_LIMITS = { products: 0, featurePages: 10, aiChecks: 0 };
  const aiN = () => limits.aiChecks || cards.length;
  let limits = { ...DEFAULT_LIMITS };
  let aiCfg = null;
  async function loadSettings() {
    try {
      const got = await chrome.storage.local.get(["ai", "limits"]);
      aiCfg = got.ai || null;
      limits = { ...DEFAULT_LIMITS, ...(got.limits || {}) };
    } catch { aiCfg = null; }
    const ready = !!(aiCfg && aiCfg.use && aiCfg.keys?.[aiCfg.use]);
    $(".filt").textContent = ready ? "Filter off-topic results" : "Filter off-topic results · add your AI key";
    $(".ai-sub").textContent = ready ? `AI · uses your ${aiCfg.use} key · ${limits.aiChecks ? `checks up to ${limits.aiChecks} products` : "checks every product"}`
                                     : "AI · needs your own key (Gemini has a free tier)";
    return ready;
  }
  loadSettings();
  chrome.storage.onChanged?.addListener((ch) => { if (ch.ai || ch.limits) loadSettings(); });
  $(".gear").onclick = () => chrome.runtime.sendMessage({ type: "open-options" });
  $(".filt").onclick = async () => {
    if (!(await loadSettings())) return chrome.runtime.sendMessage({ type: "open-options" });
    if (!lastFull) return;
    $(".filt").disabled = true; $(".start").disabled = true;
    const ai = await aiPass(lastFull, lastFeatures);
    if (ai) {
      last = ai; runs.push({ phase: "ai picks", run_id: ai.run_id });
      const off = Object.values(judgments).filter((j) => j.relevant === false).length;
      render(last, false);
      const failed = Object.values(judgments).filter((j) => j.relevant === null).length;
      status(`Removed ${off} off-topic of ${Object.keys(judgments).length} checked` + (failed ? ` · ${failed} couldn't be checked` : "")
             + (last.best_value ? " · best value updated" : " · no best value"));
    }
    $(".filt").disabled = false; $(".start").disabled = false;
  };

  // ---------- more search pages until the product limit (like clicking Next) ----------
  async function readSearchPages(limit) {
    const all = SSParsers.searchCards(document);
    if (!limit) return all;  // default: just the page you're on
    const seen = new Set(all.map((c) => c.asin));
    let organic = Math.max(0, ...all.map((c) => c.organic_rank || 0));
    for (let n = 2; all.length < limit && n <= 8; n++) {
      status(`Reading search results page ${n}… (${all.length}/${limit} products)`);
      await sleep(1500 + Math.random() * 1500);
      const url = new URL(location.href);
      url.searchParams.set("page", n);
      let doc, resp;
      try {
        resp = await fetch(url, { credentials: "include" });
        doc = new DOMParser().parseFromString(await resp.text(), "text/html");
      } catch { break; }
      if (SSParsers.blockKind(doc, resp.url)) { fetchLog.push({ asin: `search page ${n}`, status: "failed", reason: "Amazon block" }); break; }
      const more = SSParsers.searchCards(doc).filter((c) => !seen.has(c.asin));
      if (!more.length) break;  // no more results
      for (const c of more) {
        seen.add(c.asin);
        all.push({ ...c, position: all.length + 1, organic_rank: c.organic_rank ? ++organic : null, page: n });
      }
    }
    return all.slice(0, limit);
  }

  async function aiPass(base, features) {
    const shortlist = base.ai_shortlist || [];
    const byAsin = Object.fromEntries(cards.map((c) => [c.asin, c]));
    const products = shortlist.map((a) => ({ ...byAsin[a], features: features[a]?.features || [] })).filter((p) => p.asin);
    if (!products.length) { status("Nothing to filter on this page.", true); return null; }
    status(`Filtering: AI checking ${products.length} products…`);
    const res = await api("/api/ai/judge", "POST", { provider: aiCfg.use, key: aiCfg.keys[aiCfg.use],
      query: page.keyword || "", notes: $(".notes").value.trim(), products });
    if (!res || !res.ok || res.data.status !== "ok") {
      status(`Filter failed: ${res?.data?.error || "server not reachable"}. Showing regular results.`, true);
      if (res?.data?.run_id) runs.push({ phase: "ai (failed)", run_id: res.data.run_id });
      return null;
    }
    runs.push({ phase: "ai", run_id: res.data.run_id });
    const errs = res.data.judgments.filter((j) => j.relevant === null);
    if (errs.length === res.data.judgments.length) {  // every check failed: don't pretend we filtered
      status(`Filter failed: no product could be checked (${(errs[0]?.reason || "").replace(/^error:\s*/, "").slice(0, 120)}). Showing regular results.`, true);
      return null;
    }
    judgments = Object.fromEntries(res.data.judgments.map((j) => [j.asin, j]));
    const relevance = Object.fromEntries(res.data.judgments.filter((j) => j.relevant !== null).map((j) => [j.asin, j.relevant]));
    return analyze({ phase: "ai", features, fetch_log: fetchLog, relevance, judgments: res.data.judgments,
                     feature_n: limits.featurePages, ai_n: aiN() });
  }

  let judgments = {}, lastFull = null, lastFeatures = {};
  async function run() {
    judgments = {}; lastFull = null; showAll = false;
    $(".start").disabled = true; $(".ai").hidden = true;
    await loadSettings();
    runs = []; fetchLog = [];
    status("Reading this page…");
    cards = await readSearchPages(limits.products);
    const quick = await analyze({ phase: "cards", feature_n: limits.featurePages, ai_n: aiN() });
    if (!quick) { $(".start").disabled = false; return; }
    last = quick; runs.push({ phase: "cards", run_id: quick.run_id });
    render(last, true);

    const features = await readFeatures(quick.fetch_targets || []);
    const full = await analyze({ phase: "features", features, fetch_log: fetchLog, feature_n: limits.featurePages, ai_n: aiN() });
    if (!full) { $(".start").disabled = false; return; }
    last = full; lastFull = full; lastFeatures = features;
    runs.push({ phase: "features", run_id: full.run_id });
    render(last, false);
    $(".ai").hidden = !full.stats.items;  // optional next step (nothing to filter on an empty page)
    $(".start").disabled = false;
  }

  let showAll = false;
  function render(r, preliminary) {
    const s = r.stats;
    $(".s-items").textContent = s.items;
    $(".s-rating").textContent = s.avg_rating ?? "–";
    $(".s-price").textContent = money(s.median_price);

    const filtered = Object.keys(judgments).length > 0;
    const rest = (r.ranked || []).filter((p) => !r.best_value || p.asin !== r.best_value.asin);
    const risky = (r.ranked || []).filter((p) => p.risk?.length).length;
    $(".find").textContent = r.best_value
      ? `💡 Best value found${risky ? ` · ${risky} high risk flagged` : ""}`
      : !s.items ? "No products with a price on this page"
      : !filtered ? "No product here has enough ratings to trust"
      : Object.values(judgments).some((j) => j.relevant)
        ? `${Object.values(judgments).filter((j) => j.relevant).length} match your search, but none has enough ratings to trust`
        : "Nothing on this page matches your search";
    $(".find").hidden = false;

    const tags = (p) => [
      ...(p.risk?.length ? [el("span", { className: "risk" }, el("b", { textContent: "HIGH RISK" }), ` ${p.risk.join(" · ")}`)] : []),
      ...(p.ad ? [el("span", { className: "adtag", textContent: "ad" })] : []),
      ...(p.unchecked ? [el("span", { className: "adtag", textContent: "not checked by AI" })] : []),
      ...(judgments[p.asin] ? [el("span", { className: "rel" }, `✓ ${judgments[p.asin].reason}`,
          ...(judgments[p.asin].quote ? [" ", el("q", { textContent: judgments[p.asin].quote })] : []))] : []),
    ];
    const v = r.best_value;
    $(".picks").replaceChildren(...(v ? [el("a", { className: "pick best", href: `/dp/${v.asin}`, target: "_blank", title: v.title || "" },
      el("span", { className: "k", textContent: "💡 Best value" }),
      el("span", { className: "t", textContent: v.title || v.asin }),
      el("span", { className: "n" }, facts(v)),
      ...(v.why ? [el("span", { className: "why", textContent: v.why })] : []),
      el("span", { className: "tags" }, ...tags(v)))] : []));
    $(".picks").hidden = !v;

    const shown = showAll ? rest : rest.slice(0, 15);
    $(".ins").replaceChildren(
      el("div", { className: "lh" }, !filtered ? `Ranked by value · ${rest.length} more`
        : `Also matching your search · ${rest.filter((p) => !p.unchecked).length}` + (rest.some((p) => p.unchecked) ? ` · ${rest.filter((p) => p.unchecked).length} not checked` : "")),
      ...shown.map((p, i) => el("a", { className: `li${p.risk?.length ? " hr" : ""}`, href: `/dp/${p.asin}`, target: "_blank", title: p.title || "" },
        el("span", { className: "rk", textContent: i + 2 }),
        el("span", { className: "t", textContent: p.title || p.asin }),
        el("span", { className: "n", textContent: facts(p) }),
        el("span", { className: "tags" }, ...tags(p)))),
      ...(rest.length > shown.length ? [Object.assign(el("button", { className: "more", textContent: `Show all ${rest.length}` }),
          { onclick: () => { showAll = true; render(last, false); } })] : []));
    $(".ins").hidden = !rest.length;

    if (!preliminary) {
      const known = r.features_known || 0;
      status(`✓ Ready to download ${s.items} products · review features read for ${known}`);
      root.querySelectorAll(".sec").forEach((b) => (b.disabled = false));
    }
    showDebug();
  }

  // ---------- export (in-page download) ----------
  function download(name, blob) {
    const a = el("a", { href: URL.createObjectURL(blob), download: name });
    root.append(a); a.click(); a.remove();
  }
  const fname = (ext) => `shelfscout_${(page.keyword || "search").replace(/\W+/g, "-")}_${new Date().toISOString().slice(0, 10)}.${ext}`;
  function marked() {
    const tag = {};
    for (const [k, label] of [["best_value", "best value"], ["best_price", "best price"], ["amazon_pick", "amazon pick"]])
      if (last[k]) tag[last[k].asin] = (tag[last[k].asin] ? tag[last[k].asin] + "; " : "") + label;
    return cards.map((c) => ({ ...c, pick: tag[c.asin] || "", link: `https://www.amazon.com/dp/${c.asin}` }));
  }
  $(".xl").onclick = async () => {
    status("Building Excel…");
    const res = await api("/api/export", "POST", { keyword: page.keyword, result: last, products: cards });
    if (!res || !res.ok) return status("Excel export failed (backend)", true);
    const bin = Uint8Array.from(atob(res.data.xlsx_b64), (ch) => ch.charCodeAt(0));
    download(fname("xlsx"), new Blob([bin], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
    status(`✓ Excel downloaded · Shortlist sheet first`);
  };
  $(".csv").onclick = () => {
    const cols = ["pick", "asin", "title", "price", "rating", "n_ratings", "organic_rank", "sponsored", "advertised", "bought_past_month", "link"];
    const q = (x) => `"${String(x ?? "").replace(/"/g, '""')}"`;
    download(fname("csv"), new Blob([[cols.join(","), ...marked().map((r) => cols.map((c) => q(r[c])).join(","))].join("\n")], { type: "text/csv" }));
  };
  $(".json").onclick = () => download(fname("json"), new Blob([JSON.stringify({ ...last, products: marked() }, null, 2)], { type: "application/json" }));

  $(".start").onclick = run;
})();
