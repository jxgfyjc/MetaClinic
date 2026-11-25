"use client";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, Lock, User as UserIcon } from "lucide-react";
import { ROLE_HOME, useAuth } from "@/lib/auth";
import { Logo } from "@/components/Logo";
import { Spinner } from "@/components/ui";
import { errMsg } from "@/lib/api";

function LoginForm() {
  const { user, login, loading } = useAuth();
  const router = useRouter();
  const sp = useSearchParams();
  const [username, setU] = useState(""); const [password, setP] = useState("");
  const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  useEffect(() => {
    if (!loading && user) router.replace(user.must_change_password ? "/account?force=1" : sp.get("next") || ROLE_HOME[user.role] || "/");
  }, [loading, user, router, sp]);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { await login(username.trim(), password); } catch (e) { setErr(errMsg(e)); setBusy(false); }
  };
  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <div className="relative hidden overflow-hidden bg-ink-950 p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="absolute -left-24 -top-24 h-96 w-96 rounded-full bg-brand-600/30 blur-3xl" /><div className="absolute -bottom-32 right-0 h-[28rem] w-[28rem] rounded-full bg-jade-500/20 blur-3xl" />
        <div className="relative flex items-center gap-3"><Logo size={40} light /><span className="text-xl font-semibold tracking-tight">MetaClinic</span></div>
        <div className="relative max-w-md">
          <h1 className="text-[34px] font-semibold leading-tight tracking-tight">让经验不足的医师，<br />也能稳妥地做出好决策。</h1>
          <p className="mt-4 text-[15px] leading-relaxed text-ink-300">AI 完成预分析与证据整理，医师逐项审核、修改、确认。诊断、治疗方案、处方、病历全程留痕，最终决定权始终在医师手中。</p>
          <ul className="mt-8 space-y-2.5 text-sm text-ink-300">{["中西医并行分析，每条结论可追溯到患者原始资料", "处方内置十八反、十九畏、妊娠禁忌与过敏交叉校验", "病历版本化保存，定稿后只能“更正”，不可悄悄改写"].map((t) => <li key={t} className="flex gap-2"><span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-jade-400" />{t}</li>)}</ul>
        </div>
        <div className="relative text-xs text-ink-500">私有化部署 · 患者数据不出院内网络</div>
      </div>
      <div className="flex items-center justify-center px-6 py-10">
        <form onSubmit={submit} className="w-full max-w-[360px]" autoComplete="on">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden"><Logo size={34} /><span className="text-lg font-semibold">MetaClinic</span></div>
          <h2 className="text-2xl font-semibold tracking-tight text-ink-950">登录工作台</h2>
          <p className="mt-1.5 text-sm text-ink-500">请使用工号登录。患者请扫描医师提供的二维码。</p>
          <div className="mt-7 space-y-4">
            <div><label className="label" htmlFor="u">工号</label><div className="relative"><UserIcon size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" /><input id="u" className="input pl-9" value={username} onChange={(e) => setU(e.target.value)} autoComplete="username" autoFocus required /></div></div>
            <div><label className="label" htmlFor="p">密码</label><div className="relative"><Lock size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" /><input id="p" type="password" className="input pl-9" value={password} onChange={(e) => setP(e.target.value)} autoComplete="current-password" required /></div></div>
          </div>
          {err && <div role="alert" className="mt-4 rounded-lg border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-600">{err}</div>}
          <button className="btn-primary btn-lg mt-6 w-full" disabled={busy || !username || !password}>{busy ? <Spinner /> : <>登录<ArrowRight size={16} /></>}</button>
          <p className="mt-6 text-center text-xs text-ink-400">忘记密码？请联系管理员重置。连续输错 5 次将锁定账号 10 分钟。</p>
        </form>
      </div>
    </div>
  );
}
export default function LoginPage() { return <Suspense><LoginForm /></Suspense>; }
