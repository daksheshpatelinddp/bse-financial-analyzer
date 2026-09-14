/*
 * BSE FINANCIAL ANALYZER — BACKEND WORKER (UPGRADED)
 * KV binding: BSE_FIN_KV
 * Secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
 * (GEMINI_API_KEY is used by the separate analyzer.py GitHub Action, not this worker)
 */

const BSE_ANN_API =
  "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w";

const MAX_RECENT_SEEN = 800;
const MAX_ALERTS = 500;
const DISPLAY_LIMIT = 50;

const BURST_POLLS = 4;
const BURST_GAP_MS = 14000;

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

const FINANCIAL_KEYWORDS = [
  "financial result",
  "financial results",
  "audited result",
  "unaudited result",
  "quarterly result",
  "outcome of board meeting",
  "interim financial",
  "segment result"
];

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...CORS_HEADERS },
  });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function getIstDateStr() {
  const ist = new Date(Date.now() + 5.5 * 60 * 60 * 1000);
  const yyyy = ist.getUTCFullYear();
  const mm = String(ist.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(ist.getUTCDate()).padStart(2, "0");
  return `${yyyy}${mm}${dd}`;
}

function normalizeBseLink(rawLink) {
  var clean = String(rawLink || "").trim();
  if (!clean) return "https://www.bseindia.com";
  if (clean.indexOf("AttachLive") !== -1 || clean.indexOf("AttachHis") !== -1) {
    var fileName = clean.split("/").pop();
    if (fileName) return "https://www.bseindia.com/xml-data/corpfiling/AttachLive/" + fileName;
  }
  if (clean.indexOf("http") !== 0) {
    return clean.indexOf("/") === 0 ? "https://www.bseindia.com" + clean : "https://www.bseindia.com/" + clean;
  }
  return clean;
}

function isFinancialAnnouncement(headline, category) {
  const combined = `${headline} ${category}`.toLowerCase();
  return FINANCIAL_KEYWORDS.some((kw) => combined.includes(kw));
}

function escapeTelegramHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function sendTelegramAlert(company, scrip, headline, link, fetchedAt, env) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return;
  
  const pdfLink = normalizeBseLink(link);
  const formattedFetchTime = fetchedAt
    ? new Date(fetchedAt).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" })
    : "N/A";

  const messageText = 
    `🚨 <b>NEW FINANCIAL RESULT FILED</b>\n\n` +
    `🏢 <b>Company:</b> ${escapeTelegramHtml(company)} (${escapeTelegramHtml(scrip)})\n` +
    `📝 <b>Title:</b> ${escapeTelegramHtml(headline)}\n` +
    `⏱ <b>Fetched:</b> ${formattedFetchTime}\n\n` +
    (pdfLink ? `📄 <a href="${pdfLink}">Download BSE PDF Attachment</a>` : "No PDF Attached");

  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: env.TELEGRAM_CHAT_ID,
        text: messageText,
        parse_mode: "HTML",
        disable_web_page_preview: true,
      }),
    });
  } catch (err) {
    console.error("Telegram error:", err);
  }
}

function parsePubDate(row) {
  let pubDate = row.DissemDT || row.News_submission_dt || row.NEWS_DT || row.DT_TM || "";
  if (!pubDate) return "";
  pubDate = String(pubDate).trim();
  try {
    if (!pubDate.includes("Z") && !pubDate.includes("+") && pubDate.indexOf("-", 10) === -1) {
      pubDate = pubDate.replace(" ", "T") + "+05:30";
    }
    const d = new Date(pubDate);
    if (!isNaN(d.getTime())) return d.toISOString();
  } catch (e) {}
  return String(row.DissemDT || row.NEWS_DT || "");
}

