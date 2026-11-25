"use client";
// Structured line-item editors for western / patent-medicine / herbal prescriptions (also used for the plan's drafted medication).
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { AlertTriangle, Plus, Trash2 } from "lucide-react";
import { useDebounced } from "@/components/ui";

export type Issue = { key: string; rule_id: string; severity: "block" | "critical" | "warning" | "info"; message: string; item_index: number | null; item_name: string };
export const SEV: Record<string, { label: string; cls: string; row: string }> = {
  block: { label: "必须修改", cls: "tag-err", row: "border-danger-300 bg-danger-50/40" },
  critical: { label: "严重", cls: "tag-err", row: "border-danger-200 bg-danger-50/30" },
  warning: { label: "需确认", cls: "tag-warn", row: "border-amber-200 bg-amber-50/40" },
  info: { label: "提示", cls: "tag-neutral", row: "border-ink-100" },
};
const sevRank = (s: string) => ({ block: 0, critical: 1, warning: 2, info: 3 } as any)[s] ?? 9;
export const worst = (is: Issue[]) => is.slice().sort((a, b) => sevRank(a.severity) - sevRank(b.severity))[0]?.severity;

const FREQ = ["qd", "bid", "tid", "qid", "qn", "q12h", "q8h", "q6h", "prn", "必要时", "每日一次", "每日两次", "每日三次", "睡前"];
const ROUTE = ["口服", "舌下含服", "静脉滴注", "肌内注射", "皮下注射", "外用", "吸入", "滴眼", "滴鼻"];
const SPECIAL = ["", "先煎", "后下", "包煎", "烊化", "另煎", "冲服", "打碎", "捣碎", "久煎"];
const ROLE = ["", "君", "臣", "佐", "使"];

