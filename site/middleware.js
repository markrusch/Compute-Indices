// Vercel Routing Middleware: same-origin visit logging, no cookie, no client script.
//
// WHY THIS EXISTS: Vercel Web Analytics (the one <script src> the site ships, see
// _ANALYTICS in outputs/site.py) is pageview counts only on the Hobby plan, with no
// per-path/session detail and a one-month window. This middleware runs server-side on
// every page request -- nothing is sent to a browser, nothing for a third party to see
// that Vercel didn't already see by serving the request -- and logs into Upstash Redis's
// free tier (500k commands/month) instead of upgrading the Vercel plan.
//
// NO COOKIE: a visitor is identified by SHA-256(day + ip + user-agent), rotated daily, so
// the same person is one "unique" within a day and an unlinkable different hash the next.
// That sidesteps the GDPR non-essential-cookie consent-banner requirement a persistent
// tracking cookie would trigger -- this site has no consent banner because it sets no
// cookie, not because it lacks a privacy notice. What is logged here, why, and for how
// long (RETENTION_SECONDS below) is described in PRIVACY.md and site/privacy.html.
//
// Requires two Vercel environment variables (Project Settings -> Environment Variables):
//   UPSTASH_REDIS_REST_URL     from a free Upstash Redis database
//   UPSTASH_REDIS_REST_TOKEN   its REST token
// Optional:
//   ANALYTICS_SALT             any secret string, mixed into the daily hash so a visitor
//                               id can't be recomputed by anyone who only knows their own
//                               IP and user-agent string.
//
// Both required vars missing (e.g. a preview deploy with no Upstash project attached)
// makes this a no-op: the request continues unlogged rather than failing the page.
//
// No dependency: Upstash's REST API is plain HTTPS, so this needs nothing beyond fetch
// and the platform's own Web Crypto -- same "fetch is enough" reasoning as api/refresh.js
// and api/contact.js.

import { upstashPipeline } from "./api/_upstash.mjs";

export const config = {
  // Skip the API routes, static assets and data/chart downloads: only an actual page
  // view is a visit worth counting, and every skipped path is one less Upstash command.
  matcher: ["/((?!api/|assets/|data/|charts/).*)"],
};

// 13 months, matching the retention stated in PRIVACY.md. Without this the per-day hit
// hash and unique-visitor sketch keys had no expiry and accumulated forever.
const RETENTION_SECONDS = 60 * 60 * 24 * 396;

async function dailyVisitorHash(day, request) {
  const ip =
    request.headers.get("x-real-ip") ||
    (request.headers.get("x-forwarded-for") || "").split(",")[0].trim() ||
    "unknown";
  const ua = request.headers.get("user-agent") || "unknown";
  const salt = process.env.ANALYTICS_SALT || "";
  const input = new TextEncoder().encode(`${salt}|${day}|${ip}|${ua}`);
  const digest = await crypto.subtle.digest("SHA-256", input);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function logHit(request) {
  const base = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!base || !token) return;

  const url = new URL(request.url);
  const path = url.pathname === "/" ? "/index.html" : url.pathname;
  const day = new Date().toISOString().slice(0, 10);
  const visitor = await dailyVisitorHash(day, request);
  const country = request.headers.get("x-vercel-ip-country") || "XX";
  const referer = (request.headers.get("referer") || "").slice(0, 300);
  const event = JSON.stringify({ t: Date.now(), path, country, referer });

  // One HTTP round trip for all six writes. The recent-events list is capped at 200 so
  // it stays a rolling window rather than growing without bound; the two per-day keys
  // are capped by time instead, via EXPIRE, since a calendar day can't be capped by count.
  const commands = [
    ["HINCRBY", `tci:day:${day}:hits`, path, 1],
    ["EXPIRE", `tci:day:${day}:hits`, RETENTION_SECONDS],
    ["PFADD", `tci:day:${day}:uniq`, visitor],
    ["EXPIRE", `tci:day:${day}:uniq`, RETENTION_SECONDS],
    ["LPUSH", "tci:recent", event],
    ["LTRIM", "tci:recent", 0, 199],
  ];

  try {
    await upstashPipeline(base, token, commands);
  } catch (err) {
    // A dropped log entry is not worth a retry loop or a slower page.
  }
}

export default function middleware(request, context) {
  context.waitUntil(logHit(request));
}
