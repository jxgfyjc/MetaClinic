"use client";
import { useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { BookOpen, Eye, ShieldCheck } from "lucide-react";
import { Banner, Empty, ErrorBox, Skeleton } from "@/components/ui";
import { BASIS_LABEL, CATEGORY_ORDER } from "@/lib/style";
import { CatTag, Disclaimer, EvidenceModal, FlagList, Support } from "@/components/style/shared";

/** 使用者视角（非管理者）：只读已发布版本里“已审核通过”的条目 + 授权来源 + 免责声明。 */
export function Library({ p, disclaimer }: { p: any; disclaimer?: string }) {
  const { data, error, isLoading, mutate } = useSWR(`/style/profiles/${p.id}/entries`);
  const [ev, setEv] = useState<string | null>(null);
  const a = data?.authorization || p.authorization;
  const groups = useMemo(() => {
    const m = new Map<string, any[]>();
    for (const e of data?.entries || []) { const k = e.group_key || "（全库）"; (m.get(k) || m.set(k, []).get(k)!).push(e); }
    return [...m.entries()].map(([k, es]) => [k, es.sort((x, y) => CATEGORY_ORDER.indexOf(x.category) - CATEGORY_ORDER.indexOf(y.category))] as [string, any[]]);
  }, [data]);
  return (
    <div className="space-y-4">
      {a && <Banner kind="ok" title={<span className="flex items-center gap-1.5"><ShieldCheck size={15} />共享授权来源</span>}>{BASIS_LABEL[a.basis_type]}：{a.basis_text}（授权人：{a.authorizer_name}；范围：{a.scope === "dept" ? `科室「${a.scope_dept}」` : "全院"}{a.valid_until ? `；有效期至 ${a.valid_until.slice(0, 10)}` : ""}）</Banner>}
      <Disclaimer text={disclaimer} />
      {p.usable && <div className="rounded-xl border border-brand-100 bg-brand-50/50 px-4 py-3 text-sm text-ink-700"><BookOpen size={15} className="mr-1.5 inline text-brand-600" />在接诊的<b>“治疗方案”</b>步骤，可勾选这份画像作为参考；AI 会分栏列出“这位医师通常怎么做 / 与本患者是否吻合 / 本方案与之异同”，最终仍由您决定。 <Link href="/encounters" className="ml-1 text-brand-700 hover:underline">去接诊 →</Link></div>}
      {error ? <ErrorBox error={error} onRetry={() => mutate()} /> : isLoading ? <Skeleton className="h-48 w-full" /> : groups.length === 0 ? <div className="card"><Empty title="这份画像还没有已发布的内容" /></div> :
        groups.map(([g, es]) => (
          <section key={g} className="card overflow-hidden"><div className="border-b border-ink-100 bg-ink-50/60 px-4 py-2.5"><h3 className="text-sm font-semibold text-ink-800">{g === "（全库）" ? "整体习惯" : `证候 / 病证：${g}`}</h3></div>
            <ul className="divide-y divide-ink-50">{es.map((e) => (
              <li key={e.id} className="px-4 py-3.5"><div className="flex flex-wrap items-center gap-1.5"><CatTag c={e.category} /><span className="font-medium text-ink-900">{e.title}</span></div>
                <p className="mt-1.5 text-[13px] leading-relaxed text-ink-700">{e.summary}</p>
                {(e.conditions || e.exceptions) && <p className="mt-1 text-xs text-ink-500">{e.conditions && <>适用：{e.conditions}　</>}{e.exceptions && <>例外：{e.exceptions}</>}</p>}
                <div className="mt-2 flex flex-wrap items-center gap-2"><Support n={e.support_count} of={e.group_total} conf={e.confidence} /><button className="inline-flex items-center gap-1 text-xs text-brand-700 hover:underline" onClick={() => setEv(e.id)}><Eye size={13} />查看证据片段</button></div>
                <div className="mt-2"><FlagList flags={e.flags?.filter((f: any) => f.severity !== "info")} /></div></li>))}</ul></section>))}
      <EvidenceModal entryId={ev} onClose={() => setEv(null)} />
    </div>
  );
}
