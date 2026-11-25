"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { AlertTriangle, ArrowRight, CheckCircle2, FileText, History, Lock, Pencil, Plus, Printer, RefreshCw, Save, ShieldCheck, Undo2 } from "lucide-react";
import { api, ApiError, blockersText, errMsg, fmtTime } from "@/lib/api";
import { Banner, Empty, ErrorBox, Modal, Skeleton, Spinner, StatusChip, useConfirm, useToast, useUnsavedGuard } from "@/components/ui";
import { useAction } from "@/lib/useAction";
import { useAuth } from "@/lib/auth";
import { Card, Collapse } from "./bits";
import { useWs } from "./ctx";

const SECTIONS: [string, string][] = [["chief_complaint", "主诉"], ["hpi", "现病史"], ["past_history", "既往史"], ["personal_family_history", "个人史 / 家族史"], ["allergies", "过敏史"], ["current_meds", "当前用药"], ["physical_exam", "体格检查"], ["tcm_four", "中医四诊"], ["labs", "辅助检查"], ["diagnoses", "诊断"], ["treatment", "治疗意见 / 医嘱"], ["followup", "随访 / 复诊建议"]];
const ORIGIN: Record<string, [string, string]> = { auto: ["自动汇总", "tag-neutral"], doctor: ["医师编辑", "tag-brand"], patient: ["患者自述", "tag-warn"] };
const SOFT = new Set(["plan_unconfirmed", "labs_unverified"]);
const KIND: Record<string, string> = { draft: "草稿", final: "定稿", amendment: "更正" };

export function RecordPanel() {
  const ws = useWs(); const { b, eid } = ws; const { can } = useAuth(); const toast = useToast();
  const sig = [b.encounter.row_version, b.plan?.row_version, b.prescriptions.map((r) => `${r.id}${r.status}${r.row_version}`).join(), b.diagnoses.map((d) => `${d.id}${d.status}${d.row_version}`).join(), b.labs.map((l) => `${l.id}${l.verified}`).join()].join("|");
  const open = useSWR(`record-open:${eid}:${sig}`, () => api.post(`/encounters/${eid}/record`), { revalidateOnFocus: false, keepPreviousData: true, shouldRetryOnError: false });
  if (open.error && !open.data) {
    const e = open.error as ApiError;
    return e.status === 404 ? <Card><Empty icon={<FileText size={22} />} title="该就诊尚无病历" hint="病历由接诊医师在诊疗过程中创建与定稿。" /></Card> : <ErrorBox error={e} onRetry={() => open.mutate()} />;
  }
  if (!open.data) return <div className="space-y-3"><Skeleton className="h-24 w-full" /><Skeleton className="h-64 w-full" /></div>;
  return <RecordEditor key={open.data.record.id} data={open.data} reopen={() => open.mutate()} canWrite={can("record.write") && !ws.readOnly} />;
}

