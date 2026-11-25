"use client";
// 经验画像 × 治疗方案：接诊时选用参考（StylePicker）+ 方案里的四栏对照（StyleColumns）。
// ① 名医经验参考（画像原文，来自已发布版本）② 当前患者分析（模型对“是否吻合”的判断，引用患者事实）
// ③ 个性化建议与异同（方案与经验的关系）④ 医师最终决策（医师逐条采纳/不采纳——系统不替医师决定）
import { useRef, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { BookOpen, Check, ShieldCheck, X } from "lucide-react";
import { Banner, Skeleton, useToast } from "@/components/ui";
import { api, errMsg } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { CONF_LABEL, STANCE_STYLE, VERDICT_STYLE } from "@/lib/style";
import { CatTag, Disclaimer, Support } from "@/components/style/shared";
import { SRC_LABEL } from "./bits";
import { useWs } from "./ctx";

const LAYER: Record<string, [string, string]> = { master: ["名医经验库", "tag-brand"], personal: ["个人习惯", "tag-ok"] };

/** 接诊时选择参考哪些画像（按接诊选择，不做账号级绑定）。选择立即保存；对“下一次生成方案”生效。 */
export function StylePicker({ eid, disabled }: { eid: string; disabled?: boolean }) {
  const { data, error, mutate } = useSWR(`/style/encounters/${eid}/profiles`);
  const toast = useToast();
  // 选择是“乐观”的：点击立即反映在界面上，后台只提交“最终想要的集合”。连点多个画像不会互相覆盖或被静默丢弃。
  const [want, setWant] = useState<string[] | null>(null);
  const inflight = useRef(false); const latest = useRef<string[] | null>(null);
  if (error) return null;                                   // 没有 style 权限的角色不显示，且不影响原有流程
  if (!data) return <Skeleton className="h-16 w-full" />;
  const avail: any[] = data.available || []; const saved: string[] = data.chosen || [];
  const chosen = want ?? saved;
  if (avail.length === 0) return (
    <div className="rounded-xl border border-dashed border-ink-200 px-3.5 py-3 text-xs text-ink-500"><BookOpen size={14} className="mr-1 inline text-ink-400" />可选的经验参考：暂无。有已发布的个人画像，或经授权共享的名医经验库后，会出现在这里。<Link href="/style" className="ml-1 text-brand-700 hover:underline">管理经验画像</Link></div>);

  const flush = async () => {
    if (inflight.current) return;                           // 已有一个提交在飞：它结束后会再提交最新的集合
    inflight.current = true;
    try {
      while (latest.current) {
        const target = latest.current; latest.current = null;
        try { await api.put(`/style/encounters/${eid}/profiles`, { profile_ids: target }); }
        catch (e) { latest.current = null; setWant(null); toast("err", errMsg(e)); await mutate(); return; }
      }
      await mutate(); setWant(null);
    } finally { inflight.current = false; }
  };
  const toggle = (id: string) => {
    const next = chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id];
    if (next.length > 5) { toast("warn", "每次接诊最多参考 5 份画像"); return; }
    setWant(next); latest.current = next; if (next.length > chosen.length) toast("info", "已选用。重新生成方案时，AI 会参考所选经验。");
    flush();
  };
  return (
    <div className="rounded-xl border border-ink-100 p-3.5">
      <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-ink-800"><BookOpen size={15} className="text-brand-600" />参考经验画像（可选）<span className="font-normal text-ink-400">最多 5 份</span></div>
      <div className="grid gap-2 sm:grid-cols-2">
        {avail.map((p) => {
          const on = chosen.includes(p.id); const [lt, lc] = LAYER[p.kind] || ["", ""]; return (
            <label key={p.id} className={`flex items-start gap-2 rounded-lg border p-2.5 text-sm transition ${on ? "border-brand-300 bg-brand-50/60" : "border-ink-100 hover:bg-ink-50"} ${disabled ? "opacity-60" : "cursor-pointer"}`}>
              <input type="checkbox" className="mt-1 h-4 w-4 accent-brand-600" checked={on} disabled={disabled} onChange={() => toggle(p.id)} />
              <span className="min-w-0"><span className="flex flex-wrap items-center gap-1.5"><b className="text-ink-900">{p.subject_name}</b><span className={lc}>{lt}</span>{p.authorization && <span className="tag-neutral" title={p.authorization.basis_text}><ShieldCheck size={10} />已授权</span>}</span>
                <span className="block text-xs text-ink-500">{[p.subject_title, p.subject_dept, p.active_version ? `v${p.active_version.version_no}` : ""].filter(Boolean).join(" · ")}</span></span></label>);
        })}
      </div>
      <p className="mt-2 text-[11.5px] leading-snug text-ink-500">历史经验仅作参考：<b>患者当前事实与安全规则始终优先</b>，AI 不会照抄历史处方；选用后，生成的方案里会分栏显示“经验怎么说 / 是否吻合 / 与本方案异同”，由您决定是否采纳。</p>
    </div>
  );
}

