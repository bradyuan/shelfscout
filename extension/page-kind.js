// What kind of Amazon page this is. Decides what the dock offers.
//   search  : /s?k=... or a category listing  -> "Analyze this search"
//   product : /dp/ASIN, /gp/product/ASIN       -> "Analyze this product"
//   never   : cart, checkout, sign-in, account  -> no dock at all
//   other   : anywhere else                     -> dock with a hint only
globalThis.SSPageKind ??= (() => {  // ??= : safe if the script is injected twice
  const NEVER = [/^\/gp\/cart\//i, /^\/cart(\/|$)/i, /^\/gp\/buy\//i, /^\/checkout(\/|$)/i, /^\/gp\/checkout/i,
    /^\/ap\//i, /^\/ax\//i, /^\/gp\/css\//i, /^\/gp\/your-account/i, /^\/your-account/i, /^\/your-orders/i,
    /^\/a\/addresses/i, /^\/cpe\/yourpayments/i];
  const PRODUCT = /\/(?:dp|gp\/product|gp\/aw\/d)\/([A-Z0-9]{10})/i;

  function classify(href) {
    let u;
    try { u = new URL(href); } catch { return { kind: "never" }; }
    if (!/(^|\.)amazon\.com$/i.test(u.hostname)) return { kind: "never" };
    const path = u.pathname;
    if (NEVER.some((re) => re.test(path))) return { kind: "never" };
    const m = path.match(PRODUCT);
    if (m) return { kind: "product", asin: m[1].toUpperCase() };
    if (path === "/s" || path.startsWith("/s/")) {
      const k = u.searchParams.get("k") || u.searchParams.get("field-keywords");
      if (k || u.searchParams.get("rh") || u.searchParams.get("i") || u.searchParams.get("node")) {
        return { kind: "search", keyword: k ? k.trim() : null };
      }
    }
    return { kind: "other" };
  }
  return { classify };
})();
