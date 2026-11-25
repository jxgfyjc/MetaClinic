"use client";
// UI primitives: toast, confirm dialog, status chips, empty/error/loading states, modal, tabs, badges.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, CircleAlert, Info, Loader2, X, XCircle, RefreshCw, Inbox } from "lucide-react";

// ------------------------------------------------------------------ toast
type Toast = { id: number; kind: "ok" | "err" | "info" | "warn"; text: string };
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((s) => [...s.slice(-3), { id, kind, text }]);
    setTimeout(() => setItems((s) => s.filter((t) => t.id !== id)), kind === "err" ? 7000 : 3200);
  }, []);
  const Icon = { ok: CheckCircle2, err: XCircle, info: Info, warn: AlertTriangle };
  const tone = { ok: "text-jade-600", err: "text-danger-600", info: "text-brand-600", warn: "text-amber-600" };
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-[100] flex w-[min(92vw,380px)] flex-col gap-2 no-print" aria-live="polite">
        {items.map((t) => {
          const I = Icon[t.kind];
          return (
            <div key={t.id} role="status" className="card flex animate-slide items-start gap-2.5 px-3.5 py-3 shadow-pop">
              <I size={18} className={`mt-0.5 shrink-0 ${tone[t.kind]}`} />
              <div className="text-sm leading-snug text-ink-800">{t.text}</div>
              <button className="ml-auto text-ink-300 hover:text-ink-600" onClick={() => setItems((s) => s.filter((x) => x.id !== t.id))} aria-label="关闭"><X size={14} /></button>
            </div>
          );
        })}
      </div>
    </ToastCtx.Provider>
  );
}

// ------------------------------------------------------------------ modal / confirm
export function Modal({ open, onClose, title, children, footer, width = "max-w-lg", dismissable = true }:
  { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; width?: string; dismissable?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape" && dismissable) onClose(); };
    document.addEventListener("keydown", k);
    const prev = document.body.style.overflow; document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", k); document.body.style.overflow = prev; };
  }, [open, onClose, dismissable]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[90] flex items-end justify-center sm:items-center no-print" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-ink-950/40 backdrop-blur-[2px]" onClick={dismissable ? onClose : undefined} />
      <div className={`relative w-full ${width} max-h-[90vh] animate-pop overflow-hidden rounded-t-2xl bg-white shadow-pop sm:rounded-2xl`}>
        <div className="flex items-center justify-between border-b border-ink-100 px-5 py-3.5">
          <h3 className="text-[15px] font-semibold text-ink-900">{title}</h3>
          {dismissable && <button onClick={onClose} className="rounded-md p-1 text-ink-400 hover:bg-ink-100" aria-label="关闭"><X size={16} /></button>}
        </div>
        <div className="max-h-[68vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex items-center justify-end gap-2 border-t border-ink-100 bg-ink-50/60 px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

type ConfirmOpts = { title: string; body?: ReactNode; confirmText?: string; danger?: boolean; reason?: { label: string; min?: number; placeholder?: string } };
const ConfirmCtx = createContext<(o: ConfirmOpts) => Promise<{ ok: boolean; reason: string }>>(async () => ({ ok: false, reason: "" }));
export const useConfirm = () => useContext(ConfirmCtx);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<(ConfirmOpts & { resolve: (v: { ok: boolean; reason: string }) => void }) | null>(null);
  const [reason, setReason] = useState("");
  const ask = useCallback((o: ConfirmOpts) => new Promise<{ ok: boolean; reason: string }>((resolve) => { setReason(""); setState({ ...o, resolve }); }), []);
  const close = (ok: boolean) => { state?.resolve({ ok, reason: reason.trim() }); setState(null); };
  const need = state?.reason?.min ?? (state?.reason ? 2 : 0);
  return (
    <ConfirmCtx.Provider value={ask}>
      {children}
      <Modal open={!!state} onClose={() => close(false)} title={state?.title}
        footer={<><button className="btn-secondary" onClick={() => close(false)}>取消</button>
          <button className={state?.danger ? "btn-danger" : "btn-primary"} disabled={!!state?.reason && reason.trim().length < need} onClick={() => close(true)}>{state?.confirmText || "确认"}</button></>}>
        {state?.body && <div className="mb-3 text-sm leading-relaxed text-ink-700">{state.body}</div>}
        {state?.reason && (
          <div><label className="label">{state.reason.label}{need ? `（至少 ${need} 字）` : ""}</label>
            <textarea className="input min-h-[84px]" value={reason} autoFocus onChange={(e) => setReason(e.target.value)} placeholder={state.reason.placeholder} /></div>)}
      </Modal>
    </ConfirmCtx.Provider>
  );
}

// ------------------------------------------------------------------ states
export function Spinner({ size = 16, className = "" }: { size?: number; className?: string }) { return <Loader2 size={size} className={`animate-spin ${className}`} />; }

export function Skeleton({ className = "h-4 w-full" }: { className?: string }) { return <div className={`skeleton ${className}`} />; }
export function SkeletonList({ rows = 4 }: { rows?: number }) {
  return <div className="space-y-3 p-1">{Array.from({ length: rows }).map((_, i) => <div key={i} className="card card-pad space-y-2.5"><Skeleton className="h-4 w-1/3" /><Skeleton className="h-3 w-2/3" /></div>)}</div>;
}

export function Empty({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-2xl bg-ink-100 text-ink-400">{icon ?? <Inbox size={22} />}</div>
      <div className="text-sm font-medium text-ink-700">{title}</div>
      {hint && <div className="max-w-sm text-xs text-ink-400">{hint}</div>}
      {action}
    </div>
  );
}

