// Pure parsing of Amazon documents (the live page or a fetched one). No storage, no navigation.
globalThis.SSParsers ??= (() => {  // ??= : safe if the script is injected twice
  const txt = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : null);
  const num = (s) => {
    const m = String(s || "").replace(/,/g, "").match(/\d+(\.\d+)?/);
    return m ? parseFloat(m[0]) : null;
  };
  const SPONSORED = '.puis-sponsored-label-text, .s-sponsored-label-text, [data-component-type="sp-sponsored-result"], a[href*="/sspa/"]';

  // What a page is when it has nothing useful: a block, a sign-in wall, or fine.
  function blockKind(doc, url) {
    const path = (() => { try { return new URL(url, location.origin).pathname; } catch { return ""; } })();
    if (doc.querySelector('form[action*="validateCaptcha"], #captchacharacters') || /robot check/i.test(doc.title || "")) return "captcha";
    if (/\/checkpoint\//.test(path)) return "bot_check";
    if (doc.querySelector('a[href*="cs_503"], a[href*="cs_404"]') && !doc.querySelector('[data-component-type="s-search-result"]')) return "error_page";
    if (/^\/ap\/(signin|mfa|cvf)/.test(path) || doc.querySelector('form[name="signIn"], #ap_email')) return "signin";
    return null;
  }

  // ---------- search results page ----------
  // One row per product. A product can appear twice (an ad slot and an organic slot):
  // organic_rank comes from its organic slot; `advertised` says it also has an ad slot;
  // `sponsored` is true only when it appears as an ad and nowhere organically.
  function searchCards(doc) {
    let organic = 0;
    const byAsin = new Map();
    const out = [];
    doc.querySelectorAll('[data-component-type="s-search-result"][data-asin]').forEach((card, i) => {
      const asin = card.dataset.asin;
      if (!asin) return;
      const isAd = !!card.querySelector(SPONSORED) || /^\s*Sponsored\s*$/m.test(card.innerText || "");
      const prev = byAsin.get(asin);
      if (prev) {
        if (isAd) prev.advertised = true;
        else if (prev.organic_rank === null) { prev.organic_rank = ++organic; prev.sponsored = false; prev.position = i + 1; }
        return;
      }
      const sponsored = isAd;
      const ratingLabel = txt(card.querySelector(".a-icon-alt"));
      const countEl = card.querySelector('[aria-label$="ratings"], [aria-label$="rating"], a[href*="customerReviews"] span');
      const bought = (card.innerText || "").match(/([\d.,]+K?\+?) bought in past month/i);
      const row = {
        asin,
        position: i + 1,
        organic_rank: sponsored ? null : ++organic,
        sponsored,
        advertised: sponsored,
        // clothing/shoe cards split brand and product name into two h2s ("Skechers" + "Women's Go Run...")
        title: [...new Set([...card.querySelectorAll("h2")].map(txt).filter(Boolean))].join(" ") || null,
        price: num(txt(card.querySelector(".a-price .a-offscreen"))),
        rating: ratingLabel ? num(ratingLabel) : null,
        n_ratings: countEl ? num(countEl.getAttribute("aria-label") || txt(countEl)) : null,
        badges: [...card.querySelectorAll(".a-badge-text, [id$='-best-seller-label'], .s-label-popover-default")].map(txt).filter(Boolean),
        bought_past_month: bought ? bought[1] : null,
      };
      byAsin.set(asin, row);
      out.push(row);
    });
    return out.sort((a, b) => (a.organic_rank ?? 1e9) - (b.organic_rank ?? 1e9));
  }

  // ---------- product page ----------
  function productPage(doc) {
    return {
      title: txt(doc.querySelector("#productTitle")),
      price: num(txt(doc.querySelector(".a-price .a-offscreen"))),
      rating: num(txt(doc.querySelector("#acrPopover .a-icon-alt"))),
      n_ratings: num(txt(doc.querySelector("#acrCustomerReviewText"))),
      breadcrumb: [...doc.querySelectorAll("#wayfinding-breadcrumbs_feature_div a")].map(txt).filter(Boolean),
    };
  }

  // ---------- product page: Amazon's per-feature review summary ----------
  // e.g. "31 customers mention quality, 24 positive, 7 negative" + summary + 3-4 quotes.
  // Counted by Amazon over all reviews; present in the raw HTML, signed in or out.
  function features(doc) {
    return [...doc.querySelectorAll('[id^="rh_controls_aspect_"]')].map((b) => {
      const head = txt(b.querySelector('[data-testid="mentions-inline"] span')) || "";
      const m = head.match(/([\d,]+) customers? mentions? (.+?), ([\d,]+) positive, ([\d,]+) negative/i);
      if (!m) return null;
      const quotes = [...b.querySelectorAll('[data-testid="read-more"]')]
        .map((r) => (txt(r.parentElement) || "").replace(/\s*Read more\s*$/, "").replace(/^[\s"“…\.]+|[\s"”]+$/g, ""))
        .filter(Boolean);
      return { name: m[2].toLowerCase(), mentions: num(m[1]), positive: num(m[3]), negative: num(m[4]),
               summary: txt(b.querySelector('[data-testid="aspect-summary"]')), quotes };
    }).filter(Boolean);
  }

  // "65 percent of reviews have 5 stars" -> {5: 65, ...}
  function starBreakdown(doc) {
    const out = {};
    doc.querySelectorAll('[aria-label*="percent of reviews have"]').forEach((e) => {
      const m = e.getAttribute("aria-label").match(/(\d+) percent of reviews have (\d) star/);
      if (m && !(m[2] in out)) out[m[2]] = +m[1];
    });
    return out;
  }

  // ---------- review pages ----------
  function reviews(doc, asin, source) {
    return [...doc.querySelectorAll('[data-hook="review"]')].map((rv) => {
      const body = rv.querySelector('[data-hook="review-body"]');
      if (!body) return null;
      const star = rv.querySelector('[data-hook="review-star-rating"], [data-hook="cmps-review-star-rating"]');
      let title = txt(rv.querySelector('[data-hook="review-title"]'));
      if (title) title = title.replace(/^[\d.]+ out of 5 stars\s*/, "");
      return {
        review_id: rv.id || null, asin, source,
        stars: star ? num(txt(star)) : null,
        title,
        text: txt(body).replace(/\s*Read more\s*$/, ""),
        date: txt(rv.querySelector('[data-hook="review-date"]')),
        verified: !!rv.querySelector('[data-hook="avp-badge"], [data-hook="avp-badge-linkless"]'),
        helpful: txt(rv.querySelector('[data-hook="helpful-vote-statement"]')),
        variant: txt(rv.querySelector('[data-hook="format-strip"]')),
      };
    }).filter(Boolean);
  }

  function hooks(doc) {
    const h = {};
    doc.querySelectorAll("[data-hook]").forEach((e) => (h[e.dataset.hook] = (h[e.dataset.hook] || 0) + 1));
    return h;
  }

  return { blockKind, searchCards, productPage, features, starBreakdown, reviews, hooks };
})();