function RecordEditor({ data, reopen, canWrite }: { data: any; reopen: () => Promise<any>; canWrite: boolean }) {
  const ws = useWs(); const { b } = ws; const { can } = useAuth(); const act = useAction(); const confirm = useConfirm(); const toast = useToast();
  const rec = data.record; const finalized = rec.status === "finalized";
  const blockers: { code: string; message: string }[] = data.finalize_blockers || [];
  const detail = useSWR(`/records/${rec.id}?_=${rec.version.version_no}:${rec.status}`, { revalidateOnFocus: false, keepPreviousData: true });
  const versions: any[] = detail.data?.record?.versions || [];
  const [viewId, setViewId] = useState<string | null>(null);
  const viewing = useSWR(viewId ? `/records/${rec.id}?version_id=${viewId}` : null, { revalidateOnFocus: false });

  const server: Record<string, string> = useMemo(() => Object.fromEntries(SECTIONS.map(([k]) => [k, rec.version.sections?.[k]?.text || ""])), [rec]);
  const [txt, setTxt] = useState(server); const base = useRef(server);
  const dirtyKeys = SECTIONS.map(([k]) => k).filter((k) => (txt[k] || "").trim() !== (base.current[k] || "").trim());
  const dirty = dirtyKeys.length > 0;
  useUnsavedGuard(dirty && !finalized);
  // Server → form sync, per section:  untouched → follow the server · my edit has landed (server == what I typed) → no longer dirty ·
  // my edit pending while the server holds something else → keep mine (a stale-version conflict is surfaced when saving).
  useEffect(() => {
    const nb = { ...base.current }; const next = { ...txt }; let ch = false;
    for (const [k] of SECTIONS) {
      const mine = (txt[k] || "").trim(), was = (base.current[k] || "").trim(), srv = (server[k] || "").trim();
      if (mine === was) { if (txt[k] !== server[k]) { next[k] = server[k]; ch = true; } nb[k] = server[k]; }
      else if (mine === srv) nb[k] = server[k];
    }
    base.current = nb; if (ch) setTxt(next);
    /* eslint-disable-next-line */ }, [server]);
  const [sugg, setSugg] = useState<Record<string, string>>({});
  const [amend, setAmend] = useState(false); const [reason, setReason] = useState("");
  const [waive, setWaive] = useState<Record<string, boolean>>({});
  const editable = canWrite && !finalized && b.encounter.status !== "COMPLETED";
  const amendable = can("record.amend") && finalized && ws.mayAct;                 // NOT readOnly: amending is exactly what happens after finalization
  const editing = editable || (amend && amendable);
  const origin = (k: string) => rec.version.sections?.[k]?.origin || "auto";

  const save = () => act.run("save", async () => {
    const sections = Object.fromEntries(dirtyKeys.map((k) => [k, txt[k]]));
    await api.put(`/records/${rec.id}/draft`, { sections, base_version_no: rec.version.version_no }); await reopen();
  }, { ok: "草稿已保存（新版本）", onStale: reopen });

  const refresh = () => act.run("refresh", async () => {
    if (dirty) { toast("warn", "请先保存当前修改，再刷新自动汇总"); return; }
    const r = await api.post(`/records/${rec.id}/refresh`); setSugg(r.suggestions || {}); await reopen();
    const n = Object.keys(r.suggestions || {}).length;
    toast(r.updated.length || n ? "ok" : "info", r.updated.length || n ? `已更新 ${r.updated.length} 段自动内容${n ? `；${n} 段您已编辑过的内容未被改动，下方提供了新的汇总供选择` : ""}` : "自动汇总已是最新，没有变化");
  }, { onStale: reopen });

  const accept = (k: string, mode: "replace" | "append") => act.run(`sg-${k}`, async () => { await api.post(`/records/${rec.id}/sections/${k}/accept-suggestion`, { mode }); setSugg((s) => { const n = { ...s }; delete n[k]; return n; }); await reopen(); }, { ok: mode === "replace" ? "已替换" : "已追加在您的文字之后", onStale: reopen });

  const hard = blockers.filter((x) => !SOFT.has(x.code)); const soft = blockers.filter((x) => SOFT.has(x.code));
  const ignore = soft.filter((x) => waive[x.code]).map((x) => x.code);
  const canFinalize = can("record.finalize") && !hard.length && soft.every((x) => waive[x.code]) && !dirty;
  const finalize = async () => {
    const c = await confirm({ title: "定稿并签署病历？", body: <>定稿将<b>冻结</b>本次病历（含诊断、已签署处方、检验结果的快照）并结束本次就诊。之后只能通过“更正”追加新版本并写明原因，原定稿版本永久保留。{ignore.length > 0 && <div className="mt-2 rounded bg-amber-50 p-2 text-xs text-amber-800">您选择了带着以下提醒定稿：{soft.filter((x) => waive[x.code]).map((x) => x.message).join("；")}（将记入审计）。</div>}</>, confirmText: "定稿" });
    if (!c.ok) return;
    act.run("finalize", async () => { await api.post(`/records/${rec.id}/finalize`, { ignore }); await ws.reload(); await reopen(); }, { ok: "病历已定稿", onStale: reopen, onError: (e) => { const t = blockersText(e); if (t.length) toast("err", t.join("；")); reopen(); } });
  };
  const doAmend = () => act.run("amend", async () => {
    const sections = Object.fromEntries(dirtyKeys.map((k) => [k, txt[k]]));
    await api.post(`/records/${rec.id}/amend`, { sections, reason: reason.trim() }); setAmend(false); setReason(""); await reopen();
  }, { ok: "已生成更正版本" });

  // ----- viewing an older version = read-only
  const v = viewId && viewing.data ? viewing.data.record.version : null;
  const shown = (k: string) => (v ? v.sections?.[k]?.text || "" : txt[k] || "");

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
      <div className="min-w-0 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="h-sec">病历 <span className="ml-1 font-mono text-xs font-normal text-ink-400">{rec.record_no}</span></h2><StatusChip s={rec.status} />
          <span className="text-xs text-ink-400">版本 v{rec.version.version_no}（{KIND[rec.version.kind]}）· {rec.version.author} · {fmtTime(rec.version.created_at)}</span>
          {editable && <button type="button" className="btn-secondary btn-sm ml-auto" disabled={act.any} onClick={refresh}>{act.is("refresh") ? <Spinner size={12} /> : <RefreshCw size={12} />}刷新自动汇总</button>}
        </div>
        {viewId && <Banner kind="info" title={`正在查看历史版本${v ? ` v${v.version_no}（${KIND[v.kind]}）` : ""}`} action={<button className="btn-secondary btn-sm" onClick={() => setViewId(null)}>返回当前版本</button>}>历史版本只读；每个版本都不可更改，可单独导出 PDF。</Banner>}
        {finalized && !viewId && <Banner kind="ok" title={`已于 ${fmtTime(rec.finalized_at)} 定稿并锁定`}>如发现错误，请使用“更正”写明原因并生成新版本，原定稿保留。{amend && <span className="font-medium">（正在更正：只修改需要改的段落）</span>}</Banner>}
        {editable && <div className="rounded-lg bg-ink-50 px-3 py-2 text-xs leading-relaxed text-ink-500">标注“自动汇总”的段落来自接诊资料、诊断、方案与处方，<b>您一旦编辑，系统以后绝不会覆盖它</b>；资料变化时只会在下方提示新的汇总供您选择。</div>}

        {SECTIONS.map(([k, title]) => {
          const [ol, oc] = ORIGIN[origin(k)] || ORIGIN.auto; const edited = dirtyKeys.includes(k);
          return (
            <section key={k} className={`card ${edited ? "border-amber-300" : ""}`}>
              <div className="flex items-center gap-2 border-b border-ink-50 px-4 py-2"><h3 className="text-[13px] font-semibold text-ink-800">{title}</h3>{!viewId && <span className={oc}>{ol}</span>}{edited && <span className="tag-warn">未保存</span>}</div>
              <div className="p-3">
                {editing && !viewId ? <textarea className="input min-h-[64px]" aria-label={title} value={txt[k] || ""} rows={Math.min(14, Math.max(2, (txt[k] || "").split("\n").length + 1))} onChange={(e) => setTxt({ ...txt, [k]: e.target.value })} placeholder="（空）" />
                  : <div className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-ink-800">{shown(k) || <span className="text-ink-300">（空）</span>}</div>}
                {sugg[k] && editable && (
                  <div className="mt-2 rounded-lg border border-brand-100 bg-brand-50/60 p-2.5"><div className="text-xs font-medium text-brand-800">资料已有更新，系统汇总出新的内容（您的文字未被改动）：</div>
                    <div className="mt-1 whitespace-pre-wrap rounded bg-white p-2 text-[13px] text-ink-700">{sugg[k]}</div>
                    <div className="mt-2 flex gap-2"><button type="button" className="btn-secondary btn-sm" disabled={act.any || dirty} onClick={() => accept(k, "append")}><Plus size={12} />追加到我的文字后</button><button type="button" className="btn-secondary btn-sm" disabled={act.any || dirty} onClick={() => accept(k, "replace")}>用新内容替换</button><button type="button" className="btn-ghost btn-sm" onClick={() => setSugg((s) => { const n = { ...s }; delete n[k]; return n; })}>忽略</button></div>
                    {dirty && <div className="mt-1 text-[11px] text-amber-700">请先保存当前修改再采纳。</div>}</div>)}
              </div>
            </section>);
        })}
      </div>

      {/* ---------------------------------------------------------------- side */}
      <aside className="space-y-3 xl:sticky xl:top-2 xl:self-start">
        <Card title="定稿" icon={<Lock size={15} className="text-ink-500" />}>
          {finalized ? <div className="space-y-2 text-sm"><div className="flex items-center gap-1.5 text-jade-700"><CheckCircle2 size={16} />已定稿</div>
            {detail.data && <div className={`flex items-center gap-1.5 text-xs ${detail.data.integrity_ok ? "text-ink-500" : "text-danger-600"}`}><ShieldCheck size={13} />{detail.data.integrity_ok ? "内容完整性校验通过" : "完整性校验失败：内容与签名不一致！"}</div>}
            {amendable && !amend && <button className="btn-secondary w-full" onClick={() => setAmend(true)}><Pencil size={14} />更正病历…</button>}
            {amend && <div className="space-y-2"><textarea className="input min-h-[64px] text-xs" placeholder="更正原因（必填，至少 5 个字）" value={reason} onChange={(e) => setReason(e.target.value)} aria-label="更正原因" />
              <div className="flex gap-2"><button className="btn-primary btn-sm flex-1" disabled={act.any || reason.trim().length < 5 || !dirty} onClick={doAmend}>{act.is("amend") ? <Spinner size={12} /> : null}提交更正</button><button className="btn-ghost btn-sm" onClick={() => { setAmend(false); setTxt(base.current); setReason(""); }}>取消</button></div>
              {!dirty && <div className="text-[11px] text-ink-400">请在左侧修改需要更正的段落。</div>}</div>}</div> : (
            <div className="space-y-2.5">
              {blockers.length === 0 ? <div className="flex items-center gap-1.5 text-sm text-jade-700"><CheckCircle2 size={15} />可以定稿</div> : <>
                {hard.length > 0 && <div><div className="mb-1 text-xs font-semibold text-danger-700">定稿前必须处理</div><ul className="space-y-1">{hard.map((x) => <li key={x.code + x.message} className="flex gap-1.5 text-xs text-ink-700"><AlertTriangle size={12} className="mt-0.5 shrink-0 text-danger-500" />{x.message}</li>)}</ul></div>}
                {soft.length > 0 && <div><div className="mb-1 text-xs font-semibold text-amber-700">提醒（可在知情后放行）</div>
                  <ul className="space-y-1.5">{soft.map((x) => <li key={x.code}><label className="flex items-start gap-1.5 text-xs text-ink-700"><input type="checkbox" className="mt-0.5 h-3.5 w-3.5 accent-brand-600" checked={!!waive[x.code]} onChange={(e) => setWaive({ ...waive, [x.code]: e.target.checked })} />{x.message}<span className="text-ink-400">（我知悉，仍定稿）</span></label></li>)}</ul></div>}</>}
              {dirty && <div className="text-xs text-amber-700">有未保存的修改，请先保存。</div>}
              {editable && <button className="btn-secondary w-full" disabled={act.any || !dirty} onClick={save}>{act.is("save") ? <Spinner size={14} /> : <Save size={14} />}保存草稿</button>}
              {can("record.finalize") && editable && <button className="btn-success w-full" disabled={act.any || !canFinalize} onClick={finalize}>{act.is("finalize") ? <Spinner size={14} /> : <Lock size={14} />}定稿并结束就诊</button>}
              <div className="text-[11px] leading-relaxed text-ink-400">定稿由医师本人执行；系统不会自动定稿。</div></div>)}
        </Card>
        <Card title="导出">
          <div className="flex flex-col gap-2"><a className="btn-secondary" href={`/api/v1/records/${rec.id}/pdf${viewId ? `?version_id=${viewId}` : ""}`} target="_blank" rel="noreferrer"><Printer size={14} />打印 / 导出 PDF{viewId ? "（该版本）" : ""}</a>
            {!finalized && <div className="text-[11px] text-ink-400">草稿 PDF 会带“草稿”水印，不能作为正式病历。</div>}</div>
        </Card>
        <Card title={`版本历史（${versions.length}）`} icon={<History size={15} className="text-ink-500" />}>
          {versions.length === 0 ? <Skeleton className="h-16 w-full" /> : <ol className="max-h-72 space-y-1 overflow-auto pr-1">{versions.map((x) => (
            <li key={x.id}><button type="button" className={`w-full rounded-lg px-2 py-1.5 text-left text-xs transition hover:bg-ink-50 ${viewId === x.id ? "bg-brand-50" : ""}`} onClick={() => setViewId(x.id === rec.version.id ? null : x.id)}>
              <div className="flex items-center gap-1.5"><span className="font-medium text-ink-800">v{x.version_no}</span><span className={x.kind === "final" ? "tag-ok" : x.kind === "amendment" ? "tag-warn" : "tag-neutral"}>{KIND[x.kind]}</span><span className="ml-auto text-ink-400">{fmtTime(x.created_at)}</span></div>
              <div className="mt-0.5 truncate text-ink-500" title={x.note}>{x.author}：{x.note}</div></button></li>))}</ol>}
        </Card>
      </aside>
    </div>
  );
}
