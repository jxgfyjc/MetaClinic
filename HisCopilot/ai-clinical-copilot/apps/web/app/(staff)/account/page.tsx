"use client";
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { KeyRound, ShieldCheck } from "lucide-react";
import { Banner, Field, Spinner, useToast } from "@/components/ui";
import { ROLE_HOME, ROLE_LABEL, useAuth } from "@/lib/auth";
import { api, errMsg, fmtTime } from "@/lib/api";

function Account() {
  const { user, refresh } = useAuth(); const router = useRouter(); const sp = useSearchParams(); const toast = useToast();
  const forced = sp.get("force") === "1" || !!user?.must_change_password;
  const [cur, setCur] = useState(""); const [pw, setPw] = useState(""); const [pw2, setPw2] = useState(""); const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const problem = pw && (pw.length < 8 ? "至少 8 位" : /^\d+$/.test(pw) || /^[A-Za-z]+$/.test(pw) ? "需同时包含字母和数字" : pw === cur ? "不能与当前密码相同" : "");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr("");
    if (problem) return setErr(problem); if (pw !== pw2) return setErr("两次输入的新密码不一致");
    setBusy(true);
    try { await api.post("/auth/change-password", { current_password: cur, new_password: pw }); toast("ok", "密码已修改，其他设备上的登录已退出"); await refresh(); router.replace(ROLE_HOME[user!.role] || "/"); }
    catch (x) { setErr(errMsg(x)); setBusy(false); }
  };
  if (!user) return null;
  return (
    <div className="mx-auto max-w-xl space-y-5">
      <div><h1 className="h-page">我的账号</h1></div>
      <section className="card card-pad"><div className="flex items-center gap-3"><span className="grid h-12 w-12 place-items-center rounded-2xl bg-brand-50 text-lg font-semibold text-brand-700">{user.display_name.slice(0, 1)}</span>
        <div><div className="font-semibold text-ink-900">{user.display_name}</div><div className="text-sm text-ink-500">{ROLE_LABEL[user.role]}{user.title ? ` · ${user.title}` : ""}{user.dept ? ` · ${user.dept}` : ""}</div><div className="text-xs text-ink-400">工号 {user.username}{user.license_no ? ` · 执业证号 ${user.license_no}` : ""}</div></div></div></section>
      <section className="card card-pad">
        <div className="mb-3 flex items-center gap-2"><KeyRound size={16} className="text-brand-600" /><h2 className="h-sec">修改密码</h2></div>
        {forced && <div className="mb-4"><Banner kind="warn" title="请先修改密码">首次登录或密码被管理员重置后，必须设置新密码才能继续使用系统。</Banner></div>}
        <form onSubmit={submit} className="space-y-3.5">
          <Field label="当前密码" required><input className="input" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required /></Field>
          <Field label="新密码" required error={problem || undefined} hint="至少 8 位，需同时包含字母和数字"><input className="input" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} required /></Field>
          <Field label="确认新密码" required><input className="input" type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} required /></Field>
          {err && <div role="alert" className="rounded-lg border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-600">{err}</div>}
          <button className="btn-primary" disabled={busy || !cur || !pw || !pw2 || !!problem}>{busy ? <Spinner size={14} /> : <ShieldCheck size={15} />}修改密码</button>
        </form>
      </section>
      <p className="text-xs text-ink-400">上次登录：{fmtTime((user as any).last_login_at)}。会话在服务端可随时撤销；修改密码会使其他设备退出登录。</p>
    </div>
  );
}
export default function Page() { return <Suspense><Account /></Suspense>; }
