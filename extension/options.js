// ShelfScout settings: the user's own AI keys (stored in this browser only) and the developer benchmark.
const NAMES = { gemini: "Google Gemini", openai: "OpenAI", anthropic: "Anthropic Claude" };
const KEY_PAGES = {
  gemini: ["https://aistudio.google.com/apikey", "Get a free Gemini key ↗"],
  openai: ["https://platform.openai.com/api-keys", "Get an OpenAI key ↗"],
  anthropic: ["https://console.anthropic.com/settings/keys", "Get an Anthropic key ↗"],
};
const $ = (id) => document.getElementById(id);
const api = (path, method, body) => new Promise((res) =>
  chrome.runtime.sendMessage({ type: "api", path, method, body }, (r) => res(chrome.runtime.lastError ? null : r)));

async function load() { return (await chrome.storage.local.get("ai")).ai || { keys: {}, models: {}, use: null }; }
async function store(ai) { await chrome.storage.local.set({ ai }); }
const mask = (k) => k.slice(0, 4) + "…" + k.slice(-4);

function showKeyLink() {
  const [url, text] = KEY_PAGES[$("provider").value];
  $("getkey").href = url; $("getkey").textContent = text;
}

async function render() {
  const ai = await load();
  const provs = Object.keys(ai.keys);
  $("saved").replaceChildren(...(provs.length ? provs.map((p) => {
    const row = document.createElement("div");
    const rm = Object.assign(document.createElement("button"), { className: "sec", textContent: "Remove" });
    rm.onclick = async () => { delete ai.keys[p]; delete ai.models[p]; if (ai.use === p) ai.use = Object.keys(ai.keys)[0] || null; await store(ai); render(); };
    row.append(`${NAMES[p]} · ${mask(ai.keys[p])} · ${ai.models[p] || ""}`, rm);
    return row;
  }) : [Object.assign(document.createElement("div"), { className: "note", textContent: "No keys yet. AI mode is off until you add one." })]));
  $("use").replaceChildren(...provs.map((p) => Object.assign(document.createElement("option"), { value: p, textContent: NAMES[p], selected: ai.use === p })));
  $("use").disabled = !provs.length;
}

$("provider").onchange = showKeyLink;
$("key").addEventListener("paste", () => setTimeout(() => $("save").click(), 50));  // paste = test right away
$("save").onclick = async () => {
  const provider = $("provider").value, key = $("key").value.trim();
  if (!key) { $("msg").className = "msg bad"; $("msg").textContent = "Paste a key first."; return; }
  $("save").disabled = true; $("msg").className = "msg"; $("msg").textContent = "Checking…";
  const r = await api("/api/ai/test", "POST", { provider, key });
  $("save").disabled = false;
  if (!r) { $("msg").className = "msg bad"; $("msg").textContent = "Can't reach the ShelfScout server (localhost:8100)."; return; }
  if (!r.data?.ok) { $("msg").className = "msg bad"; $("msg").textContent = r.data?.error || "That didn't work."; return; }
  const ai = await load();
  ai.keys[provider] = key; ai.models[provider] = r.data.model; ai.use = ai.use || provider;
  await store(ai);
  $("key").value = "";
  $("msg").className = "msg ok"; $("msg").textContent = `✓ Working · ${r.data.model}`;
  render();
};
$("use").onchange = async () => { const ai = await load(); ai.use = $("use").value; await store(ai); };

// ---------- limits (blank = default) ----------
const LIM = { "lim-products": "products", "lim-features": "featurePages", "lim-ai": "aiChecks" };
async function loadLimits() {
  const { limits = {} } = await chrome.storage.local.get("limits");
  for (const [id, k] of Object.entries(LIM)) $(id).value = limits[k] ? limits[k] : (k === "featurePages" && limits[k] === 0 ? 0 : "");
}
async function saveLimits() {
  const limits = {};
  for (const [id, k] of Object.entries(LIM)) {
    const v = $(id).value.trim();
    if (v !== "") limits[k] = Math.max(0, Math.round(+v));
  }
  await chrome.storage.local.set({ limits });
  $("limmsg").className = "msg ok"; $("limmsg").textContent = "Saved";
  setTimeout(() => ($("limmsg").textContent = ""), 1200);
}
Object.keys(LIM).forEach((id) => ($(id).onchange = saveLimits));
$("lim-reset").onclick = async () => { await chrome.storage.local.set({ limits: {} }); loadLimits(); $("limmsg").className = "msg ok"; $("limmsg").textContent = "Defaults restored"; };

showKeyLink();
render();
loadLimits();
