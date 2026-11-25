"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import useSWR from "swr";
import {
  Activity, BellRing, BookOpen, CalendarClock, ClipboardList, FileText, FlaskConical, Home, LogOut, Menu, Pill, QrCode, Search, ShieldCheck,
  Stethoscope, UserCog, Users, X, Cpu, Syringe, ScrollText, MessageSquareHeart
} from "lucide-react";
import { ROLE_LABEL, useAuth } from "@/lib/auth";
import { Logo } from "./Logo";
import { Modal } from "./ui";
import { QrBox } from "./QrBox";
import { api } from "@/lib/api";

type Item = { href: string; label: string; icon: any; perm?: string; badge?: "followup" | "pharmacy" | "alerts" | "styleauth" };
type Group = { title: string; items: Item[] };

const NAV: Group[] = [
  { title: "工作台", items: [{ href: "/", label: "今日工作台", icon: Home, perm: "encounter.read" }] },
  {
    title: "诊疗", items: [
      { href: "/encounters", label: "今日接诊", icon: Stethoscope, perm: "encounter.read" },
      { href: "/patients", label: "患者档案", icon: Users, perm: "patient.read" },
      { href: "/appointments", label: "预约挂号", icon: CalendarClock, perm: "appointment.manage" },
      { href: "/records", label: "病历", icon: FileText, perm: "record.read" },
      { href: "/prescriptions", label: "处方", icon: ClipboardList, perm: "rx.read" },
      { href: "/style", label: "经验画像", icon: BookOpen, perm: "style.read" },
    ]
  },
  { title: "随访", items: [{ href: "/followups", label: "随访与预警", icon: MessageSquareHeart, perm: "followup.read", badge: "followup" }] },
  {
    title: "药房", items: [
      { href: "/pharmacy", label: "审方发药", icon: Pill, perm: "pharmacy.queue", badge: "pharmacy" },
      { href: "/catalog", label: "药品目录", icon: FlaskConical, perm: "catalog.read" },
    ]
  },
  {
    title: "管理", items: [
      { href: "/admin", label: "运行状态", icon: Activity, perm: "job.manage" },
      { href: "/admin/users", label: "账号权限", icon: UserCog, perm: "user.manage" },
      { href: "/admin/audit", label: "审计日志", icon: ScrollText, perm: "audit.read" },
      { href: "/style/admin", label: "画像授权与外发", icon: ShieldCheck, perm: "style.admin", badge: "styleauth" },
    ]
  },
];

function useBadges(): Record<string, number> {
  const { can } = useAuth();
  const { data: fu } = useSWR(can("followup.read") ? "/followup/inbox" : null, { refreshInterval: 60000 });
  const { data: ph } = useSWR(can("pharmacy.queue") ? "/pharmacy/queue" : null, { refreshInterval: 30000 });
  const { data: sa } = useSWR(can("style.admin") ? "/style/admin/pending-authorizations" : null, { refreshInterval: 60000 });
  return {
    styleauth: sa?.authorizations?.length || 0,
    followup: (fu?.counts?.alerts || 0) + (fu?.counts?.messages || 0),
    pharmacy: (ph?.signed?.length || 0) + (ph?.pharmacy_review?.length || 0),
  };
}

