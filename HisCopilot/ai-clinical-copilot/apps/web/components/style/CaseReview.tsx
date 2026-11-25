"use client";
import { useState } from "react";
import useSWR from "swr";
import { Check, CheckCheck, ChevronDown, ChevronUp, Pencil, RotateCcw, ShieldAlert, X } from "lucide-react";
import { Banner, Empty, ErrorBox, Modal, SkeletonList, Spinner, useConfirm, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { CASE_STATUS, chip } from "@/lib/style";
import { FlagList } from "@/components/style/shared";

type F = "all" | "pending" | "flagged" | "confirmed" | "excluded";
const FILTERS: [F, string][] = [["all", "全部"], ["pending", "待确认"], ["flagged", "需关注"], ["confirmed", "已确认"], ["excluded", "已排除"]];
const QS: Record<F, string> = { all: "", pending: "&status=pending_review", flagged: "&flagged=true", confirmed: "&status=confirmed", excluded: "&status=excluded" };

/** 逐份核对：脱敏后的文本、识别出的药味与克数、警告。确认 = 医师承诺“我看过这份文本，没有遗留患者信息，药名克数无误”。 */
export function CaseReview({ iid, stats, readOnly, onChanged }: { iid: string; stats: any; readOnly: boolean; onChanged: () => void }) {
  const [f, setF] = useState<F>(stats.pending ? "pending" : "all"); const [page, setPage] = useState(1);
  const act = useAction(); const confirm = useConfirm(); const toast = useToast();
  const { data, error, isLoading, mutate } = useSWR(`/style/imports/${iid}/cases?page=${page}&size=20${QS[f]}`);
  const [editing, setEditing] = useState<any>(null);
  const refresh = () => { mutate(); onChanged(); };
  const pages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;

  const bulk = (only_clean: boolean) => act.run("bulk", async () => {
    const r = await api.post(`/style/imports/${iid}/cases/bulk`, { action: "confirm", only_clean });
    const why = Object.entries<number>(r.skipped_reasons || {}).map(([k, n]) => `${n} 份${k}`).join("；");
    toast(r.changed ? "ok" : "info", `已确认 ${r.changed} 份${why ? `；未确认：${why}` : ""}`);
    refresh();
  });
  const bulkAll = async () => {
    const r = await confirm({ title: "确认所有待确认病例？", body: <>这会把<b>包含警告</b>的病例也一并确认（疑似重复录入的仍会跳过，需单独处理）。请确认您已浏览过这些病例的脱敏文本。脱敏风险为“高”的病例不会被确认。</>, confirmText: "全部确认" });
    if (r.ok) bulk(false);
  };
  const one = (c: any, status: string) => act.run("c" + c.id, async () => { await api.patch(`/style/cases/${c.id}`, { status, row_version: c.row_version }); refresh(); }, { onStale: refresh });

  return (
    <section className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-ink-100 px-4 py-3">
        <h2 className="h-sec">逐份核对病例</h2>
        <span className="text-xs text-ink-500">待确认 {stats.pending || 0} · 已确认 {stats.confirmed || 0} · 已排除 {stats.excluded || 0}</span>
        {!readOnly && (<div className="ml-auto flex flex-wrap gap-2">
          <button className="btn-secondary btn-sm" disabled={act.any || !stats.pending} onClick={() => bulk(true)} title="只确认没有任何警告、脱敏风险低、且不是疑似重复的病例"><Check size={13} />确认无警告病例</button>
          <button className="btn-secondary btn-sm" disabled={act.any || !stats.pending} onClick={bulkAll}><CheckCheck size={13} />确认全部待确认</button></div>)}
      </div>
      {!readOnly && <div className="border-b border-ink-100 bg-amber-50/50 px-4 py-2.5 text-xs leading-relaxed text-amber-800">请核对：① 文本中<b>不再含有患者姓名、电话、住址、编号</b>（已被替换为【姓名】等占位符）；② 下方识别出的<b>药名与克数</b>与原文一致（OCR 来源尤其容易出错）。点击“确认”即表示您已核对。<b>只有已确认的病例才会进入抽取。</b></div>}
      <div className="flex gap-1 overflow-x-auto border-b border-ink-100 px-3 py-2">
        {FILTERS.map(([k, t]) => <button key={k} onClick={() => { setF(k); setPage(1); }} className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium ${f === k ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-600 hover:bg-ink-200"}`}>{t}</button>)}
      </div>
      <div className="space-y-3 p-3">
        {error ? <ErrorBox error={error} onRetry={() => mutate()} /> : isLoading ? <SkeletonList rows={3} /> : !data?.cases.length ? <Empty title="这个筛选下没有病例" /> :
          data.cases.map((c: any) => <CaseCard key={c.id} c={c} readOnly={readOnly} busy={act.is("c" + c.id)} onStatus={(s) => one(c, s)} onEdit={() => setEditing(c)} />)}
      </div>
      {data && data.total > data.size && (
        <div className="flex items-center justify-center gap-3 border-t border-ink-100 py-2.5 text-sm">
          <button className="btn-ghost btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span className="text-ink-500">{page} / {pages}（共 {data.total} 份）</span><button className="btn-ghost btn-sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>下一页</button></div>)}
      <EditModal c={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh(); }} />
    </section>
  );
}

function CaseCard({ c, readOnly, busy, onStatus, onEdit }: { c: any; readOnly: boolean; busy: boolean; onStatus: (s: string) => void; onEdit: () => void }) {
  const [open, setOpen] = useState(false);
  const [st, cls] = chip(CASE_STATUS, c.status);
  const high = c.redaction?.risk === "high"; const med = c.redaction?.risk === "medium";
  const counts = Object.entries<number>(c.redaction?.counts || {});
  const long = (c.text || "").length > 260;
  return (
    <div className={`rounded-xl border p-3.5 ${c.status === "excluded" ? "border-ink-100 bg-ink-50/50 opacity-80" : high ? "border-danger-200" : "border-ink-100"}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-500">
        <b className="text-ink-800">#{c.seq}</b>{c.section && <span className="tag-neutral">{c.section}</span>}<span>{c.source_label}</span>{c.visit_date && <span>· {c.visit_date}</span>}
        <span className={cls}>{st}</span>{c.kind === "note" && <span className="tag-neutral">经验笔记</span>}{c.origin === "ocr" && <span className="tag-warn">OCR</span>}
        {high && <span className="tag-err"><ShieldAlert size={11} />脱敏风险高</span>}{med && <span className="tag-warn">脱敏风险中</span>}
        {c.dup_of_id && <span className="tag-warn">疑似重复</span>}
        <div className="ml-auto flex gap-1.5">
          {!readOnly && c.status !== "confirmed" && <button className="btn-primary btn-sm" disabled={busy || high} title={high ? "请先编辑文本遮盖残留信息" : ""} onClick={() => onStatus("confirmed")}>{busy ? <Spinner size={12} /> : <Check size={13} />}确认</button>}
          {!readOnly && c.status === "confirmed" && <button className="btn-secondary btn-sm" disabled={busy} onClick={() => onStatus("pending_review")}><RotateCcw size={12} />撤销确认</button>}
          {!readOnly && c.status !== "excluded" && <button className="btn-secondary btn-sm" disabled={busy} onClick={() => onStatus("excluded")}><X size={13} />排除</button>}
          {!readOnly && c.status === "excluded" && <button className="btn-secondary btn-sm" disabled={busy} onClick={() => onStatus("pending_review")}><RotateCcw size={12} />恢复</button>}
          {!readOnly && c.status !== "excluded" && <button className="btn-ghost btn-sm" disabled={busy} onClick={onEdit}><Pencil size={12} />编辑</button>}
        </div>
      </div>
      {/* 医师必须核对的两样东西放在全文之前：识别出的药味+克数、已遮盖了什么——不要埋在长文本下面 */}
      {c.herbs?.length > 0 && (
        <div className="mt-2.5 rounded-lg bg-ink-50/70 p-2.5"><div className="mb-1 text-[11px] font-medium text-ink-500">系统识别的饮片与克数（共 {c.herbs.length} 味，请与下方原文核对）</div>
          <div className="flex flex-wrap gap-1.5">{c.herbs.map((h: any) => <span key={h.name} className="kbd !text-[11px] !text-ink-700">{h.name} {h.dose_g}g{h.special ? `（${h.special}）` : ""}</span>)}</div></div>)}
      {c.kind === "case" && !c.herbs?.length && <div className="mt-2.5 rounded-lg bg-amber-50/70 px-2.5 py-1.5 text-[11.5px] text-amber-800">未识别到“药名+克数”的处方。</div>}
      <div className="mt-2 text-[11.5px] text-ink-500">{counts.length > 0 ? <>已自动遮盖：<b className="text-ink-700">{counts.map(([k, n]) => `${k}×${n}`).join("、")}</b></> : "没有发现需要遮盖的个人信息"}</div>
      <div className={`mt-2 whitespace-pre-wrap text-[13px] leading-relaxed text-ink-800 ${!open && long ? "line-clamp-5" : ""}`}>{c.text}</div>
      {long && <button type="button" className="mt-1 inline-flex items-center gap-0.5 text-xs text-brand-700 hover:underline" onClick={() => setOpen(!open)}>{open ? <>收起<ChevronUp size={12} /></> : <>展开全文<ChevronDown size={12} /></>}</button>}
      {high && <div className="mt-2"><Banner kind="err" title="脱敏后仍有疑似个人信息">{(c.redaction?.residual || []).join("；")}。请点击“编辑”，把残留信息改成【姓名】等占位符后再确认。</Banner></div>}
      <div className="mt-2"><FlagList flags={(c.anomalies || []).filter((a: any) => a.code !== "redaction_risk")} /></div>
    </div>
  );
}

function EditModal({ c, onClose, onSaved }: { c: any; onClose: () => void; onSaved: () => void }) {
  const act = useAction(); const [text, setText] = useState(""); const [seen, setSeen] = useState<string | null>(null);
  if (c && seen !== c.id) { setSeen(c.id); setText(c.text); }
  return (
    <Modal open={!!c} onClose={onClose} width="max-w-3xl" title={`编辑病例 #${c?.seq ?? ""}`}
      footer={<><button className="btn-secondary" onClick={onClose}>取消</button><button className="btn-primary" disabled={act.any || text.trim().length < 10} onClick={() => act.run("save", async () => { await api.patch(`/style/cases/${c.id}`, { text, row_version: c.row_version }); onSaved(); }, { ok: "已保存，请重新确认", onStale: onSaved })}>保存</button></>}>
      <p className="mb-2 text-xs text-ink-500">可修正 OCR 错字、药名克数，或把残留的个人信息改成【姓名】等占位符。保存后会<b>再次自动脱敏</b>并重新解析药味，原先的“已确认”会被撤销，需重新确认。</p>
      <textarea className="input font-mono text-[13px]" rows={16} value={text} onChange={(e) => setText(e.target.value)} />
    </Modal>
  );
}