function computeFingerprint(row) {
  const att = String(row.ATTACHMENTNAME || "").trim().toLowerCase();
  if (att) return `att:${att}`;
  const scrip = String(row.SCRIP_CD || "").trim();
  const title = String(row.HEADLINE || row.NEWSSUB || "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
  const day = String(row.DissemDT || row.NEWS_DT || "").slice(0, 10);
  return `st:${scrip}|${title}|${day}`;
}

function matchesWatchlist(row, watchlist) {
  if (!watchlist || !watchlist.length) return false;
  const itemScrip = String(row.SCRIP_CD || "").trim();
  const itemCompany = String(row.SLONGNAME || "").toLowerCase().trim();

  for (let i = 0; i < watchlist.length; i++) {
    const w = watchlist[i];
    const ws = String(w.scrip || "").trim();
    if (ws && itemScrip && ws === itemScrip) return true;
    const wn = String(w.name || "").toLowerCase().trim();
    if (wn.length >= 3 && itemCompany && itemCompany.indexOf(wn) !== -1) return true;
  }
  return false;
}

/* ---------- KV Helpers ---------- */

async function kvPut(env, key, value, attempts = 3) {
  let lastErr;
  for (let i = 0; i < attempts; i++) {
    try {
      await env.BSE_FIN_KV.put(key, value);
      return;
    } catch (err) {
      lastErr = err;
      if (i < attempts - 1) {
        await sleep(1050 + Math.floor(Math.random() * 400));
      }
    }
  }
  throw lastErr;
}

async function getWatchlist(env) {
  if (!env.BSE_FIN_KV) return [];
  const data = await env.BSE_FIN_KV.get("watchlist", "json");
  return Array.isArray(data) ? data : [];
}

async function setWatchlist(env, watchlist) {
  if (!env.BSE_FIN_KV) throw new Error("BSE_FIN_KV is not bound.");
  await kvPut(env, "watchlist", JSON.stringify(watchlist));
}

async function getNotificationSettings(env) {
  if (!env.BSE_FIN_KV) return { telegram: true };
  const data = await env.BSE_FIN_KV.get("notificationSettings", "json");
  return data || { telegram: true };
}

async function setNotificationSettings(env, settings) {
  if (!env.BSE_FIN_KV) throw new Error("BSE_FIN_KV is not bound.");
  await kvPut(env, "notificationSettings", JSON.stringify(settings));
}

async function getRecentSeen(env) {
  if (!env.BSE_FIN_KV) return [];
  const data = await env.BSE_FIN_KV.get("recentSeen", "json");
  return Array.isArray(data) ? data : [];
}

async function saveRecentSeen(env, ids) {
  if (!env.BSE_FIN_KV) return;
  await kvPut(env, "recentSeen", JSON.stringify(ids.slice(0, MAX_RECENT_SEEN)));
}

async function getAlertFingerprints(env) {
  if (!env.BSE_FIN_KV) return [];
  const data = await env.BSE_FIN_KV.get("alertFingerprints", "json");
  return Array.isArray(data) ? data : [];
}

async function saveAlertFingerprints(env, list) {
  if (!env.BSE_FIN_KV) return;
  await kvPut(env, "alertFingerprints", JSON.stringify(list.slice(0, MAX_ALERTS * 2)));
}

async function getAlerts(env) {
  if (!env.BSE_FIN_KV) return [];
  const data = await env.BSE_FIN_KV.get("specialAlerts", "json");
  return Array.isArray(data) ? data : [];
}

async function saveAlerts(env, alerts) {
  if (!env.BSE_FIN_KV) return;
  await kvPut(env, "specialAlerts", JSON.stringify(alerts.slice(0, MAX_ALERTS)));
}

// Tracks which alert fingerprints the Gemini/analyzer.py pipeline has already
// summarized, so the every-15-min GitHub Action doesn't re-download the same
// PDF and re-send the same Telegram summary on every run.
async function getGeminiProcessed(env) {
  if (!env.BSE_FIN_KV) return [];
  const data = await env.BSE_FIN_KV.get("geminiProcessed", "json");
  return Array.isArray(data) ? data : [];
}

async function markGeminiProcessed(env, fingerprints) {
  if (!env.BSE_FIN_KV || !fingerprints || !fingerprints.length) return;
  const existing = await getGeminiProcessed(env);
  const set = new Set(existing);
  fingerprints.forEach((fp) => set.add(fp));
  await kvPut(env, "geminiProcessed", JSON.stringify(Array.from(set).slice(0, MAX_ALERTS * 2)));
}

/* ---------- Core Polling Logic ---------- */

const FETCH_TIMEOUT_MS = 8000;
const FETCH_MAX_ATTEMPTS = 3;

async function fetchJsonPage1Once() {
  const dateStr = getIstDateStr();
  const url =
    `${BSE_ANN_API}?pageno=1` +
    `&strCat=-1&subcategory=-1` +
    `&strPrevDate=${dateStr}&strToDate=${dateStr}` +
    `&strSearch=P&strscrip=&strType=C`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      method: "GET",
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        Accept: "application/json, text/plain, */*",
        Referer: "https://www.bseindia.com/",
        Origin: "https://www.bseindia.com",
        "Cache-Control": "no-cache",
      },
      cf: { cacheTtl: 0, cacheEverything: false },
      signal: controller.signal,
    });

    if (!response.ok) throw new Error(`BSE JSON HTTP ${response.status}`);
    const data = await response.json();
    return data && Array.isArray(data.Table) ? data.Table : [];
  } finally {
    clearTimeout(timer);
  }
}

