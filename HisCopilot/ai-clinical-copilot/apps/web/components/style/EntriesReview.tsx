"use client";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { Check, CheckCheck, Eye, FileCheck2, Pencil, Plus, RotateCcw, ShieldAlert, X } from "lucide-react";
import { Banner, Empty, ErrorBox, Field, Modal, Skeleton, Spinner, useConfirm, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { CATEGORY_HINT, CATEGORY_LABEL, CATEGORY_ORDER, REVIEW_STATUS, chip, hasCritical } from "@/lib/style";
import { CatTag, EvidenceModal, FlagList, Support } from "@/components/style/shared";

type RF = "pending" | "approved" | "rejected" | "all";

/** 经验条目审核：只有所有者医师 / 指定的临床审核医师能操作；管理员只读。通过前能逐条看到证据与风险提示。 */
export function EntriesReview({ p, onChanged }: { p: any; onChanged: () => void }) {
  const { data, error, isLoading, mutate } = useSWR(`/style/profiles/${p.id}/entries?version=draft`);
  const act = useAction(); const confirm = useConfirm(); const toast = useToast();
  const [rf, setRf] = useState<RF>("pending"); const [cat, setCat] = useState<string>("all");
  const [ev, setEv] = useState<string | null>(null); const [editing, setEditing] = useState<any>(null); const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [bulkResult, setBulkResult] = useState<any>(null);
  const canReview = p.can_review;
  const refresh = () => { mutate(); onChanged(); };
  const entries: any[] = data?.entries || [];
  const counts = data?.review_counts || {};
  const shown = useMemo(() => entries.filter((e) => (rf === "all" || e.review_status === rf) && (cat === "all" || e.category === cat)), [entries, rf, cat]);
  const isEligible = (e: any) => e.review_status === "pending" && !hasCritical(e.flags);
  const eligibleShown = useMemo(() => shown.filter(isEligible), [shown]);
  const pendingIds = useMemo(() => new Set(entries.filter(isEligible).map((e) => e.id)), [entries]);
  const selectedEntries = useMemo(() => entries.filter((e) => selected[e.id] && pendingIds.has(e.id)), [entries, selected, pendingIds]);
  const groups = useMemo(() => {
    const m = new Map<string, any[]>();
    for (const e of shown) { const k = e.group_key || "（全库）"; (m.get(k) || m.set(k, []).get(k)!).push(e); }
    return [...m.entries()].map(([k, es]) => [k, es.sort((a, b) => CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category))] as [string, any[]]);
  }, [shown]);

  const toggleOne = (id: string) => setSelected((cur) => ({ ...cur, [id]: !cur[id] }));
  const toggleGroup = (es: any[]) => {
    const elig = es.filter(isEligible);
    if (!elig.length) return;
    const allOn = elig.every((e) => selected[e.id]);
    setSelected((cur) => {
      const nxt = { ...cur };
      for (const e of elig) nxt[e.id] = !allOn;
      return nxt;
    });
  };
  const selectAllShown = () => {
    if (!eligibleShown.length) return toast("info", "当前筛选下没有可批量通过的待审核条目");
    const allOn = eligibleShown.every((e) => selected[e.id]);
    setSelected((cur) => {
      const nxt = { ...cur };
      for (const e of eligibleShown) nxt[e.id] = !allOn;
      return nxt;
    });
  };

  const review = (e: any, action: string, extra: any = {}) => act.run("e" + e.id, async () => { await api.post(`/style/entries/${e.id}/review`, { action, row_version: e.row_version, ...extra }); refresh(); }, { onStale: refresh });
  const approve = async (e: any) => {
    if (hasCritical(e.flags)) {
      const r = await confirm({
        title: "该条经验含严重安全提示", body: <><FlagList flags={e.flags.filter((f: any) => f.severity === "critical")} /><p className="mt-2 text-sm">历史用药习惯<b>不是临床规则</b>。您可以：<br />① 仅留作资料（<b>不会</b>进入 AI 提示词）；或② 填写书面理由后允许 AI 参考（理由会被保存并随审计留存）。</p></>,
        confirmText: "允许 AI 参考（需填写理由）", reason: { label: "书面理由（至少 8 个字）", min: 8, placeholder: "例如：本人在寒湿重症且辨证明确时使用，已知配伍风险，会按患者情况调整" }
      });
      if (r.ok) review(e, "approve", { ack_reason: r.reason });
      return;
    }
    review(e, "approve");
  };
  const approveNoAi = (e: any) => review(e, "approve_no_ai");
  const reject = async (e: any) => { const r = await confirm({ title: "驳回这条经验？", body: e.title, confirmText: "驳回", danger: true, reason: { label: "原因（可选）", min: 0 } }); if (r.ok) review(e, "reject", { note: r.reason }); };
  const bulkApprove = async () => {
    if (!selectedEntries.length) return;
    const byCat: Record<string, number> = {};
    for (const e of selectedEntries) byCat[e.category] = (byCat[e.category] || 0) + 1;
    const catSummary = CATEGORY_ORDER.filter((c) => byCat[c]).map((c) => `${CATEGORY_LABEL[c]} ${byCat[c]} 条`).join("、");
    const r = await confirm({
      title: `批量通过 ${selectedEntries.length} 条候选经验？`,
      body: <>包含：{catSummary}。<br />说明：含“证据较少 / 可能过时”等非严重提示的条目也会通过；含严重安全提示的条目已自动排除，需逐条填写理由后审核。</>,
      confirmText: `确认通过（${selectedEntries.length} 条）`,
    });
    if (!r.ok) return;
    const reqId = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `bulk_${Date.now()}`;
    act.run("bulk", async () => {
      const res = await api.post<any>(`/style/profiles/${p.id}/entries/bulk-review`, {
        action: "approve",
        request_id: reqId,
        items: selectedEntries.map((e) => ({ id: e.id, row_version: e.row_version })),
      });
      setBulkResult(res);
      setSelected((cur) => {
        const doneIds = new Set((res.results || []).filter((x: any) => x.outcome === "approved").map((x: any) => x.id));
        const nxt: Record<string, boolean> = {};
        for (const [k, v] of Object.entries(cur)) if (v && !doneIds.has(k) && pendingIds.has(k)) nxt[k] = true;
        return nxt;
      });
      refresh();
    }, { onStale: refresh });
  };

  if (error) return <ErrorBox error={error} onRetry={() => mutate()} />;
  if (isLoading || !data) return <Skeleton className="h-64 w-full" />;
  return (
    <div className="space-y-4">
      {!canReview && <Banner kind="warn" title="只读">只有该画像的所有者医师或指定的临床审核医师可以审核与发布条目。{p.proxy_created ? "管理员代建的画像不能由管理员审核临床内容（职责分离）。" : ""}</Banner>}
      <div className="rounded-lg border border-brand-100 bg-brand-50/40 px-3.5 py-2 text-xs text-ink-600">系统从已确认病例<b>统计归纳</b>候选经验（可点“查看证据”溯源原文），勾选后可批量通过，发布后不可变。</div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1">{([["pending", "待审核"], ["approved", "已通过"], ["rejected", "已驳回"], ["all", "全部"]] as [RF, string][]).map(([k, t]) => (
          <button key={k} onClick={() => setRf(k)} className={`rounded-full px-3 py-1 text-xs font-medium ${rf === k ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-600 hover:bg-ink-200"}`}>{t}{k !== "all" ? ` ${counts[k] || 0}` : ""}</button>))}</div>
        <select className="input !w-auto !py-1 text-xs" value={cat} onChange={(e) => setCat(e.target.value)}><option value="all">全部类别</option>{CATEGORY_ORDER.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}</select>
        {canReview && (<div className="ml-auto flex flex-wrap gap-2">
          <button className="btn-secondary btn-sm" disabled={act.any} onClick={() => setAdding(true)}><Plus size={13} />手工补充经验</button>
          <button className="btn-secondary btn-sm" disabled={act.any || eligibleShown.length === 0} onClick={selectAllShown}><CheckCheck size={13} />全选可批量通过（{eligibleShown.length}）</button>
          <PublishButton p={p} counts={counts} onDone={refresh} /></div>)}
      </div>
      {canReview && selectedEntries.length > 0 && (
        <div className="sticky top-2 z-20 flex flex-wrap items-center gap-3 rounded-xl border border-brand-200 bg-brand-50 px-4 py-2.5 text-sm shadow-sm">
          <span className="font-medium text-brand-900">已选 <b>{selectedEntries.length}</b> 条待审核经验</span>
          <button className="btn-primary btn-sm" disabled={act.any} onClick={bulkApprove}>{act.is("bulk") ? <Spinner size={12} /> : <CheckCheck size={13} />}批量通过</button>
          <button className="btn-ghost btn-sm" disabled={act.any} onClick={() => setSelected({})}>清除</button>
        </div>
      )}
      {entries.length === 0 ? <div className="card"><Empty icon={<FileCheck2 size={22} />} title="草稿版本里还没有条目" hint="先在“导入资料”里导入并确认病例、完成抽取；也可以“手工补充经验”。" /></div> :
        groups.length === 0 ? <div className="card"><Empty title="这个筛选下没有条目" /></div> :
          groups.map(([g, es]) => {
            const elig = es.filter(isEligible);
            const grpChecked = elig.length > 0 && elig.every((e) => selected[e.id]);
            return (
              <section key={g} className="card overflow-hidden"><div className="flex items-center justify-between border-b border-ink-100 bg-ink-50/60 px-4 py-2.5"><h3 className="text-sm font-semibold text-ink-800">{g === "（全库）" ? "全库层面" : `证候 / 病证：${g}`}</h3>
                {canReview && elig.length > 0 && <label className="flex cursor-pointer items-center gap-1.5 text-xs text-ink-600"><input type="checkbox" className="h-3.5 w-3.5 accent-brand-600" checked={grpChecked} onChange={() => toggleGroup(es)} />全选本组可批量项（{elig.length}）</label>}</div>
                <ul className="divide-y divide-ink-50">{es.map((e) => (
                  <EntryRow key={e.id} e={e} canReview={canReview} checked={!!selected[e.id]} onToggle={() => toggleOne(e.id)} busy={act.is("e" + e.id)} onEvidence={() => setEv(e.id)} onApprove={() => approve(e)} onApproveNoAi={() => approveNoAi(e)} onReject={() => reject(e)}
                    onRevert={() => review(e, "revert")} onEdit={() => setEditing(e)} />))}</ul></section>);
          })}
      <EvidenceModal entryId={ev} onClose={() => setEv(null)} />
      <EditEntry e={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh(); }} />
      <AddEntry pid={p.id} open={adding} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); refresh(); }} />
      <Modal open={!!bulkResult} onClose={() => setBulkResult(null)} width="max-w-2xl" title="批量审核结果" footer={<button className="btn-primary" onClick={() => setBulkResult(null)}>知道了</button>}>
        {bulkResult && (
          <div className="space-y-3">
            <p className="text-sm text-ink-700">已通过 <b>{bulkResult.summary?.approved ?? 0}</b> 条，跳过 <b>{bulkResult.summary?.skipped ?? 0}</b> 条（共请求 {bulkResult.summary?.requested ?? 0} 条）。</p>
            <ul className="max-h-80 divide-y divide-ink-100 overflow-y-auto rounded-lg border border-ink-100">
              {(bulkResult.results || []).map((r: any, idx: number) => (
                <li key={r.id + idx} className="flex items-start justify-between gap-3 px-3 py-2 text-xs">
                  <span className="font-medium text-ink-800">{r.title || r.id}</span>
                  {r.outcome === "approved" ? <span className="tag-ok shrink-0">✓ 已通过</span> : <span className="tag-warn shrink-0">跳过：{r.message}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Modal>
    </div>
  );
}

function EntryRow({ e, canReview, checked, onToggle, busy, onEvidence, onApprove, onApproveNoAi, onReject, onRevert, onEdit }: any) {
  const [rs, rc] = chip(REVIEW_STATUS, e.review_status); const crit = hasCritical(e.flags);
  return (
    <li className="px-4 py-3.5">
      <div className="flex flex-wrap items-start gap-2">
        {canReview && e.review_status === "pending" && (
          <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-brand-600 disabled:cursor-not-allowed disabled:opacity-40" checked={checked} disabled={crit} title={crit ? "含严重安全提示，需逐条填写理由后审核" : "选择以批量通过"} onChange={onToggle} />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5"><CatTag c={e.category} /><span className="font-medium text-ink-900">{e.title}</span><span className={rc}>{rs}</span>
            {e.review_status === "approved" && (e.ai_eligible ? <span className="tag-ok" title="该条会进入 AI 提示词">供 AI 参考</span> : <span className="tag-neutral" title="仅作资料，不会进入 AI 提示词">仅作资料</span>)}
            {e.origin === "manual" && <span className="tag-neutral">手工</span>}{e.origin === "carried" && <span className="tag-neutral">沿用自上一版</span>}</div>
          <p className="mt-1.5 text-[13px] leading-relaxed text-ink-700">{e.summary}</p>
          {(e.conditions || e.exceptions) && <p className="mt-1 text-xs leading-relaxed text-ink-500">{e.conditions && <>适用：{e.conditions}　</>}{e.exceptions && <>例外：{e.exceptions}</>}</p>}
          <div className="mt-2 flex flex-wrap items-center gap-2"><Support n={e.support_count} of={e.group_total} conf={e.confidence} />
            <button className="inline-flex items-center gap-1 text-xs text-brand-700 hover:underline" onClick={onEvidence}><Eye size={13} />查看证据</button></div>
          <div className="mt-2"><FlagList flags={e.flags} /></div>
          {e.risk_ack_reason && <p className="mt-1.5 text-xs text-amber-700">审核人放行理由：{e.risk_ack_reason}</p>}
        </div>
        {canReview && (
          <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
            {e.review_status !== "approved" && <button className="btn-primary btn-sm" disabled={busy} onClick={onApprove}>{busy ? <Spinner size={12} /> : <Check size={13} />}通过</button>}
            {e.review_status !== "approved" && crit && <button className="btn-secondary btn-sm" disabled={busy} onClick={onApproveNoAi} title="保留为资料，但不会交给 AI"><ShieldAlert size={13} />通过（不供 AI）</button>}
            {e.review_status !== "rejected" && <button className="btn-secondary btn-sm" disabled={busy} onClick={onReject}><X size={13} />驳回</button>}
            {e.review_status !== "pending" && <button className="btn-ghost btn-sm" disabled={busy} onClick={onRevert}><RotateCcw size={12} />撤销</button>}
            <button className="btn-ghost btn-sm" disabled={busy} onClick={onEdit}><Pencil size={12} />编辑</button>
          </div>)}
      </div>
    </li>
  );
}

function PublishButton({ p, counts, onDone }: { p: any; counts: any; onDone: () => void }) {
  const act = useAction(); const confirm = useConfirm();
  const pend = counts.pending || 0; const appr = counts.approved || 0;
  const go = async () => {
    const r = await confirm({ title: `发布版本 v${p.draft_version?.version_no ?? ""}？`, body: <>将发布 <b>{appr}</b> 条已通过的条目。发布后<b>版本内容不可再修改</b>；之后新增资料只会产生新的候选，需再次审核发布。{p.active_version ? "当前正在使用的版本会被标记为“已被取代”（仍可追溯和回退）。" : ""}</>, confirmText: "发布", reason: { label: "版本说明（可选）", min: 0 } });
    if (r.ok) act.run("pub", async () => { await api.post(`/style/profiles/${p.id}/publish`, { note: r.reason }); onDone(); }, { ok: "已发布" });
  };
  return <button className="btn-success btn-sm" disabled={act.any || pend > 0 || appr === 0} title={pend > 0 ? `还有 ${pend} 条待审核，处理完才能发布` : appr === 0 ? "至少通过一条才能发布" : ""} onClick={go}>{act.is("pub") ? <Spinner size={12} /> : <FileCheck2 size={13} />}发布版本</button>;
}

function EditEntry({ e, onClose, onSaved }: { e: any; onClose: () => void; onSaved: () => void }) {
  const act = useAction(); const [f, setF] = useState<any>({}); const [seen, setSeen] = useState<string | null>(null);
  if (e && seen !== e.id) { setSeen(e.id); setF({ title: e.title, summary: e.summary, conditions: e.conditions, exceptions: e.exceptions }); }
  return (
    <Modal open={!!e} onClose={onClose} width="max-w-2xl" title="编辑经验条目" footer={<><button className="btn-secondary" onClick={onClose}>取消</button>
      <button className="btn-primary" disabled={act.any} onClick={() => act.run("edit", async () => { await api.post(`/style/entries/${e.id}/review`, { action: "edit", edits: f, row_version: e.row_version }); onSaved(); }, { ok: "已保存（如之前已通过，需重新审核）", onStale: onSaved })}>保存</button></>}>
      <p className="mb-3 text-xs text-ink-500">只改文字表述；统计数字与证据不会改变。保存后该条会回到“待审核”。</p>
      <div className="space-y-3"><Field label="标题"><input className="input" value={f.title || ""} maxLength={200} onChange={(x) => setF({ ...f, title: x.target.value })} /></Field>
        <Field label="内容摘要"><textarea className="input" rows={4} value={f.summary || ""} onChange={(x) => setF({ ...f, summary: x.target.value })} /></Field>
        <Field label="适用条件"><textarea className="input" rows={2} value={f.conditions || ""} onChange={(x) => setF({ ...f, conditions: x.target.value })} /></Field>
        <Field label="例外 / 不适用 / 替代做法"><textarea className="input" rows={2} value={f.exceptions || ""} onChange={(x) => setF({ ...f, exceptions: x.target.value })} /></Field></div>
    </Modal>
  );
}

function AddEntry({ pid, open, onClose, onSaved }: { pid: string; open: boolean; onClose: () => void; onSaved: () => void }) {
  const act = useAction(); const [f, setF] = useState({ category: "note", title: "", summary: "", group_key: "", conditions: "", exceptions: "" });
  const ok = f.title.trim() && f.summary.trim().length >= 6;
  return (
    <Modal open={open} onClose={onClose} width="max-w-2xl" title="手工补充经验" footer={<><button className="btn-secondary" onClick={onClose}>取消</button>
      <button className="btn-primary" disabled={!ok || act.any} onClick={() => act.run("add", async () => { await api.post(`/style/profiles/${pid}/entries`, f); setF({ ...f, title: "", summary: "", conditions: "", exceptions: "" }); onSaved(); }, { ok: "已添加（待审核）" })}>添加</button></>}>
      <p className="mb-3 text-xs text-ink-500">手工条目没有病例统计证据，会明确标注“医师自述”，置信度固定为较低。仍需审核后才会发布。</p>
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2"><Field label="类别"><select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{CATEGORY_ORDER.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}</select></Field>
          <Field label="适用病证（可选）"><input className="input" value={f.group_key} placeholder="如：肝阳上亢" onChange={(e) => setF({ ...f, group_key: e.target.value })} /></Field></div>
        <Field label="标题" required><input className="input" maxLength={200} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
        <Field label="内容" required hint="不要写患者信息。"><textarea className="input" rows={4} value={f.summary} onChange={(e) => setF({ ...f, summary: e.target.value })} /></Field>
        <Field label="适用条件"><textarea className="input" rows={2} value={f.conditions} onChange={(e) => setF({ ...f, conditions: e.target.value })} /></Field>
        <Field label="例外 / 不适用"><textarea className="input" rows={2} value={f.exceptions} onChange={(e) => setF({ ...f, exceptions: e.target.value })} /></Field>
      </div>
    </Modal>
  );
}