/** 方案里的四栏对照。数据来自 plan.style（生成时保存的快照，不随画像后续变化）。 */
export function StyleColumns({ plan, editable }: { plan: any; editable: boolean }) {
  const ws = useWs(); const act = useAction();
  const st = plan.style;
  const [openFact, setOpenFact] = useState<string | null>(null);
  if (!st || !st.requested?.length) return null;
  const profiles: any[] = st.profiles || []; const cmp: any[] = st.comparison || []; const app: any[] = st.applicability || []; const dec = st.decisions || {};
  const facts: Record<string, any> = st.facts || {};
  const decided = Object.keys(dec).length;
  const decide = (i: number, d: "adopt" | "skip" | "clear") => act.run("d" + i, async () => { await api.post(`/style/plans/${plan.id}/decision`, { index: i, decision: d, row_version: plan.row_version }); await ws.reload(); }, { onStale: ws.reload });
  const refEntry = (id: string) => profiles.flatMap((p) => p.entries).find((e: any) => e.ref_id === id);

  if (!profiles.length) return <Banner kind="warn" title="本方案未使用经验参考">{st.note || "所选画像当前没有可供 AI 参考的内容。"}</Banner>;
  return (
    <section className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-ink-100 px-4 py-3">
        <BookOpen size={16} className="text-brand-600" /><h2 className="h-sec">经验参考对照</h2>
        <span className="text-xs text-ink-500">参考：{profiles.map((p) => p.name).join("、")}</span>
        {cmp.length > 0 && <span className={`ml-auto text-xs ${decided === cmp.length ? "text-jade-700" : "text-amber-700"}`}>您已处理 {decided}/{cmp.length} 条</span>}
      </div>
      <div className="border-b border-ink-100 bg-ink-50/60 px-4 py-2"><Disclaimer compact text={st.disclaimer} /></div>
      {st.summary && <p className="border-b border-ink-100 px-4 py-2.5 text-[13px] leading-relaxed text-ink-700"><b className="text-ink-900">AI 小结：</b>{st.summary}</p>}
      {(st.validation?.dropped_s_refs > 0 || st.validation?.dropped_f_refs > 0 || st.validation?.unverified_claims > 0) && (
        <div className="border-b border-ink-100 px-4 py-2 text-xs text-amber-700">校验：已移除 {(st.validation.dropped_s_refs || 0) + (st.validation.dropped_f_refs || 0)} 条无法核实的引用{st.validation.unverified_claims ? `；${st.validation.unverified_claims} 条对照里的药物/剂量转述与画像记录不符，已标注“未核实”` : ""}。</div>)}

      <div className="grid gap-px bg-ink-100 lg:grid-cols-4">
        {/* ① */}
        <Col n="①" title="名医经验参考" sub="画像原文（已审核发布的版本）">
          {profiles.map((p) => (
            <div key={p.profile_id} className="space-y-2">
              <div className="flex flex-wrap items-center gap-1.5 text-xs"><span className={LAYER[p.layer]?.[1] || "tag-neutral"}>{LAYER[p.layer]?.[0]}</span><b className="text-ink-800">{p.name}</b><span className="text-ink-400">v{p.version_no}</span></div>
              <p className="text-[11px] leading-snug text-ink-400">{p.source_note}</p>
              <ul className="space-y-2">{p.entries.map((e: any) => (
                <li key={e.ref_id} className="rounded-lg border border-ink-100 p-2.5">
                  <div className="flex flex-wrap items-center gap-1.5"><span className="kbd">{e.ref_id}</span><CatTag c={e.category} />{e.group_key && <span className="text-[11px] text-ink-500">{e.group_key}</span>}</div>
                  <div className="mt-1 text-[13px] font-medium text-ink-900">{e.title}</div>
                  <p className="mt-0.5 line-clamp-3 text-xs leading-relaxed text-ink-600" title={e.summary}>{e.summary}</p>
                  {(e.conditions || e.exceptions) && <p className="mt-1 text-[11.5px] text-ink-500">{e.conditions && <>适用：{e.conditions}　</>}{e.exceptions && <>例外：{e.exceptions}</>}</p>}
                  <div className="mt-1.5"><Support n={e.support} of={e.total} conf={e.confidence} /></div>
                </li>))}</ul>
            </div>))}
        </Col>
        {/* ② */}
        <Col n="②" title="当前患者分析" sub="经验的适用条件与本患者当前事实是否吻合（AI 判断，需您核对）">
          {app.length === 0 ? <p className="text-xs text-ink-400">模型没有给出适用性判断。</p> : (
            <ul className="space-y-2">{app.map((a, i) => (
              <li key={i} className="rounded-lg border border-ink-100 p-2.5">
                <div className="flex flex-wrap items-center gap-1.5">{a.refs.map((r: string) => <span key={r} className="kbd" title={refEntry(r)?.title}>{r}</span>)}<span className={VERDICT_STYLE[a.verdict] || "tag-neutral"}>{a.verdict_label}</span></div>
                <p className="mt-1 text-[13px] leading-relaxed text-ink-800">{a.reason || "—"}</p>
                {a.fact_refs.length > 0 ? (<div className="mt-1.5 flex flex-wrap gap-1">{a.fact_refs.map((f: string) => <button type="button" key={f} className={`kbd cursor-pointer hover:border-brand-300 hover:text-brand-700 ${openFact === f + i ? "!border-brand-400 !text-brand-700" : ""}`} onClick={() => setOpenFact(openFact === f + i ? null : f + i)}>{f}</button>)}</div>) :
                  <span className="mt-1.5 inline-block tag-warn" title="模型未给出可核实的患者事实引用">无可核实的患者事实引用</span>}
                {a.fact_refs.map((f: string) => openFact === f + i && facts[f] && <div key={f} className="mt-1.5 rounded-md bg-ink-50 p-2 text-xs"><span className="kbd mr-1">{f}</span><b>{facts[f].label}</b>：{facts[f].text}<span className="ml-1 text-ink-400">［{SRC_LABEL[facts[f].source] || facts[f].source}］</span>{facts[f].reliability && <span className="ml-1 text-amber-600">{facts[f].reliability}</span>}</div>)}
              </li>))}</ul>)}
        </Col>
        {/* ③ */}
        <Col n="③" title="个性化建议与异同" sub="本方案与上述经验的关系（方案本身见下方各卡片，可直接修改）">
          {cmp.length === 0 ? <p className="text-xs text-ink-400">模型没有给出异同说明。</p> : (
            <ul className="space-y-2">{cmp.map((c, i) => (
              <li key={i} className="rounded-lg border border-ink-100 p-2.5">
                <div className="flex flex-wrap items-center gap-1.5"><span className="grid h-5 w-5 place-items-center rounded-full bg-ink-800 text-[11px] font-semibold text-white">{i + 1}</span>{c.refs.map((r: string) => <span key={r} className="kbd" title={refEntry(r)?.title}>{r}</span>)}<span className={STANCE_STYLE[c.stance] || "tag-neutral"}>{c.stance_label}</span>
                  {c.verified === false && <span className="tag-warn" title={`与画像记录不符：${(c.unverified || []).join("、")}`}>药物/剂量未核实</span>}</div>
                {c.item && <div className="mt-1 text-[11.5px] text-ink-500">{c.item}</div>}
                <p className="mt-0.5 text-[13px] leading-relaxed text-ink-800">{c.note || "—"}</p>
                {c.verified === false && <p className="mt-1 text-[11.5px] text-amber-700">此处转述的药物/剂量在所引用的画像条目中找不到依据（{(c.unverified || []).join("、")}），请以画像原文（①栏）为准。</p>}
              </li>))}</ul>)}
        </Col>
        {/* ④ */}
        <Col n="④" title="医师最终决策" sub="由您逐条取舍；系统不替您决定，也不会因此改动方案或安全检查">
          {cmp.length === 0 ? <p className="text-xs text-ink-400">没有需要取舍的对照条目。</p> : (
            <ul className="space-y-2">{cmp.map((c, i) => {
              const d = dec[String(i)]; return (
                <li key={i} className="rounded-lg border border-ink-100 p-2.5">
                  <div className="flex items-center gap-1.5 text-xs"><span className="grid h-5 w-5 place-items-center rounded-full bg-ink-800 text-[11px] font-semibold text-white">{i + 1}</span><span className="min-w-0 flex-1 truncate text-ink-600">{c.item || c.note}</span>
                    {d && <span className={d.decision === "adopt" ? "tag-ok" : "tag-neutral"}>{d.decision === "adopt" ? "已采纳" : "不采纳"}</span>}</div>
                  {editable && plan.status === "draft" ? (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <button className={d?.decision === "adopt" ? "btn-success btn-sm" : "btn-secondary btn-sm"} disabled={act.any} onClick={() => decide(i, d?.decision === "adopt" ? "clear" : "adopt")}><Check size={12} />采纳</button>
                      <button className={d?.decision === "skip" ? "btn-primary btn-sm" : "btn-secondary btn-sm"} disabled={act.any} onClick={() => decide(i, d?.decision === "skip" ? "clear" : "skip")}><X size={12} />不采纳</button></div>) :
                    !d && <p className="mt-1.5 text-[11.5px] text-ink-400">{plan.status === "confirmed" ? "方案已确认；如需补记取舍，请先修改方案使其回到草稿。" : "—"}</p>}
                </li>);
            })}</ul>)}
          <p className="mt-3 text-[11.5px] leading-snug text-ink-500">采纳 / 不采纳只是记录您对“经验参考”的取舍，方案正文请在下方直接修改；药物仍须通过处方环节的安全检查与您的签署。</p>
        </Col>
      </div>
    </section>
  );
}

function Col({ n, title, sub, children }: { n: string; title: string; sub: string; children: React.ReactNode }) {
  return (
    <div className="bg-white p-3.5">
      <div className="mb-2.5"><div className="flex items-center gap-1.5 text-[13px] font-semibold text-ink-900"><span className="text-brand-600">{n}</span>{title}</div><div className="text-[11.5px] leading-snug text-ink-400">{sub}</div></div>
      <div className="space-y-3">{children}</div>
    </div>
  );
}
