// Vercel serverless function: POST /api/contact
//
// Sends the contact form via Resend's HTTP API. No SDK: Resend's whole surface here is
// one JSON POST, so a dependency would buy nothing over fetch -- same reasoning as
// refresh.js's use of the built-in fetch for the GitHub API.
//
// Requires two Vercel environment variables (Project Settings -> Environment Variables,
// never committed to the repo):
//   RESEND_API_KEY    from resend.com. The free tier's sandbox sender
//                      (onboarding@resend.dev) is enough here: every message goes to
//                      CONTACT_TO_EMAIL, which is also the address the Resend account is
//                      registered under, and the sandbox sender can deliver to that
//                      address without a verified domain. The visitor never sees the
//                      "from" address -- their own address travels as reply-to instead.
//   CONTACT_TO_EMAIL   the destination inbox. Deliberately not a constant in this file:
//                      the owner's address exists nowhere in this repository, public or
//                      private, only in Vercel's environment.
//
// And one optional one:
//   CONTACT_FROM_EMAIL the sender, e.g. "TCI contact form <contact@thecomputeindices.com>".
//                      Needs the domain verified in Resend first. Unset, the sandbox sender
//                      above is used, which only delivers to the address the Resend account
//                      is registered under: moving the form to a different inbox without a
//                      verified domain means re-registering Resend under that inbox.
//   CONTACT_ACK_FROM   the sender of the automatic confirmation to the visitor, e.g.
//                      "The Compute Indices <contact@thecomputeindices.com>". Unset, no
//                      confirmation is sent. Needs a verified domain: the sandbox sender
//                      cannot deliver to a stranger's address.
//
// THE CONFIRMATION CARRIES NOTHING THE VISITOR TYPED. A form that emails any address
// entered into it, repeating the name or message back, is a free relay: a bot enters a
// victim's address and a spam link as the "name", and this domain delivers the spam with
// its own reputation. So the confirmation is fixed text, sent only after the owner's copy
// was accepted, marked Auto-Submitted (RFC 3834) so autoresponders on the other end do
// not answer it, and a failure to send it never turns a delivered message into #error.
//
// No dependencies: Vercel's Node runtime ships a global fetch and parses a plain HTML
// form's application/x-www-form-urlencoded body into req.body for free.
//
// Spam control is a honeypot: a hidden "website" field a real visitor never sees
// (site.css .hp-trap) but a bot filling in every input on the page does. A submission
// that fills it is redirected the same as a genuine one and nothing is sent, so a bot
// never learns its post was dropped.
//
// A HONEYPOT ONLY STOPS BOTS THAT FILL EVERY FIELD. A script that posts the three real
// fields straight at this endpoint passes it, and before 10 October 2026 nothing else
// stood in the way: no cap on how often, and none on how long a name or message could be.
// Each accepted post costs two Resend sends (the owner's copy and the confirmation), so a
// loop could empty the Resend quota, fill the owner's inbox, and send the confirmation to
// any number of strangers' addresses from this domain. So there is now a site-wide budget
// (MAX_PER_HOUR, MAX_PER_DAY), counted in the Upstash database the visit log already uses.
// It is deliberately not per visitor: a per-IP limit would mean storing something derived
// from the IP for every sender, which PRIVACY.md does not cover. A real correspondent
// writes a few times a week; the budget is far above that and far below abuse. With
// Upstash unset or unreachable the form still works -- a broken counter must not cost a
// genuine message -- so the cap is a guard, not a dependency.

const MAX_PER_HOUR = 10;
const MAX_PER_DAY = 40;
const MAX_NAME = 200;
const MAX_EMAIL = 254; // RFC 5321 path limit
const MAX_MESSAGE = 10000;

function redirect(res, path) {
  res.writeHead(302, { Location: path });
  res.end();
}

// True when the budget is spent. Counts this submission. Fails open (returns false).
async function overBudget() {
  const base = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!base || !token) return false;
  const now = new Date().toISOString();
  const hourKey = `tci:contact:${now.slice(0, 13)}`; // e.g. 2026-10-10T14
  const dayKey = `tci:contact:${now.slice(0, 10)}`;
  try {
    const { upstashPipeline } = await import("./_upstash.mjs");
    const out = await upstashPipeline(base, token, [
      ["INCR", hourKey],
      ["EXPIRE", hourKey, 7200],
      ["INCR", dayKey],
      ["EXPIRE", dayKey, 172800],
    ]);
    const hour = Number(out && out[0] && out[0].result);
    const day = Number(out && out[2] && out[2].result);
    return hour > MAX_PER_HOUR || day > MAX_PER_DAY;
  } catch (err) {
    return false;
  }
}

// Fixed text only -- see "THE CONFIRMATION CARRIES NOTHING THE VISITOR TYPED" above.
const ACK_SUBJECT = "Your message reached The Compute Indices";
const ACK_TEXT = [
  "Hello,",
  "",
  "Thanks for getting in touch. Your message has arrived, and I read every one myself.",
  "You can usually expect an answer within a few working days.",
  "",
  "This confirmation is sent automatically. If you want to add something, just reply to",
  "it and your reply will reach me.",
  "",
  "Mark Rusch",
  "The Compute Indices",
  "https://thecomputeindices.com",
].join("\n");

// Loose on purpose: it only has to stop obvious garbage and header-injection attempts
// (angle brackets, whitespace, commas) from becoming a recipient. Resend validates too.
const PLAUSIBLE_EMAIL = /^[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+$/;

async function sendAck(apiKey, from, to) {
  if (!from || !PLAUSIBLE_EMAIL.test(to) || to.length > 254) return;
  try {
    await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        from,
        to: [to],
        subject: ACK_SUBJECT,
        text: ACK_TEXT,
        headers: { "Auto-Submitted": "auto-replied" },
      }),
    });
  } catch (err) {
    // The owner's copy is already delivered; a lost confirmation is not an error to the visitor.
  }
}

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.status(405).json({ ok: false, reason: "method_not_allowed" });
    return;
  }

  const body = req.body || {};
  const honeypot = String(body.website || "").trim();
  const email = String(body.email || "").trim();
  // The name goes into a subject line: line breaks out, so it stays one header.
  const name = String(body.name || "").replace(/[\r\n]+/g, " ").trim();
  const message = String(body.message || "").trim();

  if (honeypot) {
    redirect(res, "/contact.html#sent");
    return;
  }

  if (
    !email || !message || !PLAUSIBLE_EMAIL.test(email) || email.length > MAX_EMAIL ||
    name.length > MAX_NAME || message.length > MAX_MESSAGE
  ) {
    redirect(res, "/contact.html#error");
    return;
  }

  if (await overBudget()) {
    redirect(res, "/contact.html#error");
    return;
  }

  const apiKey = process.env.RESEND_API_KEY;
  const toEmail = process.env.CONTACT_TO_EMAIL;
  if (!apiKey || !toEmail) {
    redirect(res, "/contact.html#error");
    return;
  }

  try {
    const sent = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: process.env.CONTACT_FROM_EMAIL || "TCI contact form <onboarding@resend.dev>",
        to: [toEmail],
        reply_to: email,
        subject: `Contact form: ${name || email}`,
        text: `From: ${name || "(no name given)"} <${email}>\n\n${message}`,
      }),
    });
    if (!sent.ok) {
      redirect(res, "/contact.html#error");
      return;
    }
  } catch (err) {
    redirect(res, "/contact.html#error");
    return;
  }

  await sendAck(apiKey, process.env.CONTACT_ACK_FROM, email);
  redirect(res, "/contact.html#sent");
};
