"use client";
import { useState } from "react";
import { ShieldCheck } from "lucide-react";
import { Banner, Field, useConfirm } from "@/components/ui";
import { api, fmtTime } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { useAuth } from "@/lib/auth";
import { AUTH_STATUS, BASIS_LABEL, chip } from "@/lib/style";

export function Sharing({ p, auths, onChanged }: { p: any; auths: any[]; onChanged: () => void }) {
  const { user } = useAuth(); const act = useAction(); const confirm = useConfirm();
  const isOwner = p.owner_user_id === user?.id;
  const [f, setF] = useState({ basis_type: isOwner ? "self" : "family", basis_text: "", authorizer_name: isOwner ? p.subject_name : "", scope: "hospital", scope_dept: "", valid_until: "" });
  const cur = auths.find((a) => a.status === "approved"); const pending = auths.find((a) => a.status === "pending");
  const valid = f.basis_text.trim().length >= 6 && f.authorizer_name.trim() && (f.scope === "hospital" || f.scope_dept.trim());
  const submit = () => act.run("decl", async () => { await api.post(`/style/profiles/${p.id}/authorizations`, { ...f, valid_until: f.valid_until || null }); onChanged(); }, { ok: "已提交，等待管理员备案" });
  const revoke = async (a: any) => {
    const r = await confirm({ title: "撤回共享授权？", body: "撤回后其他医师立即看不到、也不能再选用这份画像；已经生成的方案保留当时的引用快照，不受影响。", confirmText: "撤回", danger: true, reason: { label: "撤回原因", min: 4 } });
    if (r.ok) act.run("rev", async () => { await api.post(`/style/authorizations/${a.id}/revoke`, { reason: r.reason }); onChanged(); }, { ok: "已撤回" });
  };
  return (
    <div className="space-y-4">
      <Banner kind="info" title="共享规则">其他医师要看到并选用这份经验库，需要同时满足：① 已<b>发布</b>版本；② 有一条<b>管理员已备案</b>且未撤回、未过期的授权。授权记录（依据、授权人、范围）会随经验一起展示给使用者，并写入审计。<b>共享不代表名医本人审核或认可 AI 当次生成的方案。</b></Banner>
      {!p.active_version && <Banner kind="warn">还没有发布版本——即使授权备案，其他医师也看不到任何内容。</Banner>}
      <section className="card card-pad">
        <h2 className="h-sec mb-2 flex items-center gap-2"><ShieldCheck size={16} className="text-brand-600" />当前状态</h2>
        {cur ? (<div className="text-sm"><span className="tag-ok">已共享</span><span className="ml-2">范围：<b>{cur.scope === "dept" ? `科室「${cur.scope_dept}」` : "全院"}</b></span><p className="mt-2 text-ink-700">{BASIS_LABEL[cur.basis_type]}：{cur.basis_text}（授权人：{cur.authorizer_name}{cur.valid_until ? `，有效期至 ${cur.valid_until.slice(0, 10)}` : ""}）</p>
          <button className="btn-danger-ghost btn-sm mt-3" disabled={act.any} onClick={() => revoke(cur)}>撤回共享</button></div>) :
          pending ? <p className="text-sm"><span className="tag-warn">待管理员备案</span><span className="ml-2 text-ink-600">已提交 {fmtTime(pending.created_at)}，备案前其他医师仍看不到。</span></p> : <p className="text-sm text-ink-600"><span className="tag-neutral">未共享</span> 目前只有您和指定的审核医师能看到。</p>}
      </section>
      {!pending && (
        <section className="card card-pad space-y-3">
          <h2 className="h-sec">声明共享授权</h2>
          {p.proxy_created && <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">本画像为管理员代建：请选择“家属 / 单位 / 公开出版物 / 其他”并填写可核查的文件编号或出处。</p>}
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="授权依据" required><select className="input" value={f.basis_type} onChange={(e) => setF({ ...f, basis_type: e.target.value })}>{Object.entries(BASIS_LABEL).filter(([k]) => k !== "self" || isOwner).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></Field>
            <Field label="授权人" required hint="本人 / 家属姓名 / 单位名称"><input className="input" maxLength={64} value={f.authorizer_name} onChange={(e) => setF({ ...f, authorizer_name: e.target.value })} /></Field>
          </div>
          <Field label="授权来源说明" required hint="授权文件编号、签署日期、出版物名称与页码等，至少 6 个字；须可核查。"><textarea className="input" rows={2} maxLength={400} value={f.basis_text} onChange={(e) => setF({ ...f, basis_text: e.target.value })} placeholder="例：《周某某经验授权书》SQ-2026-001，2026-10-12 签署" /></Field>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="共享范围"><select className="input" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })}><option value="hospital">全院</option><option value="dept">指定科室</option></select></Field>
            {f.scope === "dept" && <Field label="科室" required><input className="input" value={f.scope_dept} onChange={(e) => setF({ ...f, scope_dept: e.target.value })} /></Field>}
            <Field label="有效期至（可选）"><input type="date" className="input" value={f.valid_until} onChange={(e) => setF({ ...f, valid_until: e.target.value })} /></Field>
          </div>
          <button className="btn-primary" disabled={!valid || act.any} onClick={submit}>提交，等待备案</button>
        </section>)}
      {auths.length > 0 && (
        <section className="card overflow-hidden"><div className="border-b border-ink-100 px-4 py-3"><h2 className="h-sec">授权记录</h2></div>
          <table className="tbl"><thead><tr><th>状态</th><th>依据</th><th>范围</th><th>声明</th><th>说明</th></tr></thead><tbody>{auths.map((a) => { const [t, c] = chip(AUTH_STATUS, a.status); return (
            <tr key={a.id}><td><span className={c}>{t}</span></td><td className="text-sm">{BASIS_LABEL[a.basis_type]}：{a.basis_text}<div className="text-xs text-ink-400">授权人：{a.authorizer_name}</div></td><td>{a.scope === "dept" ? a.scope_dept : "全院"}</td><td className="text-xs text-ink-500">{fmtTime(a.created_at)}</td><td className="text-xs text-ink-500">{a.status === "revoked" ? `撤回：${a.revoke_reason}` : a.status === "approved" ? `备案于 ${fmtTime(a.approved_at)}` : ""}</td></tr>); })}</tbody></table></section>)}
    </div>
  );
}
