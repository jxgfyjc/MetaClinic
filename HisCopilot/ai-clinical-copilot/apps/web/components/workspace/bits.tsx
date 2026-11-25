"use client";
// Small presentational pieces shared by the clinical workspace panels.
import { useEffect, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Spinner } from "@/components/ui";

export const SRC_LABEL: Record<string, string> = { patient: "患者自述", doctor: "医师录入", voice: "语音转写", ocr: "OCR 识别", tongue: "舌象图像", system: "系统" };

export function Card({ title, icon, right, children, tone, className = "" }: { title?: ReactNode; icon?: ReactNode; right?: ReactNode; children: ReactNode; tone?: "warn" | "err" | "ok"; className?: string }) {
  const b = tone === "warn" ? "border-amber-100" : tone === "err" ? "border-danger-100" : tone === "ok" ? "border-jade-100" : "";
  return (
    <section className={`card ${b} ${className}`}>
      {(title || right) && <div className="flex items-center gap-2 border-b border-ink-100 px-4 py-3">{icon}<h2 className="h-sec">{title}</h2><div className="ml-auto flex items-center gap-2">{right}</div></div>}
      <div className="card-pad">{children}</div>
    </section>
  );
}

export function Collapse({ title, children, defaultOpen = false, right }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean; right?: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-xl border border-ink-100">
      <button type="button" className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-sm font-medium text-ink-700 hover:bg-ink-50" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}{title}<span className="ml-auto">{right}</span>
      </button>
      {open && <div className="border-t border-ink-100 p-3.5">{children}</div>}
    </div>
  );
}

/** AI's own confidence — deliberately neutral colours so it never reads as an endorsement. */
export function ConfChip({ c }: { c?: string }) {
  const t = c === "high" ? "较高" : c === "medium" ? "中等" : "较低";
  return <span className="tag-neutral" title="模型自评，不代表医学上的确定程度">AI 自评置信度：{t}</span>;
}

export type FactMap = Record<string, { id: string; label: string; text: string; source: string; reliability: string }>;
export const factMap = (facts?: any[]): FactMap => Object.fromEntries((facts || []).map((f) => [f.id, f]));

/** A statement + the numbered source facts it cites. Click the chips to see the ORIGINAL data (with provenance). */
export function Claim({ text, refs = [], facts, tone }: { text: string; refs?: string[]; facts: FactMap; tone?: "against" }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="text-[13px] leading-relaxed text-ink-800">
      <span className={tone === "against" ? "text-ink-700" : ""}>{text}</span>
      {refs.length > 0 ? (
        <button type="button" className="ml-1.5 inline-flex flex-wrap gap-1 align-middle" onClick={() => setOpen(!open)} title="查看引用的原始资料">{refs.map((r) => <span key={r} className="kbd cursor-pointer hover:border-brand-300 hover:text-brand-700">{r}</span>)}</button>
      ) : <span className="ml-1.5 align-middle tag-warn" title="模型未给出可核实的引用">无可核实引用</span>}
      {open && (
        <ul className="mt-1 space-y-1 rounded-lg bg-ink-50 p-2 text-xs">
          {refs.map((r) => { const f = facts[r]; return f ? (
            <li key={r}><span className="kbd mr-1">{r}</span><b className="text-ink-700">{f.label}</b>：{f.text}<span className="ml-1 text-ink-400">［{SRC_LABEL[f.source] || f.source}］</span>{f.reliability && <span className="ml-1 text-amber-600">{f.reliability}</span>}</li>
          ) : <li key={r} className="text-danger-600">{r}：引用的资料不存在</li>; })}
        </ul>)}
    </div>
  );
}

export function Bullets({ items, tone }: { items?: string[]; tone?: "warn" | "err" }) {
  if (!items?.length) return null;
  const dot = tone === "err" ? "bg-danger-500" : tone === "warn" ? "bg-amber-500" : "bg-ink-300";
  return <ul className="space-y-1">{items.map((t, i) => <li key={i} className="flex gap-2 text-[13px] leading-relaxed text-ink-700"><span className={`mt-[9px] h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />{t}</li>)}</ul>;
}

/** textarea that edits string[] as one item per line */
export function LinesEditor({ value, onChange, placeholder, rows = 3, disabled }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string; rows?: number; disabled?: boolean }) {
  const [txt, setTxt] = useState(value.join("\n"));
  const key = JSON.stringify(value);
  useEffect(() => { if (JSON.stringify(txt.split("\n").map((s) => s.trim()).filter(Boolean)) !== key) setTxt(value.join("\n")); /* eslint-disable-next-line */ }, [key]);
  return <textarea className="input" rows={rows} disabled={disabled} value={txt} placeholder={placeholder} onChange={(e) => { setTxt(e.target.value); onChange(e.target.value.split("\n").map((s) => s.trim()).filter(Boolean)); }} />;
}

export function Seg<T extends string | boolean | null>({ value, onChange, options, disabled }: { value: T; onChange: (v: T) => void; options: { v: T; label: string; tone?: string }[]; disabled?: boolean }) {
  return (
    <div className="inline-flex overflow-hidden rounded-lg border border-ink-200 bg-white text-xs" role="radiogroup">
      {options.map((o) => (
        <button key={String(o.v)} type="button" role="radio" aria-checked={value === o.v} disabled={disabled} onClick={() => onChange(o.v)}
          className={`px-2.5 py-1.5 font-medium transition disabled:opacity-50 ${value === o.v ? (o.tone || "bg-brand-600 text-white") : "text-ink-500 hover:bg-ink-50"}`}>{o.label}</button>
      ))}
    </div>
  );
}

export function Elapsed({ since }: { since?: string | null }) {
  const [n, setN] = useState(0);
  useEffect(() => { const t = setInterval(() => setN((x) => x + 1), 1000); return () => clearInterval(t); }, []);
  if (!since) return null;
  const s = Math.max(0, Math.floor((Date.now() - +new Date(since.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(since) ? since : since + "Z")) / 1000));
  void n;
  return <span className="tabular-nums">{Math.floor(s / 60)}:{String(s % 60).padStart(2, "0")}</span>;
}

export function Working({ label, sub }: { label: ReactNode; sub?: ReactNode }) {
  return <div className="flex items-center gap-3 rounded-xl border border-brand-100 bg-brand-50/60 px-4 py-3 text-sm text-brand-800"><Spinner className="text-brand-600" /><div><div className="font-medium">{label}</div>{sub && <div className="mt-0.5 text-xs text-brand-700/80">{sub}</div>}</div></div>;
}

export const sameJson = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