// Retries transient network failures (e.g. "fetch failed") with backoff -
// same fix already proven on the sibling bse-fastest-jsonapi worker.
async function fetchJsonPage1() {
  let lastErr;
  for (let i = 0; i < FETCH_MAX_ATTEMPTS; i++) {
    try {
      return await fetchJsonPage1Once();
    } catch (err) {
      lastErr = err;
      if (i < FETCH_MAX_ATTEMPTS - 1) await sleep(800 + i * 700);
    }
  }
  throw lastErr;
}

async function pollOnce(env, cachedWatchlist) {
  const fetchedAt = new Date().toISOString();
  let rows = [];
  try {
    rows = await fetchJsonPage1();
  } catch (err) {
    console.error("fetch failed:", err);
    return { ok: false, error: String(err), newAnnouncements: 0, newAlerts: 0 };
  }

  if (!rows.length) return { ok: true, newAnnouncements: 0, newAlerts: 0, rows: 0 };

  const page = [];
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const fp = computeFingerprint(row);
    if (!fp) continue;
    page.push({ row, fp });
  }

  const recentSeen = await getRecentSeen(env);
  const seenSet = new Set(recentSeen);

  if (recentSeen.length === 0) {
    await saveRecentSeen(env, page.map((p) => p.fp));
    return { ok: true, status: "baseline", newAnnouncements: 0, newAlerts: 0, rows: rows.length };
  }

  const newOnes = [];
  for (let i = 0; i < page.length; i++) {
    if (!seenSet.has(page[i].fp)) newOnes.push(page[i]);
  }

  if (newOnes.length === 0) return { ok: true, newAnnouncements: 0, newAlerts: 0, rows: rows.length };

  const watchlist = cachedWatchlist || (await getWatchlist(env));
  const settings = await getNotificationSettings(env);

  let newAlertCount = 0;
  let alerts = null;
  let alertFpSet = null;

  if (watchlist.length > 0) {
    for (let i = 0; i < newOnes.length; i++) {
      const { row, fp } = newOnes[i];

      if (!matchesWatchlist(row, watchlist)) continue;

      const headline = String(row.HEADLINE || row.NEWSSUB || "").trim();
      const category = String(row.CATEGORYNAME || "").trim();
      if (!isFinancialAnnouncement(headline, category)) continue;

      if (!alertFpSet) {
        alertFpSet = new Set(await getAlertFingerprints(env));
        alerts = await getAlerts(env);
      }
      if (alertFpSet.has(fp)) continue;

      const company = String(row.SLONGNAME || "").trim() || "Scrip";
      const scrip = String(row.SCRIP_CD || "").trim();
      let link = "";
      if (row.ATTACHMENTNAME) {
        link = `https://www.bseindia.com/xml-data/corpfiling/AttachLive/${row.ATTACHMENTNAME}`;
      } else if (row.NSURL) {
        link = row.NSURL;
      }

      if (settings.telegram !== false) {
        await sendTelegramAlert(company, scrip, headline, link, fetchedAt, env);
      }

      const pubDate = parsePubDate(row);

      alerts.unshift({
        company,
        scrip,
        title: headline,
        link,
        fingerprint: fp,
        pubDate,
        fetchedAt,
        alert: true,
        alertCreatedAt: new Date().toISOString(),
      });
      alertFpSet.add(fp);
      newAlertCount++;
    }
  }

  const updatedSeen = [];
  const addSet = new Set();
  for (let i = 0; i < newOnes.length; i++) {
    const fp = newOnes[i].fp;
    if (!addSet.has(fp)) {
      addSet.add(fp);
      updatedSeen.push(fp);
    }
  }
  for (let i = 0; i < recentSeen.length; i++) {
    if (updatedSeen.length >= MAX_RECENT_SEEN) break;
    if (!addSet.has(recentSeen[i])) {
      addSet.add(recentSeen[i]);
      updatedSeen.push(recentSeen[i]);
    }
  }
  await saveRecentSeen(env, updatedSeen);

  if (newAlertCount > 0 && alerts && alertFpSet) {
    await saveAlerts(env, alerts);
    await saveAlertFingerprints(env, Array.from(alertFpSet));
  }

  return {
    ok: true,
    newAnnouncements: newOnes.length,
    newAlerts: newAlertCount,
    rows: rows.length,
  };
}

