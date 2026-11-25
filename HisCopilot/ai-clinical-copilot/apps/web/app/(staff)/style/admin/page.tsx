"use client";
import Link from "next/link";
import useSWR from "swr";
import { CheckCircle2, Cpu, ShieldCheck, ShieldOff } from "lucide-react";
import { Banner, Empty, ErrorBox, Skeleton, useConfirm } from "@/components/ui";
import { api, fmtTime } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { useAuth } from "@/lib/auth";
import { BASIS_LABEL } from "@/lib/style";

export default function StyleAdmin() {
  const { can } = useAuth();
  const ext = useSWR(can("style.admin") ? "/style/admin/external" : null); const pend = useSWR(can("style.admin") ? "/style/admin/pending-authorizations" : null, { refreshInterval: 30000 });
  const act = useAction(); const confirm = useConfirm();
  if (!can("style.admin")) return <Banner kind="warn">仅管理员可访问。</Banner>;

  const toggle = async (allow: boolean) => {
    if (allow) {
      const r = await confirm({ title: "允许使用外部大模型做病例抽取？", confirmText: "开启",
        body: <div className="space-y-2 text-sm"><p>开启后，医师在导入向导里<b>可以选择</b>把“已脱敏、且已逐份确认”的病例文本发送给外部模型。每个批次仍需医师勾选书面确认；脱敏风险为“高”的病例不会外发。</p>
          <p className="rounded-lg bg-amber-50 px-3 py-2 text-amber-800">规则脱敏<b>不能保证</b>万无一失。开启前请确认：① 机构的数据合规要求允许；② 外部服务方的数据处理条款可接受；③ 这是您有权做的决定。此开关的每次变更都会写入审计。</p></div> });
      if (!r.ok) return;
    }
    act.run("ext", async () => { await api.put("/style/admin/external", { allow }); ext.mutate(); }, { ok: allow ? "已开启" : "已关闭" });
  };
  const approve = async (a: any) => {
    const r = await confirm({ title: "备案这条共享授权？", confirmText: "确认备案",
      body: <div className="space-y-1.5 text-sm"><p>画像：<b>{a.profile.subject_name}</b> {a.profile.proxy_created && <span className="tag-neutral">管理员代建</span>}</p><p>{BASIS_LABEL[a.basis_type]}：{a.basis_text}</p><p>授权人：{a.authorizer_name}；范围：{a.scope === "dept" ? `科室「${a.scope_dept}」` : "全院"}{a.valid_until ? `；有效期至 ${a.valid_until.slice(0, 10)}` : ""}</p>
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-amber-800">备案表示您已核查上述授权来源属实。备案后，范围内的医师可看到并选用该画像；使用处会显示这条授权来源。</p></div> });
    if (r.ok) act.run("ap", async () => { await api.post(`/style/authorizations/${a.id}/approve`); pend.mutate(); }, { ok: "已备案" });
  };

  return (
    <div className="space-y-5">
      <div><h1 className="h-page">经验画像 · 授权与外发</h1><p className="mt-1 text-sm text-ink-500">备案名医经验库的共享授权，并控制是否允许使用外部大模型做病例抽取。管理员不能审核或发布临床内容（职责分离）。</p></div>

      <section className="card card-pad space-y-3">
        <h2 className="h-sec flex items-center gap-2"><Cpu size={16} className="text-brand-600" />外部大模型抽取</h2>
        {ext.error ? <ErrorBox error={ext.error} onRetry={() => ext.mutate()} /> : !ext.data ? <Skeleton className="h-16 w-full" /> : (<>
          <div className="flex flex-wrap items-center gap-3">
            {ext.data.allow_external ? <span className="tag-ok"><CheckCircle2 size={11} />已允许</span> : <span className="tag-neutral"><ShieldOff size={11} />未允许（默认）</span>}
            {ext.data.configured ? <span className="tag-ok">已配置端点</span> : <span className="tag-warn">尚未配置端点</span>}
            <button className={ext.data.allow_external ? "btn-secondary btn-sm ml-auto" : "btn-primary btn-sm ml-auto"} disabled={act.any} onClick={() => toggle(!ext.data.allow_external)}>{ext.data.allow_external ? "关闭" : "开启"}</button>
          </div>
          {!ext.data.configured && <Banner kind="info" title="如何配置端点">在服务器环境变量中设置 <span className="kbd">XY_STYLE_EXT_BASE_URL</span>、<span className="kbd">XY_STYLE_EXT_MODEL</span>、<span className="kbd">XY_STYLE_EXT_API_KEY</span>（可选 <span className="kbd">XY_STYLE_EXT_EXTRA_JSON</span>）后重启 API。密钥只从环境变量读取，不会存入数据库，也不会通过任何接口返回。未配置时，即使开关打开，医师选择“外部模型”也会明确失败，不会悄悄改用别的模型。</Banner>}
          <p className="text-xs text-ink-500">默认全部在本机模型上处理，数据不出机器。开启后每个批次仍需医师逐份确认并勾选书面同意。</p></>)}
      </section>

      <section className="card overflow-hidden">
        <div className="flex items-center gap-2 border-b border-ink-100 px-4 py-3"><h2 className="h-sec flex items-center gap-2"><ShieldCheck size={16} className="text-brand-600" />待备案的共享授权</h2></div>
        {pend.error ? <div className="p-4"><ErrorBox error={pend.error} onRetry={() => pend.mutate()} /></div> : !pend.data ? <Skeleton className="m-4 h-24" /> : pend.data.authorizations.length === 0 ? <Empty title="没有待备案的授权" hint="医师或管理员在画像的“共享与授权”页提交声明后，会出现在这里。" /> : (
          <div className="overflow-x-auto"><table className="tbl"><thead><tr><th>画像</th><th>授权依据</th><th>授权人</th><th>范围</th><th>声明</th><th /></tr></thead><tbody>
            {pend.data.authorizations.map((a: any) => (
              <tr key={a.id}><td><Link href={`/style/${a.profile.id}`} className="font-medium text-brand-700 hover:underline">{a.profile.subject_name}</Link>{a.profile.subject_title && <span className="ml-1 text-xs text-ink-400">{a.profile.subject_title}</span>}{a.profile.proxy_created && <span className="ml-1 tag-neutral">代建</span>}</td>
                <td className="max-w-xs text-sm">{BASIS_LABEL[a.basis_type]}：{a.basis_text}</td><td>{a.authorizer_name}</td><td>{a.scope === "dept" ? a.scope_dept : "全院"}{a.valid_until ? <div className="text-xs text-ink-400">至 {a.valid_until.slice(0, 10)}</div> : null}</td>
                <td className="text-xs text-ink-500">{fmtTime(a.created_at)}</td><td><button className="btn-primary btn-sm" disabled={act.any} onClick={() => approve(a)}>备案</button></td></tr>))}</tbody></table></div>)}
        <div className="border-t border-ink-100 px-4 py-2.5 text-xs text-ink-500">备案前请点开画像核查内容与来源。撤回已备案的授权：进入该画像的“共享与授权”页。</div>
      </section>
    </div>
  );
}