export function ErrorBox({ error, onRetry, title = "加载失败" }: { error: any; onRetry?: () => void; title?: string }) {
  const msg = error?.message || "未知错误";
  return (
    <div className="card flex items-start gap-3 border-danger-100 bg-danger-50/60 p-4">
      <CircleAlert size={18} className="mt-0.5 shrink-0 text-danger-600" />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-danger-700">{title}</div>
        <div className="mt-0.5 break-words text-xs text-danger-600/90">{msg}{error?.requestId ? <span className="ml-1 font-mono opacity-60">[{error.requestId}]</span> : null}</div>
      </div>
      {onRetry && <button className="btn-secondary btn-sm shrink-0" onClick={onRetry}><RefreshCw size={12} />重试</button>}
    </div>
  );
}

export function Banner({ kind = "info", title, children, action }: { kind?: "info" | "warn" | "err" | "ok"; title?: ReactNode; children?: ReactNode; action?: ReactNode }) {
  const tone = { info: "border-brand-100 bg-brand-50/70 text-brand-800", warn: "border-amber-100 bg-amber-50/80 text-amber-700", err: "border-danger-100 bg-danger-50/80 text-danger-700", ok: "border-jade-100 bg-jade-50/80 text-jade-700" }[kind];
  const I = { info: Info, warn: AlertTriangle, err: CircleAlert, ok: CheckCircle2 }[kind];
  return (
    <div className={`flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 ${tone}`}>
      <I size={16} className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1 text-[13px] leading-snug">{title && <div className="font-semibold">{title}</div>}{children && <div className={title ? "mt-0.5 opacity-90" : ""}>{children}</div>}</div>
      {action}
    </div>
  );
}

// ------------------------------------------------------------------ chips
const STATUS: Record<string, [string, string]> = {
  WAITING: ["待接诊", "tag-warn"], IN_PROGRESS: ["接诊中", "tag-brand"], PENDING_CONFIRM: ["待确认", "tag-warn"], COMPLETED: ["已完成", "tag-ok"], CANCELLED: ["已取消", "tag-neutral"],
  queued: ["排队中", "tag-neutral"], running: ["进行中", "tag-brand"], succeeded: ["已完成", "tag-ok"], failed: ["失败", "tag-err"], partial: ["部分完成", "tag-warn"], cancelled: ["已取消", "tag-neutral"],
  pending: ["待处理", "tag-warn"], processing: ["处理中", "tag-brand"], not_required: ["无需处理", "tag-neutral"], skipped: ["已跳过", "tag-neutral"], unavailable: ["不可用", "tag-err"],
  accepted: ["已接受", "tag-ok"], modified: ["已修改", "tag-ok"], rejected: ["已驳回", "tag-err"], withdrawn: ["已撤销", "tag-neutral"], superseded: ["已被取代", "tag-neutral"],
  generating: ["生成中", "tag-brand"], draft: ["草稿", "tag-warn"], confirmed: ["已确认", "tag-ok"],
  signed: ["已签署", "tag-brand"], pharmacy_review: ["药房审核中", "tag-brand"], approved: ["审核通过", "tag-ok"], dispensed: ["已发药", "tag-ok"], returned: ["药房退回", "tag-err"], voided: ["已作废", "tag-neutral"],
  finalized: ["已定稿", "tag-ok"], booked: ["已预约", "tag-brand"], checked_in: ["已到诊", "tag-ok"], no_show: ["爽约", "tag-err"],
  scheduled: ["待随访", "tag-neutral"], answered: ["患者已答", "tag-brand"], closed: ["已关闭", "tag-neutral"], proposed: ["待启用", "tag-warn"], active: ["进行中", "tag-brand"],
  open: ["待处理", "tag-err"], acknowledged: ["已确认", "tag-warn"], resolved: ["已处理", "tag-ok"],
  normal: ["正常", "tag-ok"], attention: ["需关注", "tag-warn"], high_risk: ["高危", "tag-err"],
  low: ["低", "tag-neutral"], medium: ["中", "tag-warn"], high: ["高", "tag-ok"],
};
export function StatusChip({ s, label }: { s: string; label?: string }) {
  const [txt, cls] = STATUS[s] || [s, "tag-neutral"];
  return <span className={cls}>{(s === "running" || s === "processing" || s === "generating") && <span className="h-1.5 w-1.5 animate-pulseDot rounded-full bg-current" />}{label || txt}</span>;
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { key: T; label: ReactNode; count?: number }[] }) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-ink-100 no-print" role="tablist">
      {items.map((it) => (
        <button key={it.key} role="tab" aria-selected={value === it.key} onClick={() => onChange(it.key)}
          className={`relative shrink-0 px-3.5 py-2.5 text-sm font-medium transition ${value === it.key ? "text-brand-700" : "text-ink-500 hover:text-ink-800"}`}>
          {it.label}{it.count ? <span className="ml-1.5 rounded-full bg-ink-100 px-1.5 text-[10px] text-ink-600">{it.count}</span> : null}
          {value === it.key && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded bg-brand-600" />}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, children, hint, error, required }: { label: string; children: ReactNode; hint?: string; error?: string; required?: boolean }) {
  return (
    <div>
      <label className="label">{label}{required && <span className="ml-0.5 text-danger-500">*</span>}</label>
      {children}
      {error ? <div className="mt-1 text-xs text-danger-600">{error}</div> : hint ? <div className="hint mt-1">{hint}</div> : null}
    </div>
  );
}

export function useDebounced<T>(v: T, ms = 300): T {
  const [x, setX] = useState(v);
  useEffect(() => { const t = setTimeout(() => setX(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return x;
}

/** Warn before leaving a page with unsaved work. */
export function useUnsavedGuard(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);
}
