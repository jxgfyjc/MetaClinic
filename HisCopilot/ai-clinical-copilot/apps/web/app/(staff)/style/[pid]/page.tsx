"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import { ArrowLeft, CheckCircle2, Circle, Pencil, Save, X } from "lucide-react";
import { Banner, ErrorBox, Field, Skeleton, Tabs, useConfirm } from "@/components/ui";
import { api, fmtTime } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { IMPORT_STATUS, chip } from "@/lib/style";
import { Disclaimer, Stat } from "@/components/style/shared";
import { ImportNew } from "@/components/style/ImportNew";
import { EntriesReview } from "@/components/style/EntriesReview";
import { Versions } from "@/components/style/Versions";
import { Sharing } from "@/components/style/Sharing";
import { Library } from "@/components/style/Library";

type Tab = "overview" | "imports" | "review" | "versions" | "sharing";

export default function ProfilePage() {
  const { pid } = useParams<{ pid: string }>();
  const { data, error, mutate } = useSWR(`/style/profiles/${pid}`);
  const p = data?.profile;
  const [tab, setTab] = useState<Tab>("overview");
  useEffect(() => { const h = location.hash.replace("#", ""); if (["overview", "imports", "review", "versions", "sharing"].includes(h)) setTab(h as Tab); }, []);
  const go = (t: Tab) => { setTab(t); history.replaceState(null, "", `#${t}`); };
  const pend = useSWR(p?.can_manage ? `/style/profiles/${pid}/entries?version=draft` : null);
  const pending = pend.data?.review_counts?.pending || 0;

  if (error) return <div className="space-y-4"><BackLink /><ErrorBox error={error} onRetry={() => mutate()} /></div>;
  if (!p) return <div className="space-y-4"><BackLink /><Skeleton className="h-28 w-full" /><Skeleton className="h-64 w-full" /></div>;
  return (
    <div className="space-y-4">
      <BackLink />
      <Header p={p} auth={data.authorizations} onChanged={() => mutate()} />
      {p.status === "disabled" && <Banner kind="warn" title="该画像已停用">停用原因：{p.disabled_reason || "—"}。停用后不能被新的接诊选用，已生成的方案仍保留当时的引用快照。</Banner>}
      {!p.can_manage ? <Library p={p} disclaimer={data.disclaimer} /> : (<>
        <Tabs<Tab> value={tab} onChange={go} items={[
          { key: "overview", label: "概览" }, { key: "imports", label: "导入资料" }, { key: "review", label: "审核经验条目", count: pending }, { key: "versions", label: "版本" },
          ...(p.kind === "master" ? [{ key: "sharing" as Tab, label: "共享与授权" }] : [])]} />
        {tab === "overview" && <Overview p={p} pending={pending} onChanged={() => mutate()} go={go} />}
        {tab === "imports" && <ImportsTab p={p} />}
        {tab === "review" && <EntriesReview p={p} onChanged={() => { mutate(); pend.mutate(); }} />}
        {tab === "versions" && <Versions p={p} onChanged={() => { mutate(); pend.mutate(); }} />}
        {tab === "sharing" && p.kind === "master" && <Sharing p={p} auths={data.authorizations} onChanged={() => mutate()} />}
      </>)}
    </div>
  );
}

const BackLink = () => <Link href="/style" className="inline-flex items-center gap-1 text-sm text-ink-500 hover:text-brand-700"><ArrowLeft size={14} />经验画像</Link>;

