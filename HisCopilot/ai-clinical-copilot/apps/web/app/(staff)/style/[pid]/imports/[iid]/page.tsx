"use client";
import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { ArrowLeft, ArrowRight, CheckCircle2, Circle, Cpu, FileText, Loader2, ShieldAlert } from "lucide-react";
import { Banner, ErrorBox, Skeleton, Spinner, useConfirm } from "@/components/ui";
import { api, fmtTime } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { IMPORT_STATUS, MAPPING_FIELDS, PARSE_STATUS, chip } from "@/lib/style";
import { Stat } from "@/components/style/shared";
import { CaseReview } from "@/components/style/CaseReview";

const STEPS = ["上传", "解析与脱敏", "逐份核对", "抽取", "审核与发布"];
const stepOf = (s: string) => ({ parsing: 1, needs_review: 2, extracting: 3, extracted: 4, failed: 1, cancelled: 0 } as Record<string, number>)[s] ?? 0;
const FIELD_NAME = Object.fromEntries(MAPPING_FIELDS);

export default function ImportPage() {
  const { pid, iid } = useParams<{ pid: string; iid: string }>();
  const { data, error, mutate } = useSWR(`/style/imports/${iid}`, { refreshInterval: (d: any) => (d?.import && ["parsing", "extracting"].includes(d.import.status) ? 2000 : 0) });
  const act = useAction(); const confirm = useConfirm();
  const imp = data?.import;
  if (error) return <div className="space-y-4"><Back pid={pid} /><ErrorBox error={error} onRetry={() => mutate()} /></div>;
  if (!imp) return <div className="space-y-4"><Back pid={pid} /><Skeleton className="h-24 w-full" /><Skeleton className="h-64 w-full" /></div>;
  const [stLabel, stCls] = chip(IMPORT_STATUS, imp.status); const st = imp.stats || {}; const cur = stepOf(imp.status);
  const busy = ["parsing", "extracting"].includes(imp.status);
  const cancel = async () => {
    const r = await confirm({ title: "取消这个导入批次？", body: "未处理的源文件会被立即删除。已生成的候选条目（若有）不受影响。", confirmText: "取消批次", danger: true });
    if (r.ok) act.run("cancel", async () => { await api.post(`/style/imports/${iid}/cancel`); mutate(); }, { ok: "已取消" });
  };
  return (
    <div className="space-y-4">
      <Back pid={pid} />
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="h-page">导入批次{imp.label ? `：${imp.label}` : ""}</h1><span className={stCls}>{stLabel}</span><span className="text-xs text-ink-400">{fmtTime(imp.created_at)}</span>
        {!["extracted", "cancelled"].includes(imp.status) && <button className="btn-ghost btn-sm ml-auto" disabled={act.any} onClick={cancel}>取消批次</button>}
      </div>
      <ol className="flex flex-wrap gap-x-5 gap-y-2 rounded-xl border border-ink-100 bg-white px-4 py-3 text-sm">
        {STEPS.map((t, i) => (
          <li key={t} className={`flex items-center gap-1.5 ${i < cur ? "text-jade-700" : i === cur ? "font-semibold text-brand-700" : "text-ink-400"}`}>
            {i < cur ? <CheckCircle2 size={16} /> : i === cur && busy ? <Loader2 size={16} className="animate-spin" /> : <Circle size={16} />}{i + 1}. {t}</li>))}
      </ol>

      {imp.job && busy && (
        <div className="card card-pad"><div className="flex items-center gap-2 text-sm text-ink-700"><Spinner />{imp.job.message || (imp.status === "parsing" ? "正在解析文件并脱敏…" : "正在抽取…")}</div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-ink-100"><div className="h-full rounded-full bg-brand-500 transition-all" style={{ width: `${Math.max(4, Math.round((imp.job.progress || 0) * 100))}%` }} /></div>
          <p className="mt-2 text-xs text-ink-500">后台运行，可以离开本页，完成后回到这里继续。{imp.status === "extracting" && "使用本机模型时每份病例需要一定时间。"}</p></div>)}
      {imp.error_code && !busy && imp.status !== "extracted" && <Banner kind="err" title={`出错了：${imp.error_code}`}>{imp.error_detail || "请查看下方文件状态。"}</Banner>}

      {imp.sources?.length > 0 && <Sources sources={imp.sources} />}

      {!busy && (st.cases || st.notes) ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          <Stat label="病例" v={st.cases || 0} /><Stat label="经验笔记" v={st.notes || 0} /><Stat label="含处方" v={st.with_prescription || 0} /><Stat label="有警告" v={st.anomalous || 0} tone={st.anomalous ? "warn" : undefined} />
          <Stat label="疑似重复 / 复诊" v={`${(st.duplicates_exact || 0) + (st.duplicates_near || 0)} / ${st.possible_revisits || 0}`} /><Stat label="脱敏风险高" v={st.redaction_high || 0} tone={st.redaction_high ? "err" : undefined} />
        </div>) : null}
      {(st.possible_revisits || 0) > 0 && !busy && <Banner kind="info">有 {st.possible_revisits} 份病例与另一份<b>内容相同但就诊日期不同</b>，已按“同方复诊”作为独立病例计入统计，没有当作重复丢弃。如其实是重复录入，请手动排除。</Banner>}

      {imp.status === "needs_review" && (st.confirmed || 0) > 0 && (st.pending || 0) === 0 && <ExtractCard imp={imp} onStarted={() => mutate()} />}
      {imp.status !== "parsing" && (st.cases || st.notes) ? <CaseReview iid={iid} stats={st} readOnly={imp.status !== "needs_review"} onChanged={() => mutate()} /> : null}
      {imp.status === "needs_review" && !((st.confirmed || 0) > 0 && (st.pending || 0) === 0) && <ExtractCard imp={imp} onStarted={() => mutate()} />}
      {imp.status === "extracted" && <Done pid={pid} imp={imp} />}
    </div>
  );
}

