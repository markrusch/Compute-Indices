// Shared by middleware.mjs (Edge runtime) and stats.js (Node serverless function): the
// one HTTP call both make against Upstash Redis's REST pipeline endpoint. A leading
// underscore keeps Vercel from routing this as its own /api/_upstash endpoint.
//
// Plain ESM so an Edge-runtime `import` and a CJS `await import()` both load it without
// either side changing module systems.

export async function upstashPipeline(base, token, commands) {
  const r = await fetch(`${base}/pipeline`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(commands),
  });
  if (!r.ok) throw new Error(`upstash ${r.status}`);
  return r.json();
}
