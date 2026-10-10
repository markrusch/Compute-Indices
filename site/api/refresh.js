// Vercel serverless function: POST /api/refresh?date=YYYY-MM-DD
//
// Triggers daily.yml via workflow_dispatch when (and only when) the requested date is
// today in UTC -- collectors report live market prices, not history, so a past date can
// never be honestly re-collected; this endpoint refuses rather than fake it.
//
// WHY IT NEEDS A SECRET AND REFUSES BEFORE 11:00 UTC. Until 10 October 2026 this endpoint
// took an anonymous GET or POST. A collector that already has an `ok` run for the day is
// skipped by the 11:00 job (`has_ok_run` in collectors/base.py), so a dispatch made before
// 11:00 does not add to the fixing, it *becomes* the fixing: the day's prints are then
// computed from prices read whenever the caller chose. In the record, the first revision
// of the day was computed before 11:00 on 16 August (09:04), 12 September (08:32, a
// workflow dispatch) and 17 September (06:27 UTC). After 11:00 each call stored a new
// revision of every series and committed the database again, about twenty times an hour
// if someone looped it. So:
//   - only POST: a GET that starts a collection can be fired by a link prefetcher or crawler;
//   - only with `Authorization: Bearer <REFRESH_SECRET>`, compared in constant time;
//   - never before the 11:00 UTC fixing, whoever asks;
//   - GitHub's error body is not echoed to the caller.
// Nothing on the site calls this endpoint. It is a catch-up lever for the administrator,
// for a day the scheduled run failed after 11:00.
//
// Requires three Vercel environment variables (Project Settings -> Environment Variables,
// never committed to the repo). Without REFRESH_SECRET the endpoint refuses every request:
//   GITHUB_DISPATCH_TOKEN  a token scoped to just this repo's Actions (read/write),
//                          e.g. a fine-grained PAT limited to markrusch/Compute-Indices.
//   GITHUB_REPO            "markrusch/Compute-Indices" (owner/repo)
//   REFRESH_SECRET         a long random string; the caller sends it as a bearer token.
//
// No dependencies: Vercel's Node runtime ships a global fetch and node:crypto.

const crypto = require("node:crypto");

const WORKFLOW_FILE = "daily.yml";
const RECENT_RUN_WINDOW_MINUTES = 3; // avoid duplicate dispatches from repeated clicks
const FIXING_HOUR_UTC = 11; // daily.yml's cron; a collection before it would become the fixing

function todayUtc(now) {
  return now.toISOString().slice(0, 10);
}

// Hashing both sides first gives timingSafeEqual two buffers of the same length, so the
// comparison leaks neither the secret's content nor its length.
function secretMatches(given, expected) {
  const a = crypto.createHash("sha256").update(String(given)).digest();
  const b = crypto.createHash("sha256").update(String(expected)).digest();
  return crypto.timingSafeEqual(a, b);
}

function bearer(req) {
  const header = (req.headers && (req.headers.authorization || req.headers.Authorization)) || "";
  const match = /^Bearer\s+(.+)$/i.exec(String(header).trim());
  return match ? match[1].trim() : "";
}

async function githubApi(repo, token, path, init) {
  const res = await fetch(`https://api.github.com/repos/${repo}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init && init.headers),
    },
  });
  return res;
}

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    res.status(405).json({ ok: false, reason: "method_not_allowed" });
    return;
  }

  const secret = process.env.REFRESH_SECRET;
  if (!secret) {
    res.status(503).json({
      ok: false,
      reason: "not_configured",
      message: "Refresh is closed on this deployment.",
    });
    return;
  }
  const given = bearer(req);
  if (!given || !secretMatches(given, secret)) {
    res.status(401).json({ ok: false, reason: "unauthorized" });
    return;
  }

  const now = new Date();
  const today = todayUtc(now);
  const date = (req.query && req.query.date) || today;
  if (date !== today) {
    res.status(400).json({
      ok: false,
      reason: "not_today",
      message:
        "Only today's collection can be requested. Collectors report live market prices, " +
        "not history, so a past date can't be retroactively collected.",
    });
    return;
  }
  if (now.getUTCHours() < FIXING_HOUR_UTC) {
    res.status(409).json({
      ok: false,
      reason: "before_fixing",
      message:
        "The day's prices are read at the 11:00 UTC fixing. A collection before it would " +
        "replace the fixing's observations, so it is refused.",
    });
    return;
  }

  const token = process.env.GITHUB_DISPATCH_TOKEN;
  const repo = process.env.GITHUB_REPO || "markrusch/Compute-Indices";
  if (!token) {
    res.status(503).json({
      ok: false,
      reason: "not_configured",
      message: "Live refresh isn't configured on this deployment (missing dispatch token).",
    });
    return;
  }

  try {
    const runsRes = await githubApi(
      repo,
      token,
      `/actions/workflows/${WORKFLOW_FILE}/runs?per_page=1`
    );
    if (runsRes.ok) {
      const runsBody = await runsRes.json();
      const latestRun = runsBody.workflow_runs && runsBody.workflow_runs[0];
      if (latestRun) {
        const ageMinutes = (Date.now() - new Date(latestRun.created_at).getTime()) / 60000;
        const stillActive = latestRun.status === "in_progress" || latestRun.status === "queued";
        if (stillActive || ageMinutes < RECENT_RUN_WINDOW_MINUTES) {
          res.status(200).json({
            ok: true,
            dispatched: false,
            alreadyRunning: true,
            message: "A collection run is already in progress or just completed.",
            run_url: latestRun.html_url,
          });
          return;
        }
      }
    }

    const dispatchRes = await githubApi(
      repo,
      token,
      `/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      { method: "POST", body: JSON.stringify({ ref: "main" }) }
    );

    if (dispatchRes.status === 204) {
      res.status(200).json({
        ok: true,
        dispatched: true,
        message: "Collection run requested.",
      });
      return;
    }

    res.status(502).json({
      ok: false,
      reason: "github_error",
      message: `GitHub API returned ${dispatchRes.status}.`,
    });
  } catch (err) {
    res.status(502).json({
      ok: false,
      reason: "github_error",
      message: "Could not reach the GitHub API.",
    });
  }
};
