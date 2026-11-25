// Typed API client. One place for: base URL, cookie credentials, uniform error parsing, 401 handling, idempotency keys,
// optimistic-lock conflicts. Patient-facing calls authenticate with an `X-XY-Token` header (never cookies, never URLs).

export class ApiError extends Error {
  status: number;
  code: string;
  details: any;
  requestId: string;
  constructor(status: number, code: string, message: string, details?: any, requestId = "") {
    super(message);
    this.status = status; this.code = code; this.details = details; this.requestId = requestId;
  }
  get isConflict() { return this.status === 409; }
  get isStale() { return this.code === "stale_version"; }
  get isNetwork() { return this.status === 0; }
}

const BASE = "/api/v1";
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) { onUnauthorized = fn; }

export const newKey = () =>
  (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `k-${Date.now()}-${Math.random().toString(36).slice(2)}`);

type Opts = { method?: string; body?: any; token?: string; idempotencyKey?: string; signal?: AbortSignal; form?: FormData; noRedirect?: boolean };

async function request<T>(path: string, o: Opts = {}): Promise<T> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  if (o.form) body = o.form;
  else if (o.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(o.body); }
  if (o.token) headers["X-XY-Token"] = o.token;
  if (o.idempotencyKey) headers["Idempotency-Key"] = o.idempotencyKey;
  let res: Response;
  try {
    res = await fetch(BASE + path, { method: o.method || "GET", headers, body, credentials: "same-origin", signal: o.signal, cache: "no-store" });
  } catch (e: any) {
    if (e?.name === "AbortError") throw e;
    throw new ApiError(0, "network_error", "网络连接失败，请检查网络后重试");
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  if (!ct.includes("json")) {
    if (res.ok) return (await res.blob()) as any;
    throw new ApiError(res.status, "http_error", `服务返回异常（${res.status}）`);
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const e = data?.error || {};
    const err = new ApiError(res.status, e.code || "http_error", e.message || `请求失败（${res.status}）`, e.details, e.request_id || "");
    if (res.status === 401 && !o.token && !o.noRedirect && e.code === "unauthenticated") onUnauthorized?.();
    throw err;
  }
  return data as T;
}

export const api = {
  get: <T = any>(p: string, o: Opts = {}) => request<T>(p, o),
  post: <T = any>(p: string, body?: any, o: Opts = {}) => request<T>(p, { ...o, method: "POST", body: body ?? {} }),
  put: <T = any>(p: string, body?: any, o: Opts = {}) => request<T>(p, { ...o, method: "PUT", body }),
  patch: <T = any>(p: string, body?: any, o: Opts = {}) => request<T>(p, { ...o, method: "PATCH", body }),
  del: <T = any>(p: string, o: Opts = {}) => request<T>(p, { ...o, method: "DELETE" }),
  upload: <T = any>(p: string, form: FormData, o: Opts = {}) => request<T>(p, { ...o, method: "POST", form }),
};

export const fetcher = (path: string) => api.get(path);

// ---------------------------------------------------------------- helpers shared by UI
export function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return (e as any)?.message || "操作失败";
}

/** Conflict details → short human text (used under blocked buttons). */
export function blockersText(e: unknown): string[] {
  if (!(e instanceof ApiError)) return [];
  const d = e.details || {};
  return (d.blockers || []).map((b: any) => b.message).filter(Boolean);
}

export function fmtTime(iso?: string | null, withDate = true): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(+d)) return "—";
  const p = (n: number) => String(n).padStart(2, "0");
  const t = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return withDate ? `${p(d.getMonth() + 1)}-${p(d.getDate())} ${t}` : t;
}

export function fmtDay(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(+d) ? "—" : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function ago(iso?: string | null): string {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - +new Date(iso)) / 1000);
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}
