"use client";
import { useState } from "react";
import useSWR from "swr";
import { KeyRound, Plus, ShieldOff, UserCog } from "lucide-react";
import { api, errMsg, fmtTime } from "@/lib/api";
import { ROLE_LABEL, useAuth } from "@/lib/auth";
import { Banner, ErrorBox, Field, Modal, SkeletonList, Spinner, useConfirm, useToast } from "@/components/ui";
import { useAction } from "@/lib/useAction";

const ROLES = ["DOCTOR", "NURSE", "PHARMACIST", "ADMIN"];
const ROLE_HINT: Record<string, string> = { DOCTOR: "诊断、方案、处方签署、病历定稿", NURSE: "登记、预约、采集资料；不能诊断或签署", PHARMACIST: "只看已签署处方；审方、发药、维护药品目录", ADMIN: "账号、审计、运行状态；不能签署处方或定稿病历" };

export default function Users() {
  const { user: me } = useAuth(); const toast = useToast(); const confirm = useConfirm(); const act = useAction();
  const { data, error, mutate } = useSWR("/admin/users");
  const [edit, setEdit] = useState<any>(null); const [pw, setPw] = useState<any>(null);
  if (error) return <ErrorBox error={error} onRetry={() => mutate()} />;
  const toggle = async (u: any) => {
    const c = await confirm({ title: u.active ? `停用 ${u.display_name}？` : `启用 ${u.display_name}？`, body: u.active ? "停用后该账号立即退出所有登录，且无法再登录。历史记录与审计保留。" : "启用后可重新登录。", confirmText: u.active ? "停用" : "启用", danger: u.active });
    if (c.ok) act.run(`t-${u.id}`, async () => { await api.patch(`/admin/users/${u.id}`, { active: !u.active }); await mutate(); }, { ok: u.active ? "已停用" : "已启用" });
  };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="h-page">账号与权限</h1><p className="mt-1 text-sm text-ink-500">权限由服务端按角色强制执行，界面隐藏只是便利。</p></div><button className="btn-primary" onClick={() => setEdit({})}><Plus size={16} />新建账号</button></div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{ROLES.map((r) => <div key={r} className="rounded-xl border border-ink-100 bg-white p-3"><div className="text-sm font-semibold text-ink-800">{ROLE_LABEL[r]}</div><div className="mt-0.5 text-xs text-ink-500">{ROLE_HINT[r]}</div></div>)}</div>
      <div className="card overflow-hidden">{!data ? <SkeletonList rows={5} /> : (
        <div className="overflow-x-auto"><table className="tbl"><thead><tr><th>工号</th><th>姓名</th><th>角色</th><th>科室 / 职称</th><th>状态</th><th>最近登录</th><th /></tr></thead>
          <tbody>{data.items.map((u: any) => (
            <tr key={u.id} className={u.active ? "" : "opacity-60"}><td className="font-mono text-xs">{u.username}</td><td className="font-medium">{u.display_name}{u.id === me?.id && <span className="ml-1.5 tag-brand">我</span>}</td><td><span className="tag-neutral">{ROLE_LABEL[u.role]}</span></td><td className="text-ink-500">{[u.dept, u.title].filter(Boolean).join(" · ") || "—"}</td>
              <td>{u.active ? (u.must_change_password ? <span className="tag-warn">待改密</span> : <span className="tag-ok">正常</span>) : <span className="tag-err">已停用</span>}</td><td className="text-ink-500">{fmtTime(u.last_login_at)}</td>
              <td className="whitespace-nowrap text-right"><button className="btn-ghost btn-sm" onClick={() => setEdit(u)}><UserCog size={13} />编辑</button><button className="btn-ghost btn-sm" onClick={() => setPw(u)}><KeyRound size={13} />重置密码</button>{u.id !== me?.id && <button className="btn-ghost btn-sm text-ink-500" disabled={act.any} onClick={() => toggle(u)}><ShieldOff size={13} />{u.active ? "停用" : "启用"}</button>}</td></tr>))}</tbody></table></div>)}</div>
      {edit && <UserModal u={edit} onClose={() => setEdit(null)} onDone={() => { setEdit(null); mutate(); }} />}
      {pw && <PwModal u={pw} onClose={() => setPw(null)} />}
    </div>
  );
}

