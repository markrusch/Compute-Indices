// Vercel serverless function: GET /api/stats?key=...&days=14
//
// The only viewer for what middleware.mjs logs into Upstash Redis. There is no
// dashboard on either side of that pipe, so this renders one: hits and unique visitors
// per day, the busiest paths, and the most recent raw events (path, country, referrer).
//
// Requires the same UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN as middleware.mjs,
// plus:
//   STATS_KEY   any secret string. Without a matching ?key= query param this refuses to
//               serve -- the data behind it is otherwise-private visitor traffic, and
//               nothing else on this site sits behind a password.
//
// No dependency: same reasoning as middleware.mjs -- Upstash's REST API is plain HTTPS.

const MAX_DAYS = 30;
const DEFAULT_DAYS = 14;
const RECENT_COUNT = 50;

function pairsToObject(flat) {
  const out = {};
  for (let i = 0; i < flat.length; i += 2) out[flat[i]] = flat[i + 1];
  return out;
}

function lastNDays(n) {
  const days = [];
  const now = new Date();
  for (let i = 0; i < n; i++) {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - i));
    days.push(d.toISOString().slice(0, 10));
  }
  return days;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

module.exports = async (req, res) => {
  if (req.method !== "GET") {
    res.status(405).json({ ok: false, reason: "method_not_allowed" });
    return;
  }

  const statsKey = process.env.STATS_KEY;
  const givenKey = (req.query && req.query.key) || "";
  if (!statsKey || givenKey !== statsKey) {
    res.status(statsKey ? 401 : 500).json({
      ok: false,
      reason: statsKey ? "unauthorized" : "not_configured",
    });
    return;
  }

  const base = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!base || !token) {
    res.status(500).json({ ok: false, reason: "not_configured" });
    return;
  }

  const requested = parseInt((req.query && req.query.days) || DEFAULT_DAYS, 10);
  const nDays = Math.min(MAX_DAYS, Math.max(1, Number.isFinite(requested) ? requested : DEFAULT_DAYS));
  const days = lastNDays(nDays);

  const commands = [];
  for (const day of days) {
    commands.push(["HGETALL", `tci:day:${day}:hits`]);
    commands.push(["PFCOUNT", `tci:day:${day}:uniq`]);
  }
  commands.push(["LRANGE", "tci:recent", 0, RECENT_COUNT - 1]);

  let results;
  try {
    const { upstashPipeline } = await import("./_upstash.mjs");
    results = await upstashPipeline(base, token, commands);
  } catch (err) {
    res.status(502).json({ ok: false, reason: "upstash_error" });
    return;
  }

  const perDay = days.map((day, i) => {
    const hits = pairsToObject(results[i * 2].result || []);
    const uniq = results[i * 2 + 1].result || 0;
    const total = Object.values(hits).reduce((a, b) => a + Number(b), 0);
    return { day, hits, uniq, total };
  });
  const recent = (results[days.length * 2].result || []).map((s) => {
    try {
      return JSON.parse(s);
    } catch {
      return null;
    }
  }).filter(Boolean);

  const grandTotal = perDay.reduce((a, d) => a + d.total, 0);
  const pathTotals = {};
  for (const d of perDay) {
    for (const [path, count] of Object.entries(d.hits)) {
      pathTotals[path] = (pathTotals[path] || 0) + Number(count);
    }
  }
  const topPaths = Object.entries(pathTotals).sort((a, b) => b[1] - a[1]).slice(0, 20);

  const dayRows = perDay.map((d) => (
    `<tr><td>${escapeHtml(d.day)}</td><td class="n">${d.total}</td><td class="n">${d.uniq}</td></tr>`
  )).join("");
  const pathRows = topPaths.map(([path, count]) => (
    `<tr><td>${escapeHtml(path)}</td><td class="n">${count}</td></tr>`
  )).join("");
  const recentRows = recent.map((e) => (
    `<tr><td>${escapeHtml(new Date(e.t).toISOString())}</td><td>${escapeHtml(e.path)}</td>` +
    `<td>${escapeHtml(e.country)}</td><td>${escapeHtml(e.referer || "—")}</td></tr>`
  )).join("");

  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>TCI traffic</title>
<meta name="robots" content="noindex, nofollow">
<style>
body{background:#0b0c0d;color:#d8d8d8;font:14px/1.5 -apple-system,Segoe UI,sans-serif;
     margin:0;padding:32px max(16px,env(safe-area-inset-left))}
h1{font-size:18px;margin:0 0 4px}
h2{font-size:14px;color:#9a9a9a;margin:32px 0 8px}
table{border-collapse:collapse;width:100%;max-width:720px}
td,th{padding:4px 12px 4px 0;border-bottom:1px solid #262626;text-align:left}
.n{text-align:right;font-variant-numeric:tabular-nums}
.sub{color:#9a9a9a;font-size:12px}
</style></head>
<body>
<h1>Traffic, last ${nDays} day${nDays === 1 ? "" : "s"}</h1>
<p class="sub">${grandTotal} page views logged, no cookie, hashed IP+UA rotated daily.</p>
<h2>By day</h2>
<table><thead><tr><th>Day</th><th class="n">Views</th><th class="n">Unique</th></tr></thead>
<tbody>${dayRows}</tbody></table>
<h2>Top paths</h2>
<table><thead><tr><th>Path</th><th class="n">Views</th></tr></thead>
<tbody>${pathRows}</tbody></table>
<h2>Most recent</h2>
<table><thead><tr><th>Time (UTC)</th><th>Path</th><th>Country</th><th>Referrer</th></tr></thead>
<tbody>${recentRows}</tbody></table>
</body></html>`;

  res.setHeader("Cache-Control", "no-store");
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.status(200).send(html);
};
