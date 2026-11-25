"use client";
// 经验画像模块共用的小组件：免责声明、风险标记、置信度、证据查看（带原文高亮）。
import { useState, type ReactNode } from "react";
import useSWR from "swr";
import { AlertTriangle, Info, Quote, ShieldAlert } from "lucide-react";
import { Banner, ErrorBox, Modal, Skeleton } from "@/components/ui";
import { CATEGORY_LABEL, CONF_LABEL, hasCritical, splitByQuotes, type Flag } from "@/lib/style";

export function Disclaimer({ text, compact }: { text?: string; compact?: boolean }) {
  const t = text || "本经验由历史病例归纳而来，仅供参考，不是临床规则；不代表该医师本人审核或认可当前 AI 生成的方案，也不构成对当前患者的诊疗意见。最终决策由接诊医师负责。";
  if (compact) return <p className="flex gap-1.5 text-[11.5px] leading-snug text-ink-500"><Info size={13} className="mt-0.5 shrink-0" />{t}</p>;
  return <Banner kind="info" title="使用说明">{t}</Banner>;
}

const isCrit = (f: { severity: string }) => f.severity === "critical" || f.severity === "error";

export function FlagList({ flags, max }: { flags?: Flag[]; max?: number }) {
  const list = flags || [];
  if (!list.length) return null;
  const shown = max ? list.slice(0, max) : list;
  return (
    <ul className="space-y-1">
      {shown.map((f, i) => (
        <li key={i} className={`flex gap-1.5 rounded-lg px-2 py-1 text-xs leading-snug ${isCrit(f) ? "bg-danger-50 text-danger-700" : f.severity === "warn" ? "bg-amber-50 text-amber-700" : "bg-ink-50 text-ink-600"}`}>
          {isCrit(f) ? <ShieldAlert size={13} className="mt-0.5 shrink-0" /> : f.severity === "warn" ? <AlertTriangle size={13} className="mt-0.5 shrink-0" /> : <Info size={13} className="mt-0.5 shrink-0" />}
          <span>{f.message}</span>
        </li>))}
      {max && list.length > max && <li className="px-2 text-[11px] text-ink-400">另有 {list.length - max} 条提示</li>}
    </ul>
  );
}

/** 置信度：说明它是由证据数算出的，不是模型自评——避免被读成“权威背书”。 */
export function Support({ n, of, conf }: { n: number; of: number; conf: string }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className="tag-neutral" title="支持该条经验的病例数 / 该病证组病例总数">{of > 0 ? `${n}/${of} 例` : "医师自述"}</span>
      <span className={conf === "high" ? "tag-ok" : conf === "medium" ? "tag-brand" : "tag-neutral"} title="由证据数量与一致率计算，不是模型自评">证据可信度：{CONF_LABEL[conf] || conf}</span>
    </span>
  );
}

export function CatTag({ c }: { c: string }) { return <span className="tag-brand">{CATEGORY_LABEL[c] || c}</span>; }

export function Stat({ label, v, tone }: { label: string; v: ReactNode; tone?: "warn" | "err" | "ok" }) {
  const c = tone === "err" ? "text-danger-600" : tone === "warn" ? "text-amber-600" : tone === "ok" ? "text-jade-700" : "text-ink-950";
  return <div className="rounded-xl border border-ink-100 p-3"><div className={`text-xl font-semibold ${c}`}>{v}</div><div className="text-xs text-ink-500">{label}</div></div>;
}

/** 查看某条经验的证据：每条都是病例原文里的逐字片段；有权限时可看到（已脱敏的）整份病例并高亮。 */
export function EvidenceModal({ entryId, onClose }: { entryId: string | null; onClose: () => void }) {
  const { data, error, isLoading } = useSWR(entryId ? `/style/entries/${entryId}` : null);
  const e = data?.entry;
  return (
    <Modal open={!!entryId} onClose={onClose} width="max-w-3xl" title={<span className="flex items-center gap-2"><Quote size={16} className="text-brand-600" />证据：{e?.title || "…"}</span>}
      footer={<button className="btn-secondary" onClick={onClose}>关闭</button>}>
      {error ? <ErrorBox error={error} /> : isLoading || !e ? <Skeleton className="h-40 w-full" /> : (
        <div className="space-y-3">
          <p className="text-xs text-ink-500">下列每条都是病例原文中<b>逐字出现</b>的片段，用来说明这条经验从哪里归纳而来。{e.evidence_total && e.evidence_total > e.evidence.length ? `（仅显示前 ${e.evidence.length} 条）` : ""}</p>
          {e.evidence.length === 0 && <div className="text-sm text-ink-400">没有证据记录。</div>}
          {e.evidence.map((x: any, i: number) => <EvidenceItem key={i} x={x} />)}
        </div>)}
    </Modal>
  );
}

function EvidenceItem({ x }: { x: any }) {
  const [open, setOpen] = useState(false);
  const quotes = [x.quote, x.detail?.quote2].filter(Boolean) as string[];
  return (
    <div className={`rounded-xl border p-3 ${x.kind === "counter" ? "border-amber-200 bg-amber-50/40" : "border-ink-100"}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-500">
        {x.kind === "counter" && <span className="tag-warn">反例/替代做法</span>}
        {x.case ? <span>病例 #{x.case.seq} · {x.case.source_label}{x.case.visit_date ? ` · ${x.case.visit_date}` : ""}</span> : <span>病例原文仅画像管理者可见</span>}
      </div>
      <div className="mt-1.5 space-y-1">{quotes.map((q, i) => <blockquote key={i} className="border-l-2 border-brand-300 pl-2.5 text-[13px] leading-relaxed text-ink-800">{q}</blockquote>)}</div>
      {x.case?.text && (<>
        <button type="button" className="mt-2 text-xs text-brand-700 hover:underline" onClick={() => setOpen(!open)}>{open ? "收起整份病例" : "查看整份病例（已脱敏，片段高亮）"}</button>
        {open && <div className="mt-1.5 whitespace-pre-wrap rounded-lg bg-ink-50 p-2.5 text-[12.5px] leading-relaxed text-ink-700">{splitByQuotes(x.case.text, quotes).map((s, i) => s.hit ? <mark key={i} className="rounded bg-amber-200/70 px-0.5">{s.t}</mark> : <span key={i}>{s.t}</span>)}</div>}
      </>)}
    </div>
  );
}

export { hasCritical };