function UserModal({ u, onClose, onDone }: { u: any; onClose: () => void; onDone: () => void }) {
  const act = useAction(); const isNew = !u.id; const { user: me } = useAuth(); const [err, setErr] = useState("");
  const [f, setF] = useState({ username: u.username || "", display_name: u.display_name || "", role: u.role || "DOCTOR", dept: u.dept || "", title: u.title || "", license_no: u.license_no || "", password: "" });
  const set = (k: string, v: string) => setF((s) => ({ ...s, [k]: v }));
  const self = u.id === me?.id;
  const save = () => act.run("save", async () => {
    try {
      if (isNew) await api.post("/admin/users", f); else await api.patch(`/admin/users/${u.id}`, { display_name: f.display_name, role: f.role, dept: f.dept, title: f.title, license_no: f.license_no });
      onDone();
    } catch (e) { setErr(errMsg(e)); }
  }, { ok: isNew ? "账号已创建，首次登录需修改密码" : "已保存", quiet: true });
  return (
    <Modal open onClose={onClose} title={isNew ? "新建账号" : `编辑 ${u.username}`} footer={<><button className="btn-secondary" onClick={onClose}>取消</button><button className="btn-primary" disabled={act.any || !f.display_name.trim() || (isNew && (!f.username.trim() || !f.password))} onClick={save}>{act.is("save") ? <Spinner size={14} /> : null}保存</button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="工号" required hint={isNew ? "字母、数字、._-" : "不可修改"}><input className="input" disabled={!isNew} value={f.username} onChange={(e) => set("username", e.target.value)} autoFocus={isNew} /></Field>
        <Field label="姓名" required><input className="input" value={f.display_name} onChange={(e) => set("display_name", e.target.value)} /></Field>
        <Field label="角色" required hint={ROLE_HINT[f.role]}><select className="input" disabled={self} value={f.role} onChange={(e) => set("role", e.target.value)}>{ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}</select></Field>
        <Field label="科室"><input className="input" value={f.dept} onChange={(e) => set("dept", e.target.value)} /></Field>
        <Field label="职称"><input className="input" value={f.title} onChange={(e) => set("title", e.target.value)} /></Field>
        <Field label="执业证号" hint="医师处方 PDF 会显示"><input className="input" value={f.license_no} onChange={(e) => set("license_no", e.target.value)} /></Field>
        {isNew && <div className="sm:col-span-2"><Field label="初始密码" required hint="至少 8 位且含字母和数字；用户首次登录必须修改"><input className="input" type="text" autoComplete="off" value={f.password} onChange={(e) => set("password", e.target.value)} /></Field></div>}
      </div>
      {self && <div className="mt-3"><Banner kind="info">不能修改自己的角色，以免管理员把自己锁在系统外。</Banner></div>}
      {err && <div role="alert" className="mt-3 rounded-lg border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-600">{err}</div>}
    </Modal>);
}

function PwModal({ u, onClose }: { u: any; onClose: () => void }) {
  const act = useAction(); const [pw, setPw] = useState(""); const [err, setErr] = useState("");
  const bad = pw && (pw.length < 8 || /^\d+$/.test(pw) || /^[A-Za-z]+$/.test(pw));
  return (
    <Modal open onClose={onClose} title={`重置 ${u.display_name} 的密码`} width="max-w-sm" footer={<><button className="btn-secondary" onClick={onClose}>取消</button><button className="btn-primary" disabled={act.any || !pw || !!bad} onClick={() => act.run("pw", async () => { try { await api.post(`/admin/users/${u.id}/reset-password`, { new_password: pw }); onClose(); } catch (e) { setErr(errMsg(e)); } }, { ok: "密码已重置，该用户所有登录已退出，下次登录需修改密码", quiet: true })}>重置</button></>}>
      <Field label="临时密码" required error={bad ? "至少 8 位，且需同时包含字母和数字" : undefined} hint="通过安全渠道告知本人；本人登录后必须立即修改"><input className="input" autoComplete="off" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus /></Field>
      {err && <div role="alert" className="mt-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-600">{err}</div>}
    </Modal>);
}