function Header({ p, auth, onChanged }: { p: any; auth: any[]; onChanged: () => void }) {
  const cur = p.authorization;
  return (
    <div className="card card-pad flex flex-wrap items-start gap-4">
      <div className={`grid h-12 w-12 place-items-center rounded-full text-lg font-semibold ${p.kind === "master" ? "bg-brand-50 text-brand-700" : "bg-jade-50 text-jade-700"}`}>{p.subject_name.slice(0, 1)}</div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2"><h1 className="h-page">{p.subject_name}</h1>{p.subject_title && <span className="text-sm text-ink-500">{p.subject_title}</span>}
          <span className={p.kind === "master" ? "tag-brand" : "tag-ok"}>{p.kind === "master" ? "名医经验库" : "个人诊疗习惯"}</span>
          {p.proxy_created && <span className="tag-neutral">管理员代建</span>}</div>
        <div className="mt-1 text-sm text-ink-500">{[p.subject_dept, p.active_version ? `已发布 v${p.active_version.version_no}（${fmtTime(p.active_version.activated_at)}）` : "尚未发布"].filter(Boolean).join(" · ")}</div>
        {p.intro && <p className="mt-2 max-w-3xl whitespace-pre-wrap text-[13px] leading-relaxed text-ink-600">{p.intro}</p>}
        {p.kind === "master" && cur && <p className="mt-2 text-xs text-ink-500">共享授权：<b>{cur.scope === "dept" ? `科室「${cur.scope_dept}」` : "全院"}</b> · {cur.basis_text}（授权人：{cur.authorizer_name}）{cur.valid_until ? ` · 有效期至 ${cur.valid_until.slice(0, 10)}` : ""}</p>}
      </div>
    </div>
  );
}

function Overview({ p, pending, onChanged, go }: { p: any; pending: number; onChanged: () => void; go: (t: Tab) => void }) {
  const act = useAction(); const confirm = useConfirm();
  const [edit, setEdit] = useState(false); const [intro, setIntro] = useState(p.intro); const [title, setTitle] = useState(p.subject_title); const [dept, setDept] = useState(p.subject_dept);
  const cc = p.case_counts || {}; const total = Object.values<number>(cc).reduce((a, b) => a + b, 0);
  const published = !!p.active_version; const hasCases = (cc.confirmed || 0) > 0;
  const steps: [string, boolean, string][] = [
    ["导入历史病历 / 处方 / 经验笔记", total > 0, "imports"], ["逐份核对脱敏后的病例并确认", hasCases, "imports"], ["审核系统归纳的候选经验条目", published || (hasCases && pending === 0 && !!p.draft_version?.stats?.entries), "review"],
    ["发布版本（发布后不可变；新资料只会生成新的候选）", published, "review"], ...(p.kind === "master" ? [["声明共享授权，由管理员备案后其他医师才可见", !!p.authorization, "sharing"] as [string, boolean, string]] : []),
  ];
  const save = () => act.run("save", async () => { await api.patch(`/style/profiles/${p.id}`, { intro, subject_title: title, subject_dept: dept, row_version: p.row_version }); setEdit(false); onChanged(); }, { ok: "已保存", onStale: onChanged });
  const toggle = async () => {
    if (p.status === "active") {
      const r = await confirm({ title: "停用该画像？", body: "停用后不能被新的接诊选用；已生成的方案保留当时的引用快照，不受影响。可随时重新启用。", confirmText: "停用", danger: true, reason: { label: "停用原因", min: 4 } });
      if (r.ok) act.run("dis", async () => { await api.post(`/style/profiles/${p.id}/disable`, { reason: r.reason }); onChanged(); }, { ok: "已停用" });
    } else act.run("en", async () => { await api.post(`/style/profiles/${p.id}/enable`); onChanged(); }, { ok: "已重新启用" });
  };
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="已确认病例" v={cc.confirmed || 0} tone={hasCases ? "ok" : undefined} /><Stat label="待确认病例" v={cc.pending_review || 0} tone={cc.pending_review ? "warn" : undefined} />
        <Stat label="待审核条目" v={pending} tone={pending ? "warn" : undefined} /><Stat label="当前版本" v={p.active_version ? `v${p.active_version.version_no}` : "—"} />
      </div>
      <section className="card card-pad"><h2 className="h-sec mb-3">建立画像的步骤</h2>
        <ol className="space-y-2">{steps.map(([t, done, tab], i) => (
          <li key={i} className="flex items-center gap-2.5 text-sm">{done ? <CheckCircle2 size={17} className="text-jade-600" /> : <Circle size={17} className="text-ink-300" />}<span className={done ? "text-ink-700" : "text-ink-900"}>{t}</span>
            {!done && <button className="ml-auto text-xs text-brand-700 hover:underline" onClick={() => go(tab as Tab)}>去处理 →</button>}</li>))}</ol></section>
      <section className="card card-pad">
        <div className="mb-3 flex items-center"><h2 className="h-sec">基本信息</h2>
          <div className="ml-auto flex gap-2">{!edit ? <button className="btn-secondary btn-sm" onClick={() => setEdit(true)}><Pencil size={13} />编辑</button> : <><button className="btn-ghost btn-sm" onClick={() => setEdit(false)}><X size={13} />取消</button><button className="btn-primary btn-sm" disabled={act.any} onClick={save}><Save size={13} />保存</button></>}</div></div>
        {edit ? (<div className="grid gap-3 sm:grid-cols-2"><Field label="职称"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></Field><Field label="科室 / 专长"><input className="input" value={dept} onChange={(e) => setDept(e.target.value)} /></Field>
          <div className="sm:col-span-2"><Field label="简介"><textarea className="input" rows={4} value={intro} maxLength={2000} onChange={(e) => setIntro(e.target.value)} /></Field></div></div>) :
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2"><div><dt className="text-xs text-ink-400">姓名</dt><dd>{p.subject_name}</dd></div><div><dt className="text-xs text-ink-400">职称</dt><dd>{p.subject_title || "—"}</dd></div><div><dt className="text-xs text-ink-400">科室 / 专长</dt><dd>{p.subject_dept || "—"}</dd></div>
            <div><dt className="text-xs text-ink-400">临床审核</dt><dd>{p.can_review ? "您是该画像的临床审核人" : p.proxy_created ? "由指定的医师审核发布（管理员不能审核临床内容）" : "所有者医师"}</dd></div></dl>}
        {p.can_review || p.kind === "personal" ? <div className="mt-4 border-t border-ink-50 pt-3"><button className={p.status === "active" ? "btn-danger-ghost btn-sm" : "btn-secondary btn-sm"} disabled={act.any} onClick={toggle}>{p.status === "active" ? "停用该画像" : "重新启用"}</button></div> : null}
      </section>
      <Disclaimer />
    </div>
  );
}

