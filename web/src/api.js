/* One place that talks to the API.
 *
 * Auth rides on an httpOnly cookie the server sets at sign-in, so nothing here
 * ever touches a token. A 401 means the session is gone - the caller decides
 * whether that is a redirect or a form error.
 */

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });

  const body = res.status === 204 ? null : await res.json().catch(() => null);

  if (!res.ok) {
    // FastAPI wraps our own errors in `detail`; validation errors arrive as an array.
    const d = body?.detail;
    const code = Array.isArray(d) ? "invalid" : d?.error ?? "error";
    const message = Array.isArray(d)
      ? d[0]?.msg ?? "That input is not valid."
      : d?.detail ?? d?.error ?? `HTTP ${res.status}`;
    throw new ApiError(res.status, code, message);
  }
  return body;
}

export const get = (path) => request(path);
export const post = (path, data) =>
  request(path, { method: "POST", body: JSON.stringify(data ?? {}) });
export const del = (path) => request(path, { method: "DELETE" });

/* ------------------------------------------------------------------ helpers */

export function ago(iso) {
  if (!iso) return "never";
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export function initials(name) {
  return String(name || "?")
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0] || "")
    .join("")
    .toUpperCase();
}
