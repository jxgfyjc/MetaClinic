// Shared fetch helper + tiny types for the MVP frontend.
// AI failure (503) is surfaced as a visible degraded badge, never a crash (instruct #30).
//
// Two execution contexts, two base URLs:
//  - Server components (SSR / RSC): Node fetch can't resolve a relative URL
//    (no page origin on the server), so we use an absolute BACKEND_URL the
//    server can reach directly (http://127.0.0.1:8001).
//  - Client components (browser): use a relative path ("") so the browser
//    same-origin request flows through next.config.js rewrites → backend.
//    The browser cannot reach 127.0.0.1:8001 on the server host.
//
// updatev2_3 fix: Node's fetch() in SSR does NOT forward the browser's
// cookies with credentials:"include" (it has no cookie jar). So in SSR we
// manually forward the incoming request's Cookie header so protected
// API calls succeed instead of always 401-ing. Dynamic-import next/headers
// (static import breaks client components that also import this module).
const _isServer = typeof window === "undefined";
export const API_BASE = _isServer
  ? (process.env.BACKEND_URL || "http://127.0.0.1:8001")
  : (process.env.NEXT_PUBLIC_API_BASE || "");

/** In SSR, pull the browser's Cookie header so we can forward it. */
async function ssrCookieHeader(): Promise<string | undefined> {
  if (!_isServer) return undefined;
  try {
    const { headers } = await import("next/headers");
    const h = await headers();
    return h.get("cookie") || undefined;
  } catch {
    return undefined; // outside of a request scope (e.g. background task)
  }
}

export async function apiGet<T = any>(path: string): Promise<T> {
  const init: RequestInit = { cache: "no-store", credentials: "include" };
  if (_isServer) {
    const cookie = await ssrCookieHeader();
    if (cookie) init.headers = { cookie };
  }
  const r = await fetch(`${API_BASE}${path}`, init);
  if (r.status === 401 && !_isServer) {
    window.location.href = "/login";
    throw new Error("未登录，正在跳转登录页");
  }
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`);
  return r.json();
}

export async function apiPost<T = any>(path: string, body: any): Promise<T> {
  const init: RequestInit = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
  };
  if (_isServer) {
    const cookie = await ssrCookieHeader();
    if (cookie) (init.headers as Record<string, string>).cookie = cookie;
  }
  const r = await fetch(`${API_BASE}${path}`, init);
  const txt = await r.text();
  let data: any = null;
  try { data = txt ? JSON.parse(txt) : null; } catch {}
  if (r.status === 401 && !_isServer) {
    window.location.href = "/login";
    throw new Error("未登录，正在跳转登录页");
  }
  if (!r.ok) throw new Error(`POST ${path} → ${r.status}: ${data?.detail || txt}`);
  return data as T;
}

export async function apiPatch<T = any>(path: string, body: any): Promise<T> {
  const init: RequestInit = {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
  };
  if (_isServer) {
    const cookie = await ssrCookieHeader();
    if (cookie) (init.headers as Record<string, string>).cookie = cookie;
  }
  const r = await fetch(`${API_BASE}${path}`, init);
  if (r.status === 401 && !_isServer) {
    window.location.href = "/login";
    throw new Error("未登录，正在跳转登录页");
  }
  if (!r.ok) throw new Error(`PATCH ${path} → ${r.status}`);
  return r.json();
}

// V1.0: multipart upload helper for ASR/OCR/tongue image
export async function apiUpload<T = any>(path: string, formData: FormData): Promise<T> {
  const init: RequestInit = { method: "POST", body: formData, credentials: "include" };
  if (_isServer) {
    const cookie = await ssrCookieHeader();
    if (cookie) init.headers = { cookie };
  }
  const r = await fetch(`${API_BASE}${path}`, init);
  const txt = await r.text();
  let data: any = null;
  try { data = txt ? JSON.parse(txt) : null; } catch {}
  if (r.status === 401 && !_isServer) {
    window.location.href = "/login";
    throw new Error("未登录，正在跳转登录页");
  }
  if (!r.ok) throw new Error(`UPLOAD ${path} → ${r.status}: ${data?.detail || txt}`);
  return data as T;
}

// updatev2_3 §2: DELETE helper — single or batch (body with {ids:[...]}).
// Same SSR cookie forwarding + 401 redirect as the other verbs.
export async function apiDelete<T = any>(path: string, body?: any): Promise<T> {
  const init: RequestInit = { method: "DELETE", credentials: "include" };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  if (_isServer) {
    const cookie = await ssrCookieHeader();
    if (cookie) {
      const h = (init.headers as Record<string, string>) || {};
      h.cookie = cookie;
      init.headers = h;
    }
  }
  const r = await fetch(`${API_BASE}${path}`, init);
  const txt = await r.text();
  let data: any = null;
  try { data = txt ? JSON.parse(txt) : null; } catch {}
  if (r.status === 401 && !_isServer) {
    window.location.href = "/login";
    throw new Error("未登录，正在跳转登录页");
  }
  if (!r.ok) throw new Error(`DELETE ${path} → ${r.status}: ${data?.detail || txt}`);
  return data as T;
}

export type Patient = {
  id: string; name: string; gender?: string; sex?: string; age: number;
  phone?: string; allergies?: { allergen: string; severity: string }[];
  insurance?: string; is_demo?: boolean;
  chronic_conditions?: string[] | { name: string }[];
  history?: any;
};

export type Encounter = {
  id: string; patient_id: string; doctor_id: string;
  chief_complaint: string; case_type: string; status: string;
  history?: any; labs?: any[]; medications?: any[]; raw_text?: string;
  allergies?: any[]; medical_record?: any;
  source?: string; tongue_image_key?: string;
};

export type AiRun = {
  run_id: string; status: string; latency_ms: number;
  panel_status: Record<string, string>;
  module_timings?: Record<string, { start_ms: number; end_ms: number; duration_ms: number }>;
  western: any; tcm: any; tongue: any;
  knowledge: any; synthesis: any; prescription: any;
  therapy: any;
  evidence: any[];
};

export type DoctorProfile = {
  doctor_id: string; dept: string; specialties: string[];
  syndrome_preference: string; formula_preference: string;
  herb_preference: string; non_drug_preference: string;
  analysis_focus: string; experience_notes: string[];
};

export type TherapyProgram = {
  id: string; name: string; category: string;
  applicable_scenarios: string; therapeutic_goal: string;
  frequency_duration: string; contraindications: string;
  hospital_code: string; source_type: string;
};

export function panelBadge(s: string) {
  if (s === "complete" || s === "ok") return "badge-ok";
  if (s === "failed" || s === "unavailable") return "badge-err";
  return "badge-warn";
}

// Poll an AI run until terminal (SUCCEEDED/PARTIAL/FAILED), calling onUpdate.
// Returns the final run. Interval 2.5s. Timeout 6 min (real model latency).
export async function pollAiRun(
  runId: string,
  onUpdate: (run: AiRun) => void,
  opts: { intervalMs?: number; timeoutMs?: number } = {}
): Promise<AiRun> {
  const interval = opts.intervalMs ?? 2500;
  const timeout = opts.timeoutMs ?? 6 * 60 * 1000;
  const start = Date.now();
  while (true) {
    const run = await apiGet<AiRun>(`/api/ai-runs/${runId}`);
    onUpdate(run);
    if (["SUCCEEDED", "PARTIAL", "FAILED"].includes(run.status)) {
      return run;
    }
    if (Date.now() - start > timeout) {
      throw new Error("AI run polling timed out");
    }
    await new Promise((r) => setTimeout(r, interval));
  }
}