function ImportsTab({ p }: { p: any }) {
  const router = useRouter();
  const { data, error, mutate } = useSWR(`/style/profiles/${p.id}/imports`, { refreshInterval: 5000 });
  const [open, setOpen] = useState(!data);
  if (p.status !== "active") return <Banner kind="warn">该画像已停用，不能再导入资料。</Banner>;
  const list: any[] = data?.imports || [];
  return (
    <div className="space-y-4">
      <section className="card card-pad"><h2 className="h-sec mb-3">新建导入批次</h2><ImportNew pid={p.id} onCreated={(iid) => { mutate(); router.push(`/style/${p.id}/imports/${iid}`); }} /></section>
      <section className="card overflow-hidden"><div className="border-b border-ink-100 px-4 py-3"><h2 className="h-sec">导入历史</h2></div>
        {error ? <div className="p-4"><ErrorBox error={error} onRetry={() => mutate()} /></div> : !data ? <Skeleton className="m-4 h-24" /> : list.length === 0 ? <div className="p-6 text-center text-sm text-ink-400">还没有导入过资料</div> : (
          <div className="overflow-x-auto"><table className="tbl"><thead><tr><th>批次</th><th>状态</th><th>病例</th><th>已确认</th><th>创建时间</th><th /></tr></thead><tbody>
            {list.map((i) => { const [t, c] = chip(IMPORT_STATUS, i.status); return (
              <tr key={i.id} className="cursor-pointer" onClick={() => router.push(`/style/${p.id}/imports/${i.id}`)}>
                <td>{i.label || <span className="text-ink-400">（无备注）</span>}</td><td><span className={c}>{t}</span></td><td>{(i.stats.cases || 0) + (i.stats.notes || 0)}</td><td>{i.stats.confirmed || 0}</td><td className="text-ink-500">{fmtTime(i.created_at)}</td><td className="text-xs text-brand-700">查看 →</td></tr>); })}
          </tbody></table></div>)}</section>
    </div>
  );
}