async function pollBurst(env) {
  const results = [];
  let totalNew = 0;
  let totalAlerts = 0;
  const watchlist = await getWatchlist(env);

  for (let i = 0; i < BURST_POLLS; i++) {
    const r = await pollOnce(env, watchlist);
    results.push(r);
    totalNew += r.newAnnouncements || 0;
    totalAlerts += r.newAlerts || 0;
    if (i < BURST_POLLS - 1) await sleep(BURST_GAP_MS);
  }

  return { ok: true, mode: "burst", newAnnouncements: totalNew, newAlerts: totalAlerts, results };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS_HEADERS });

    try {
      if (url.pathname === "/") {
        return json({
          status: "running",
          app: "BSE Financial Results Analyzer Backend",
          version: "1.1.0",
        });
      }

      if (url.pathname === "/monitor") {
        const burst = url.searchParams.get("burst") === "1";
        if (burst) return json(await pollBurst(env));
        return json(await pollOnce(env));
      }

      if (url.pathname === "/watchlist") {
        if (request.method === "GET") return json({ ok: true, watchlist: await getWatchlist(env) });
        if (request.method === "POST") {
          const body = await request.json();
          await setWatchlist(env, body.watchlist || []);
          return json({ ok: true, watchlist: body.watchlist });
        }
      }

      if (url.pathname === "/watchlist/overwrite" && request.method === "POST") {
        const body = await request.json();
        const rawWatchlist = body.watchlist || [];
        // Format strings or numbers into valid watchlist objects matching worker structure [{ scrip: "..." }]
        const formattedWatchlist = rawWatchlist.map((item) => {
          if (typeof item === "object" && item !== null) return item;
          return { scrip: String(item).trim() };
        });
        await setWatchlist(env, formattedWatchlist);
        return json({ ok: true, count: formattedWatchlist.length, watchlist: formattedWatchlist });
      }

      if (url.pathname === "/notification-settings") {
        if (request.method === "GET") return json({ ok: true, settings: await getNotificationSettings(env) });
        if (request.method === "POST") {
          const body = await request.json();
          await setNotificationSettings(env, body);
          return json({ ok: true, settings: body });
        }
      }

      if (url.pathname === "/announcements" || url.pathname === "/alerts") {
        let items = (await getAlerts(env)).slice(0, DISPLAY_LIMIT);
        if (url.searchParams.get("pending") === "1") {
          const processed = new Set(await getGeminiProcessed(env));
          items = items.filter((it) => !processed.has(it.fingerprint));
        }
        return json({ ok: true, count: items.length, items });
      }

      if (url.pathname === "/alerts/mark-processed" && request.method === "POST") {
        const body = await request.json();
        await markGeminiProcessed(env, body.fingerprints || []);
        return json({ ok: true });
      }

      return json({ error: "Not found" }, 404);
    } catch (err) {
      return json({ error: err.message }, 500);
    }
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(pollBurst(env));
  },
};