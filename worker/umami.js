// First-party proxy for Umami Cloud: the tracker loads from /u/s.js and sends to /u/api/send on our own domain.
// Avoids ad blockers that block cloud.umami.is, and keeps the site's security policy to 'self'.
export const UMAMI_SCRIPT = "https://cloud.umami.is/script.js";
export const UMAMI_SEND = "https://gateway.umami.is/api/send";   // Umami Cloud collection endpoint
const MAX_BODY = 16 * 1024;

// Builds the upstream request for a tracker POST, or returns an error status.
export async function forwardSend(request, sendUrl = UMAMI_SEND) {
  if (request.method !== "POST") return { status: 405 };
  const body = await request.text();
  if (!body || body.length > MAX_BODY) return { status: 413 };
  const headers = new Headers({ "content-type": "application/json" });
  headers.set("user-agent", request.headers.get("user-agent") || "Mozilla/5.0");   // Umami drops requests without one
  const ip = request.headers.get("cf-connecting-ip");
  if (ip) headers.set("x-forwarded-for", ip);                                      // for country and unique visitors
  for (const h of ["accept-language", "x-umami-cache"]) { const v = request.headers.get(h); if (v) headers.set(h, v); }
  return { request: new Request(sendUrl, { method: "POST", headers, body }) };
}