export function AppShell({ children }: { children: ReactNode }) {
  const { user, can, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [qr, setQr] = useState(false);
  const badges = useBadges();
  const { data: sys } = useSWR(can("job.read") ? "/system/status" : null, { refreshInterval: 45000 });
  useEffect(() => setOpen(false), [pathname]);

  const active = (href: string) => (href === "/" ? pathname === "/" : href === "/style" ? pathname.startsWith("/style") && !pathname.startsWith("/style/admin") : pathname === href || pathname.startsWith(href + "/"));
  const groups = NAV.map((g) => ({ ...g, items: g.items.filter((i) => !i.perm || can(i.perm)) })).filter((g) => g.items.length);
  const models = sys?.models || {};
  const down = Object.entries(models).filter(([, v]: any) => !v.ok).map(([k]) => ({ llm_main: "主模型", llm_tcm: "中医模型", asr: "语音识别", ocr: "OCR" } as any)[k] || k);

  const Sidebar = (
    <aside className="flex h-full w-[236px] shrink-0 flex-col bg-ink-950 text-ink-200">
      <div className="flex items-center gap-2.5 px-4 pb-4 pt-5">
        <Logo size={32} light />
        <div className="leading-tight"><div className="text-[15px] font-semibold text-white">MetaClinic</div><div className="text-[10.5px] text-ink-400">中西医结合 · 医师决策辅助</div></div>
      </div>
      <nav className="flex-1 space-y-4 overflow-y-auto px-2.5 pb-3 pt-1">
        {groups.map((g) => (
          <div key={g.title}>
            <div className="px-2.5 pb-1 text-[10.5px] font-semibold uppercase tracking-wider text-ink-500">{g.title}</div>
            {g.items.map((it) => {
              const I = it.icon, on = active(it.href), n = it.badge ? badges[it.badge] : 0;
              return (
                <Link key={it.href} href={it.href} className={`group relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] transition ${on ? "bg-white/10 font-medium text-white" : "text-ink-300 hover:bg-white/5 hover:text-white"}`}>
                  {on && <span className="absolute -left-2.5 top-1.5 h-6 w-1 rounded-r bg-brand-400" />}
                  <I size={17} className={on ? "text-brand-300" : "text-ink-400 group-hover:text-ink-200"} />
                  <span className="flex-1">{it.label}</span>
                  {n > 0 && <span className="grid min-w-[18px] place-items-center rounded-full bg-danger-500 px-1 text-[10px] font-semibold text-white">{n > 99 ? "99+" : n}</span>}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
      <div className="border-t border-white/10 p-3">
        <div className="mb-2 rounded-lg bg-amber-500/10 px-2.5 py-2 text-[11px] leading-snug text-amber-200"><b className="font-semibold">HIS：模拟模式</b><br />未接入真实 HIS，提交仅为演练</div>
        <div className="flex items-center gap-2.5 rounded-lg px-1.5 py-1">
          <div className="grid h-8 w-8 place-items-center rounded-full bg-brand-600 text-[13px] font-semibold text-white">{user!.display_name.slice(0, 1)}</div>
          <div className="min-w-0 flex-1 leading-tight"><div className="truncate text-[13px] font-medium text-white">{user!.display_name}</div><div className="truncate text-[11px] text-ink-400">{ROLE_LABEL[user!.role]}{user!.title ? ` · ${user!.title}` : ""}</div></div>
          <Link href="/account" className="rounded-md p-1.5 text-ink-400 hover:bg-white/10 hover:text-white" title="账号"><ShieldCheck size={15} /></Link>
          <button onClick={logout} className="rounded-md p-1.5 text-ink-400 hover:bg-white/10 hover:text-white" title="退出登录"><LogOut size={15} /></button>
        </div>
      </div>
    </aside>
  );

  return (
    <div className="flex h-screen overflow-hidden">
      <div className="hidden lg:block">{Sidebar}</div>
      {open && (<div className="fixed inset-0 z-50 lg:hidden"><div className="absolute inset-0 bg-ink-950/50" onClick={() => setOpen(false)} /><div className="absolute inset-y-0 left-0 animate-slide">{Sidebar}</div></div>)}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-ink-100 bg-white/90 px-4 backdrop-blur no-print">
          <button className="rounded-lg p-2 text-ink-600 hover:bg-ink-100 lg:hidden" onClick={() => setOpen(true)} aria-label="菜单"><Menu size={20} /></button>
          <GlobalSearch />
          <div className="ml-auto flex items-center gap-2">
            {down.length > 0 && <Link href="/admin" className="tag-err" title="点击查看运行状态"><Cpu size={12} />服务异常：{down.join("、")}</Link>}
            {can("intake.link") && <button className="btn-secondary btn-sm" onClick={() => setQr(true)}><QrCode size={14} /><span className="hidden sm:inline">患者问诊二维码</span></button>}
            {can("encounter.create") && <button className="btn-primary btn-sm" onClick={() => router.push("/encounters/new")}><Stethoscope size={14} /><span className="hidden sm:inline">新建接诊</span></button>}
          </div>
        </header>
        <main className="flex-1 overflow-y-auto"><div className="mx-auto w-full max-w-[1380px] px-4 py-5 sm:px-6">{children}</div></main>
      </div>
      <QrModal open={qr} onClose={() => setQr(false)} />
    </div>
  );
}

function GlobalSearch() {
  const [q, setQ] = useState("");
  const router = useRouter();
  return (
    <form className="relative w-full max-w-sm" onSubmit={(e) => { e.preventDefault(); if (q.trim()) router.push(`/patients?q=${encodeURIComponent(q.trim())}`); }}>
      <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" />
      <input className="input rounded-full bg-ink-50 py-1.5 pl-9" placeholder="搜索患者姓名 / 手机 / 门诊号" value={q} onChange={(e) => setQ(e.target.value)} />
    </form>
  );
}

function QrModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [url, setUrl] = useState(""); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false); const [hours, setHours] = useState(24);
  const gen = async () => {
    setBusy(true); setErr("");
    try { const r = await api.post<{ url: string }>("/intake-links", { hours }); setUrl(r.url); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  useEffect(() => { if (open && !url) gen(); /* eslint-disable-next-line */ }, [open]);
  return (
    <Modal open={open} onClose={onClose} title="患者扫码问诊" width="max-w-md"
      footer={<><button className="btn-secondary" onClick={onClose}>关闭</button><button className="btn-primary" onClick={gen} disabled={busy}>重新生成</button></>}>
      <p className="mb-3 text-sm text-ink-600">患者用手机扫码，填写病史、语音、检验单和舌象后提交，资料自动进入您的待接诊队列。二维码仅含一次性令牌，<b>不含任何患者信息</b>。</p>
      {err && <div className="mb-3 rounded-lg bg-danger-50 p-2.5 text-sm text-danger-600">{err}</div>}
      <div className="grid place-items-center rounded-xl border border-ink-100 bg-ink-50 p-4">{url ? <QrBox text={url} size={220} /> : <div className="h-[220px] w-[220px] skeleton" />}</div>
      <div className="mt-3 flex items-center gap-2 text-xs text-ink-500"><span>有效期</span>
        <select className="input w-auto py-1 text-xs" value={hours} onChange={(e) => setHours(+e.target.value)}><option value={2}>2 小时</option><option value={8}>8 小时</option><option value={24}>24 小时</option><option value={72}>3 天</option></select>
        <span className="ml-auto">重新生成后旧二维码仍有效至到期</span></div>
      {url && <div className="mt-3 break-all rounded-lg bg-ink-50 p-2 font-mono text-[11px] text-ink-500">{url.replace(/#t=.*/, "#t=••••")}</div>}
      {url && !/^https:/.test(url) && !/localhost|127\.0\.0\.1/.test(url) && <div className="mt-2 text-xs text-amber-700">当前为 HTTP 地址：手机浏览器在 HTTP 下不允许使用麦克风，语音将改为“上传录音文件”。如需现场录音请使用 HTTPS 地址（见运行说明）。</div>}
    </Modal>
  );
}