/** name input with catalog autocomplete (never forces a catalog pick — off-catalog names are allowed but get flagged by the safety rules) */
function DrugInput({ value, kind, disabled, onText, onPick, placeholder, ariaLabel }: { value: string; kind: "western" | "patent" | "herb"; disabled?: boolean; onText: (v: string) => void; onPick: (d: any) => void; placeholder?: string; ariaLabel?: string }) {
  const [open, setOpen] = useState(false); const [hi, setHi] = useState(0); const [typed, setTyped] = useState(false);
  const q = useDebounced(value.trim(), 200);
  const { data } = useSWR(open && typed && q ? `/catalog/drugs?kind=${kind}&limit=8&q=${encodeURIComponent(q)}` : null);
  const items: any[] = data?.items || [];
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { const h = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); }; document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h); }, []);
  return (
    <div className="relative" ref={box}>
      <input className="input py-1.5" disabled={disabled} value={value} placeholder={placeholder} aria-label={ariaLabel || "药名"} role="combobox" aria-expanded={open && items.length > 0} autoComplete="off"
        onChange={(e) => { onText(e.target.value); setTyped(true); setOpen(true); setHi(0); }} onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (!open || !items.length) return;
          if (e.key === "ArrowDown") { e.preventDefault(); setHi((h) => Math.min(items.length - 1, h + 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(0, h - 1)); }
          else if (e.key === "Enter") { e.preventDefault(); onPick(items[hi]); setOpen(false); setTyped(false); }
          else if (e.key === "Escape") setOpen(false);
        }} />
      {open && typed && items.length > 0 && (
        <ul className="absolute left-0 right-0 top-full z-30 mt-1 max-h-60 overflow-auto rounded-lg border border-ink-200 bg-white py-1 shadow-pop" role="listbox">
          {items.map((d, i) => (
            <li key={d.id} role="option" aria-selected={i === hi}><button type="button" className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] ${i === hi ? "bg-brand-50" : "hover:bg-ink-50"}`} onMouseEnter={() => setHi(i)} onMouseDown={(e) => { e.preventDefault(); onPick(d); setOpen(false); setTyped(false); }}>
              <span className="font-medium text-ink-900">{d.name}</span>{d.spec && <span className="text-xs text-ink-400">{d.spec}</span>}{d.dose_max_g ? <span className="text-xs text-ink-400">常规 {d.dose_min_g ?? ""}{d.dose_min_g ? "–" : "≤"}{d.dose_max_g}g</span> : null}
              {d.source === "sample" && <span className="tag-sim ml-auto">示例目录</span>}</button></li>))}
        </ul>)}
    </div>
  );
}

function IssueLines({ list }: { list?: Issue[] }) {
  if (!list?.length) return null;
  return <ul className="mt-1 space-y-0.5">{list.map((i) => <li key={i.key} className="flex items-start gap-1.5 text-xs"><span className={`${SEV[i.severity].cls} shrink-0`}>{SEV[i.severity].label}</span><span className="text-ink-700">{i.message}</span></li>)}</ul>;
}

const emptyWestern = () => ({ name: "", drug_id: null as string | null, spec: "", form: "", dose_text: "", frequency: "", route: "口服", days: null as number | null, instructions: "", in_catalog: false, flags: [] as string[] });
const emptyHerb = () => ({ name: "", drug_id: null as string | null, dose_g: null as number | null, special: "", role: "", in_catalog: false, flags: [] as string[] });

export function WesternItemsEditor({ items, onChange, readOnly, kind = "western", issues = [] }: { items: any[]; onChange: (v: any[]) => void; readOnly?: boolean; kind?: "western" | "patent"; issues?: Issue[] }) {
  const set = (i: number, p: any) => onChange(items.map((r, j) => (j === i ? { ...r, ...p } : r)));
  return (
    <div className="space-y-2">
      {items.map((r, i) => {
        const iss = issues.filter((x) => x.item_index === i); const w = worst(iss);
        return (
          <div key={i} className={`rounded-xl border p-2.5 ${w ? SEV[w].row : "border-ink-100 bg-white"}`}>
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-[minmax(10rem,1.6fr)_1fr_1fr_1fr_5rem_auto]">
              <div className="col-span-2 lg:col-span-1"><DrugInput kind={kind} value={r.name} disabled={readOnly} placeholder="药名（通用名）" onText={(v) => set(i, { name: v, drug_id: null, in_catalog: false })}
                onPick={(d) => set(i, { name: d.name, drug_id: d.id, spec: d.spec || r.spec, form: d.form || r.form, in_catalog: true, dose_text: r.dose_text || d.default_dose || "", frequency: r.frequency || d.default_frequency || "", route: r.route || d.default_route || "口服" })} /></div>
              <input className="input py-1.5" disabled={readOnly} placeholder="单次剂量 如 0.5g" aria-label="单次剂量" value={r.dose_text || ""} onChange={(e) => set(i, { dose_text: e.target.value })} />
              <input className="input py-1.5" list="freq-list" disabled={readOnly} placeholder="频次 如 bid" aria-label="频次" value={r.frequency || ""} onChange={(e) => set(i, { frequency: e.target.value })} />
              <input className="input py-1.5" list="route-list" disabled={readOnly} placeholder="途径" aria-label="给药途径" value={r.route || ""} onChange={(e) => set(i, { route: e.target.value })} />
              <input className="input py-1.5" type="number" min={1} disabled={readOnly} placeholder="天数" aria-label="疗程天数" value={r.days ?? ""} onChange={(e) => set(i, { days: e.target.value === "" ? null : Number(e.target.value) })} />
              {!readOnly && <button type="button" className="btn-ghost btn-sm text-ink-400 hover:text-danger-600" aria-label={`删除 ${r.name || "此行"}`} onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 size={14} /></button>}
            </div>
            {(r.spec || r.reason || (r.flags || []).length > 0 || (r.considerations || []).length > 0 || r.in_catalog === false && r.name) && (
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-ink-500">
                {r.spec && <span className="text-ink-400">规格 {r.spec}</span>}
                {r.in_catalog ? <span className="tag-ok">目录内</span> : r.name ? <span className="tag-warn">目录外</span> : null}
                {(r.flags || []).filter((f: string) => f.startsWith("missing_")).map((f: string) => <span key={f} className="tag-warn">缺{({ missing_dose_text: "剂量", missing_frequency: "频次", missing_days: "疗程" } as any)[f] || f}（AI 未给出，需医师填写）</span>)}
                {r.reason && <span>用于：{r.reason}</span>}
                {(r.considerations || []).length > 0 && <span className="text-amber-700">注意：{r.considerations.join("；")}</span>}
              </div>)}
            <IssueLines list={iss} />
          </div>);
      })}
      {items.length === 0 && <div className="rounded-lg border border-dashed border-ink-200 px-3 py-2.5 text-xs text-ink-400">暂无药品</div>}
      {!readOnly && <button type="button" className="btn-secondary btn-sm" onClick={() => onChange([...items, emptyWestern()])}><Plus size={13} />添加药品</button>}
      <datalist id="freq-list">{FREQ.map((f) => <option key={f} value={f} />)}</datalist><datalist id="route-list">{ROUTE.map((f) => <option key={f} value={f} />)}</datalist>
    </div>
  );
}

export function HerbItemsEditor({ items, onChange, readOnly, issues = [] }: { items: any[]; onChange: (v: any[]) => void; readOnly?: boolean; issues?: Issue[] }) {
  const set = (i: number, p: any) => onChange(items.map((r, j) => (j === i ? { ...r, ...p } : r)));
  const total = items.reduce((a, r) => a + (Number(r.dose_g) || 0), 0);
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {items.map((r, i) => {
          const iss = issues.filter((x) => x.item_index === i); const w = worst(iss);
          return (
            <div key={i} className={`rounded-xl border p-2 ${w ? SEV[w].row : "border-ink-100 bg-white"}`}>
              <div className="flex items-center gap-1.5">
                <div className="min-w-0 flex-1"><DrugInput kind="herb" value={r.name} disabled={readOnly} placeholder="药材名" ariaLabel="药材名" onText={(v) => set(i, { name: v, drug_id: null, in_catalog: false })}
                  onPick={(d) => set(i, { name: d.name, drug_id: d.id, in_catalog: true, special: r.special || d.special_method || "" })} /></div>
                <div className="relative w-[4.6rem] shrink-0"><input className="input py-1.5 pr-5 text-right" type="number" min={0} step="0.5" disabled={readOnly} aria-label={`${r.name || "药材"}克数`} placeholder="克" value={r.dose_g ?? ""} onChange={(e) => set(i, { dose_g: e.target.value === "" ? null : Number(e.target.value) })} /><span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[11px] text-ink-400">g</span></div>
                {!readOnly && <button type="button" className="text-ink-300 hover:text-danger-600" aria-label={`删除 ${r.name || "此味"}`} onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 size={13} /></button>}
              </div>
              <div className="mt-1.5 flex items-center gap-1.5">
                <select className="input w-auto py-1 text-xs" disabled={readOnly} aria-label="特殊煎法" value={r.special || ""} onChange={(e) => set(i, { special: e.target.value })}>{SPECIAL.map((s) => <option key={s} value={s}>{s || "常规煎"}</option>)}</select>
                <select className="input w-auto py-1 text-xs" disabled={readOnly} aria-label="君臣佐使" value={r.role || ""} onChange={(e) => set(i, { role: e.target.value })}>{ROLE.map((s) => <option key={s} value={s}>{s ? `${s}药` : "角色"}</option>)}</select>
                {!r.in_catalog && r.name && <span className="tag-warn ml-auto" title="不在药品目录中，药房可能无法调配">目录外</span>}
              </div>
              <IssueLines list={iss} />
            </div>);
        })}
      </div>
      {items.length === 0 && <div className="rounded-lg border border-dashed border-ink-200 px-3 py-2.5 text-xs text-ink-400">暂无药味</div>}
      <div className="flex flex-wrap items-center gap-3">
        {!readOnly && <button type="button" className="btn-secondary btn-sm" onClick={() => onChange([...items, emptyHerb()])}><Plus size={13} />添加药味</button>}
        <span className="text-xs text-ink-400">共 {items.filter((r) => r.name?.trim()).length} 味 · 每剂合计 {total % 1 ? total.toFixed(1) : total} g</span>
      </div>
    </div>
  );
}

export function IssueBadge({ summary }: { summary?: Record<string, number> }) {
  if (!summary) return null;
  const b = summary.block || 0, c = summary.critical || 0, w = summary.warning || 0;
  if (!b && !c && !w) return <span className="tag-ok">无风险提示</span>;
  return <span className="inline-flex items-center gap-1">{b > 0 && <span className="tag-err"><AlertTriangle size={11} />{b} 必须修改</span>}{c > 0 && <span className="tag-err">{c} 严重</span>}{w > 0 && <span className="tag-warn">{w} 需确认</span>}</span>;
}
