// Relays calls from the Amazon page to the local backend (avoids CORS / mixed-content issues).
const API = "http://localhost:8100";

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type === "open-options") { chrome.runtime.openOptionsPage(); return; }
  if (msg.type !== "api") return;
  fetch(API + msg.path, {
    method: msg.method || "GET",
    headers: { "Content-Type": "application/json" },
    body: msg.body ? JSON.stringify(msg.body) : undefined,
  })
    .then(async (r) => reply({ ok: r.ok, status: r.status, data: await r.json().catch(() => null) }))
    .catch((e) => reply({ ok: false, status: 0, error: String(e) }));
  return true; // async reply
});

// Toolbar icon: open the dock on the current Amazon tab. If the tab was opened before the
// extension (re)loaded, its content scripts are missing, so inject them first.
chrome.action.onClicked.addListener(async (tab) => {
  const open = () => chrome.tabs.sendMessage(tab.id, { type: "open-dock" });
  try { await open(); return; } catch {}
  if (!/^https:\/\/www\.amazon\.com\//.test(tab.url || "")) {
    chrome.action.setBadgeText({ tabId: tab.id, text: "?" });
    chrome.action.setTitle({ tabId: tab.id, title: "ShelfScout: open an Amazon search page first" });
    return;
  }
  try {
    // Already loaded but no panel here (cart, checkout, sign-in pages hide it on purpose): don't inject twice.
    const [{ result: loaded }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => !!window.__shelfscout || typeof SSPageKind !== "undefined" });
    if (loaded) {
      chrome.action.setBadgeText({ tabId: tab.id, text: "?" });
      chrome.action.setTitle({ tabId: tab.id, title: "ShelfScout: open an Amazon search page to use it" });
      return;
    }
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["page-kind.js", "parsers.js", "content.js"] });
    await open();
    chrome.action.setBadgeText({ tabId: tab.id, text: "" });
  } catch (e) {
    chrome.action.setBadgeText({ tabId: tab.id, text: "!" });
    chrome.action.setTitle({ tabId: tab.id, title: "ShelfScout couldn't start here: " + e });
  }
});
