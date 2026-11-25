"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { BookOpen, ChevronRight, Plus, ShieldCheck, UserRound } from "lucide-react";
import { Banner, Empty, ErrorBox, SkeletonList } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { ProfileCreate } from "@/components/style/ProfileCreate";
import { Disclaimer } from "@/components/style/shared";
import { AUTH_STATUS } from "@/lib/style";

function ProfileCard({ p }: { p: any }) {
  const av = p.active_version; const a = p.authorization;
  return (
    <Link href={`/style/${p.id}`} className="card card-pad group block transition hover:border-brand-300">
      <div className="flex items-start gap-3">
        <div className={`grid h-10 w-10 shrink-0 place-items-center rounded-full text-sm font-semibold ${p.kind === "master" ? "bg-brand-50 text-brand-700" : "bg-jade-50 text-jade-700"}`}>{p.subject_name.slice(0, 1)}</div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5"><span className="font-semibold text-ink-900">{p.subject_name}</span>{p.subject_title && <span className="text-xs text-ink-500">{p.subject_title}</span>}</div>
          <div className="mt-0.5 text-xs text-ink-500">{[p.subject_dept, p.kind === "personal" ? "个人诊疗习惯" : "名医经验库"].filter(Boolean).join(" · ")}</div>
          {p.intro && <p className="mt-2 line-clamp-2 text-[13px] leading-relaxed text-ink-600">{p.intro}</p>}
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {p.status === "disabled" ? <span className="tag-neutral">已停用</span> : av ? <span className="tag-ok">已发布 v{av.version_no} · {av.stats?.cases ?? 0} 例</span> : <span className="tag-warn">尚未发布</span>}
            {p.kind === "master" && (a ? <span className="tag-brand"><ShieldCheck size={11} />已授权共享</span> : <span className="tag-neutral">未共享</span>)}
            {p.proxy_created && <span className="tag-neutral">管理员代建</span>}
            {p.usable && <span className="tag-ok">可在接诊时选用</span>}
            {p.draft_version && p.can_manage && <span className="tag-warn">有草稿 v{p.draft_version.version_no}</span>}
          </div>
        </div>
        <ChevronRight size={16} className="mt-1 text-ink-300 transition group-hover:text-brand-500" />
      </div>
    </Link>
  );
}

export default function StyleHome() {
  const { user, can } = useAuth(); const router = useRouter();
  const { data, error, isLoading, mutate } = useSWR("/style/profiles");
  const [open, setOpen] = useState(false);
  const list: any[] = data?.profiles || [];
  const masters = list.filter((p) => p.kind === "master");
  const mine = list.filter((p) => p.kind === "personal" && p.owner_user_id === user?.id);
  const others = list.filter((p) => p.kind === "personal" && p.owner_user_id !== user?.id);   // 管理员能看到别人个人画像的元数据（不含内容）
  const isAdmin = user?.role === "ADMIN";
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h1 className="h-page">医师经验画像</h1><p className="mt-1 max-w-3xl text-sm text-ink-500">把资深医师的历史诊疗经验沉淀为<b>结构化、可追溯、可审核</b>的画像。年轻医师接诊时可选用一份参考，AI 在不越过患者当前事实与安全规则的前提下，分栏展示“经验怎么说 / 与本患者是否吻合 / 方案与经验的异同”。</p></div>
        {can("style.write") && <button className="btn-primary" onClick={() => setOpen(true)}><Plus size={16} />新建画像</button>}
      </div>
      <Disclaimer text={data?.disclaimer} />
      {error ? <ErrorBox error={error} onRetry={() => mutate()} /> : isLoading ? <SkeletonList rows={3} /> : (<>
        <section className="space-y-3">
          <h2 className="h-sec flex items-center gap-2"><BookOpen size={16} className="text-brand-600" />名医 / 资深医师经验库</h2>
          {masters.length === 0 ? <div className="card"><Empty icon={<BookOpen size={22} />} title="还没有可用的经验库" hint={isAdmin ? "点“新建画像”为名老中医代建经验库并导入历史资料。" : "您可以新建自己的经验库；其他医师的经验库需经授权备案后才会出现在这里。"} /></div> :
            <div className="grid gap-3 lg:grid-cols-2">{masters.map((p) => <ProfileCard key={p.id} p={p} />)}</div>}
        </section>
        {!isAdmin && (
          <section className="space-y-3">
            <h2 className="h-sec flex items-center gap-2"><UserRound size={16} className="text-jade-600" />我的个人诊疗习惯</h2>
            {mine.length === 0 ? <div className="card"><Empty icon={<UserRound size={22} />} title="还没有个人画像" hint="导入您自己的历史病历/处方，让 AI 生成方案时贴近您的习惯。只有您自己可见。" /></div> :
              <div className="grid gap-3 lg:grid-cols-2">{mine.map((p) => <ProfileCard key={p.id} p={p} />)}</div>}
          </section>)}
        {isAdmin && others.length > 0 && (
          <section className="space-y-3"><h2 className="h-sec">医师个人画像（仅元数据）</h2>
            <Banner kind="info">个人画像的内容只有其所有者可见，管理员只能看到存在与状态。</Banner>
            <div className="grid gap-3 lg:grid-cols-2">{others.map((p) => <div key={p.id} className="card card-pad text-sm"><b>{p.subject_name}</b><span className="ml-2 text-xs text-ink-500">个人画像 · {p.active_version ? `已发布 v${p.active_version.version_no}` : "未发布"}</span></div>)}</div></section>)}
      </>)}
      <ProfileCreate open={open} onClose={() => setOpen(false)} hasPersonal={mine.length > 0} onDone={(id) => { setOpen(false); mutate(); router.push(`/style/${id}`); }} />
    </div>
  );
}
