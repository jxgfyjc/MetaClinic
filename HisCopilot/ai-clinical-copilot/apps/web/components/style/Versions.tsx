"use client";
import { useState } from "react";
import useSWR from "swr";
import { ChevronDown, ChevronRight, History, Trash2, Undo2 } from "lucide-react";
import { Banner, ErrorBox, Skeleton, useConfirm } from "@/components/ui";
import { api, fmtTime } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { CatTag } from "@/components/style/shared";
import { VERSION_STATUS, chip } from "@/lib/style";

function VersionEntries({ pid, vid }: { pid: string; vid: string }) {
  const { data, error } = useSWR(`/style/profiles/${pid}/entries?version=${vid}`);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Skeleton className="h-16 w-full" />;
  const es: any[] = data.entries;
  if (!es.length) return <p className="text-xs text-ink-400">这个版本没有已通过的条目。</p>;
  return <ul className="space-y-1.5">{es.map((e) => <li key={e.id} className="flex gap-2 text-[13px]"><CatTag c={e.category} /><span><b className="text-ink-800">{e.title}</b><span className="ml-2 text-ink-500">{e.summary.slice(0, 90)}{e.summary.length > 90 ? "…" : ""}</span></span></li>)}</ul>;
}

export function Versions({ p, onChanged }: { p: any; onChanged: () => void }) {
  const { data, error, mutate } = useSWR(`/style/profiles/${p.id}/versions`);
  const act = useAction(); const confirm = useConfirm(); const [open, setOpen] = useState<string | null>(null);
  const refresh = () => { mutate(); onChanged(); };
  const rollback = async (v: any) => {
    const r = await confirm({ title: `回退到 v${v.version_no}？`, body: <>不会改写历史：系统会用 v{v.version_no} 的已通过条目生成一个<b>新的草稿版本</b>，您审核并发布后才会生效。当前使用中的版本在此之前保持不变。</>, confirmText: "生成回退草稿", reason: { label: "回退原因", min: 4 } });
    if (r.ok) act.run("rb", async () => { await api.post(`/style/profiles/${p.id}/versions/${v.id}/rollback`, { reason: r.reason }); refresh(); }, { ok: "已生成回退草稿，请到“审核经验条目”确认并发布" });
  };
  const discard = async (v: any) => {
    const r = await confirm({ title: `丢弃草稿 v${v.version_no}？`, body: "草稿里尚未发布的候选条目与审核结果会被删除（病例本身保留，之后可重新抽取）。已发布的版本不受影响。", confirmText: "丢弃", danger: true });
    if (r.ok) act.run("dc", async () => { await api.del(`/style/profiles/${p.id}/draft`); refresh(); }, { ok: "草稿已丢弃" });
  };
  if (error) return <ErrorBox error={error} onRetry={() => mutate()} />;
  if (!data) return <Skeleton className="h-40 w-full" />;
  const vs: any[] = data.versions;
  return (
    <div className="space-y-4">
      <Banner kind="info" title="版本规则">已发布的版本是<b>不可变快照</b>（含内容哈希）。新增资料、修改、回退都发生在新的草稿里，审核发布后才取代当前版本；旧版本永远保留，便于追溯“当时的方案参考的是哪一版”。</Banner>
      <section className="card overflow-hidden"><div className="border-b border-ink-100 px-4 py-3"><h2 className="h-sec flex items-center gap-2"><History size={16} className="text-brand-600" />版本历史</h2></div>
        <ul className="divide-y divide-ink-50">{vs.map((v) => { const [t, c] = chip(VERSION_STATUS, v.status); const isOpen = open === v.id; const current = v.id === data.active_version_id; return (
          <li key={v.id} className="px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <button className="flex items-center gap-1 font-medium text-ink-900" onClick={() => setOpen(isOpen ? null : v.id)}>{isOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}v{v.version_no}</button>
              <span className={c}>{t}</span>{current && <span className="tag-brand">当前使用</span>}
              <span className="text-xs text-ink-500">{v.status === "draft" ? `创建于 ${fmtTime(v.created_at)}` : `发布于 ${fmtTime(v.activated_at)}`}{v.retired_at ? ` · 取代于 ${fmtTime(v.retired_at)}` : ""}</span>
              {v.snapshot_hash && <span className="kbd" title="已通过条目内容的 SHA-256 前缀；方案里保存的引用快照会带上它">{v.snapshot_hash.slice(0, 10)}</span>}
              {p.can_review && (<div className="ml-auto flex gap-1.5">
                {v.status !== "draft" && !current && <button className="btn-secondary btn-sm" disabled={act.any} onClick={() => rollback(v)}><Undo2 size={13} />回退到此版</button>}
                {v.status === "draft" && <button className="btn-danger-ghost btn-sm" disabled={act.any} onClick={() => discard(v)}><Trash2 size={13} />丢弃草稿</button>}</div>)}
            </div>
            {v.note && <p className="mt-1 text-xs text-ink-500">{v.note}</p>}
            {isOpen && <div className="mt-2.5 rounded-lg bg-ink-50 p-3"><VersionEntries pid={p.id} vid={v.id} /></div>}
          </li>); })}</ul></section>
    </div>
  );
}