const Back = ({ pid }: { pid: string }) => <Link href={`/style/${pid}#imports`} className="inline-flex items-center gap-1 text-sm text-ink-500 hover:text-brand-700"><ArrowLeft size={14} />返回画像</Link>;

function Sources({ sources }: { sources: any[] }) {
  return (
    <section className="card overflow-hidden"><div className="border-b border-ink-100 px-4 py-3"><h2 className="h-sec flex items-center gap-2"><FileText size={16} className="text-brand-600" />文件解析结果</h2></div>
      <ul className="divide-y divide-ink-50">{sources.map((s) => { const [t, c] = chip(PARSE_STATUS, s.parse_status); const m = s.mapping ? Object.entries<string>(s.mapping) : []; return (
        <li key={s.id} className="px-4 py-2.5 text-sm">
          <div className="flex flex-wrap items-center gap-2"><span className="font-medium text-ink-800">{s.name || "(粘贴文字)"}</span><span className="text-xs text-ink-400">{s.kind}</span><span className={c}>{t}</span>
            {!s.has_file && s.kind !== "paste" && s.kind !== "note" && <span className="text-xs text-ink-400">源文件已清除</span>}</div>
          {s.parse_error && <p className="mt-1 text-xs text-danger-600">{s.parse_error}</p>}
          {m.length > 0 && <p className="mt-1 text-xs text-ink-500">列识别：{m.map(([k, col]) => `${FIELD_NAME[k] || k} ← 「${col}」`).join("；")}{s.unmapped?.length ? `。未识别的列（内容已并入正文）：${s.unmapped.join("、")}` : ""}。识别不对？新建导入时在“高级”里指定列映射。</p>}
        </li>); })}</ul></section>
  );
}

