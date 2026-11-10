// Validation for feedback submissions. Pure function so it can be unit-tested.
const KINDS = new Set(["idea", "wrong", "airline", "other"]);
const RATINGS = new Set(["up", "down"]);

export function cleanFeedback(raw) {
  if (!raw || typeof raw !== "object") return { error: "Could not read that." };
  if (raw.website) return { spam: true };                       // honeypot
  const str = (v, max) => (typeof v === "string" ? v.trim().slice(0, max) : "");
  const value = {
    page: str(raw.page, 200) || null,
    config: str(raw.config, 40) || null,
    rating: RATINGS.has(raw.rating) ? raw.rating : null,
    kind: KINDS.has(raw.kind) ? raw.kind : "other",
    message: str(raw.message, 1500) || null,
    email: str(raw.email, 200) || null,
  };
  if (value.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.email)) return { error: "That email address doesn't look right." };
  if (!value.rating && !value.message) return { error: "Choose Yes or Not really, or write a message." };
  if (value.page && !value.page.startsWith("/")) value.page = null;
  return { value };
}
