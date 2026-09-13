// Vercel serverless function: POST /api/contact
//
// Sends the contact form to CONTACT_EMAIL via Resend's HTTP API. No SDK: Resend's
// whole surface here is one JSON POST, so a dependency would buy nothing over fetch --
// same reasoning as refresh.js's use of the built-in fetch for the GitHub API.
//
// Requires one Vercel environment variable (Project Settings -> Environment Variables,
// never committed to the repo):
//   RESEND_API_KEY   from resend.com. The free tier's sandbox sender
//                     (onboarding@resend.dev) is enough here: every message goes to
//                     CONTACT_EMAIL, which is also the address the Resend account is
//                     registered under, and the sandbox sender can deliver to that
//                     address without a verified domain. The visitor never sees the
//                     "from" address -- their own address travels as reply-to instead.
//
// No dependencies: Vercel's Node runtime ships a global fetch and parses a plain HTML
// form's application/x-www-form-urlencoded body into req.body for free.
//
// Spam control is a honeypot only: a hidden "website" field a real visitor never sees
// (site.css .hp-trap) but a bot filling in every input on the page does. A submission
// that fills it is redirected the same as a genuine one and nothing is sent, so a bot
// never learns its post was dropped.

const CONTACT_EMAIL = "rusch.mh@gmail.com";

function redirect(res, path) {
  res.writeHead(302, { Location: path });
  res.end();
}

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.status(405).json({ ok: false, reason: "method_not_allowed" });
    return;
  }

  const body = req.body || {};
  const honeypot = String(body.website || "").trim();
  const email = String(body.email || "").trim();
  const name = String(body.name || "").trim();
  const message = String(body.message || "").trim();

  if (honeypot) {
    redirect(res, "/contact.html#sent");
    return;
  }

  if (!email || !message) {
    redirect(res, "/contact.html#error");
    return;
  }

  const apiKey = process.env.RESEND_API_KEY;
  if (!apiKey) {
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
        from: "TCI contact form <onboarding@resend.dev>",
        to: [CONTACT_EMAIL],
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

  redirect(res, "/contact.html#sent");
};