function ExtractCard({ imp, onStarted }: { imp: any; onStarted: () => void }) {
  const act = useAction(); const { data: opt } = useSWR("/style/options");
  const localOk = opt?.local_available ?? true;
  const extOk = !!(opt?.allow_external && opt?.external_configured);
  const [mode, setMode] = useState<"local" | "rules" | "external">("local"); const [ack, setAck] = useState(false);
  const effectiveMode = !localOk && mode === "local" ? (extOk ? "external" : "rules") : mode;
  const st = imp.stats || {}; const confirmed = st.confirmed || 0;
  const start = () => act.run("start", async () => { await api.post(`/style/imports/${imp.id}/extract`, { mode: effectiveMode, external_ack: ack }); onStarted(); }, { ok: "已开始抽取" });
  const opts: [typeof mode, string, string, boolean][] = [
    ["local", "本机模型（推荐）", localOk ? "数据不出本机。速度较慢，病例多时需要较长时间；后台运行，可离开页面。" : "当前部署未连接本机模型，请选择其他抽取方式。", localOk],
    ["rules", "仅规则（最快）", "不调用任何模型，只用关键词与规则提取。能力有限（可能抽不到证型/治法），但完全可追溯。", true],
    ["external", "外部大模型", opt?.allow_external ? (opt?.external_configured ? "速度快，但脱敏后的文本会发送到外部服务。仅限本批次已逐份确认的病例。" : "管理员已开启，但尚未配置外部模型端点，暂不可用。") : "管理员未开启该功能。", extOk],
  ];
  return (
    <section className="card card-pad space-y-3">
      <h2 className="h-sec flex items-center gap-2"><Cpu size={16} className="text-brand-600" />开始抽取与归纳</h2>
      <p className="text-sm text-ink-600">只有<b>已确认的 {confirmed} 份</b>病例会进入抽取。系统会提取证型、治法、方名、加减等，<b>饮片和克数始终由规则从原文解析</b>，模型不参与；模型给出的每个字段都必须能在原文里逐字找到，否则丢弃。</p>
      <div className="grid gap-2 lg:grid-cols-3">
        {opts.map(([k, t, h, enabled]) => (
          <label key={k} className={`flex items-start gap-2 rounded-xl border p-3 text-sm ${effectiveMode === k ? "border-brand-300 bg-brand-50/60" : "border-ink-100"} ${enabled ? "cursor-pointer" : "cursor-not-allowed opacity-50"}`}>
            <input type="radio" className="mt-1 accent-brand-600" disabled={!enabled} checked={effectiveMode === k} onChange={() => setMode(k)} /><span><span className="font-medium text-ink-800">{t}</span><span className="block text-xs text-ink-500">{h}</span></span></label>))}
      </div>
      {effectiveMode === "external" && (
        <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-3 text-sm text-amber-900">
          <p className="flex gap-1.5"><ShieldAlert size={16} className="mt-0.5 shrink-0" />规则脱敏<b>不能保证</b>万无一失（例如叙述中没有提示词的姓名、罕见病 + 年龄 + 地区的组合）。{st.redaction_high ? `有 ${st.redaction_high} 份脱敏风险为“高”的病例不会被外发，会改用规则抽取。` : ""}</p>
          <label className="mt-2 flex items-start gap-2"><input type="checkbox" className="mt-0.5 h-4 w-4 accent-brand-600" checked={ack} onChange={(e) => setAck(e.target.checked)} /><span>我已逐份核对了脱敏后的文本，同意将这些文本发送给外部模型；并确认已有权这样处理这些资料。</span></label>
        </div>)}
      <div className="flex items-center gap-3">
        <button className="btn-primary" disabled={act.any || confirmed === 0 || (effectiveMode === "external" && !ack)} onClick={start}>{act.is("start") ? <Spinner size={14} /> : <ArrowRight size={15} />}开始抽取</button>
        {confirmed === 0 && <span className="text-xs text-amber-700">请先在上方确认至少一份病例</span>}
        {(st.pending || 0) > 0 && confirmed > 0 && <span className="text-xs text-ink-500">还有 {st.pending} 份未确认，它们不会参与本次抽取。</span>}
      </div>
    </section>
  );
}

function Done({ pid, imp }: { pid: string; imp: any }) {
  const s = imp.stats || {};
  return (
    <section className="card card-pad space-y-3 border-jade-100">
      <h2 className="h-sec flex items-center gap-2 text-jade-700"><CheckCircle2 size={17} />抽取完成</h2>
      <p className="text-sm text-ink-700">新增候选经验 <b>{s.new ?? 0}</b> 条{(s.updates ?? 0) > 0 && <>，其中 <b>{s.updates}</b> 条是对已审核经验的更新建议</>}{(s.refreshed ?? 0) > 0 && <>，刷新了 {s.refreshed} 条尚未审核的候选</>}{(s.unchanged ?? 0) > 0 && <>，{s.unchanged} 条无变化</>}。<b>候选条目不会自动生效</b>，必须经医师逐条审核、发布后才会被使用；已发布的版本不会被改动。</p>
      {s.below_threshold?.length > 0 && <p className="text-xs text-ink-500">以下病证病例数不足 3 例，不足以归纳可靠的经验，已跳过：{s.below_threshold.map((b: any) => `${b.group}（${b.n} 例）`).join("、")}。</p>}
      {imp.sources_purged && <p className="text-xs text-ink-500">原始上传文件已从服务器删除。</p>}
      <Link href={`/style/${pid}#review`} className="btn-primary inline-flex">去审核候选经验<ArrowRight size={15} /></Link>
    </section>
  );
}
