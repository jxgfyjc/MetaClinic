"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

// 侧边导航 — 参考 his-functional-blueprint.md 信息架构，按本院取舍重组
// 导航组：工作台 / 患者管理 / 预约挂号 / 医师工作站 / 病历管理 /
//         药品管理 / 理疗康复 / 个性化配置 / 随访管理 / 系统管理

type NavItem = {
  label: string;
  href: string;
  badge?: string; // "Demo" | "新建" | 标识未接通
};

type NavGroup = {
  title: string;
  items: NavItem[];
};

const NAV: NavGroup[] = [
  {
    title: "工作台",
    items: [
      { label: "医生工作台", href: "/" },
    ],
  },
  {
    title: "患者管理",
    items: [
      { label: "患者列表", href: "/patients" },
    ],
  },
  {
    title: "预约挂号",
    items: [
      { label: "预约管理", href: "/appointments", badge: "未接入" },
      { label: "挂号接诊", href: "/registration", badge: "未接入" },
    ],
  },
  {
    title: "医师工作站",
    items: [
      { label: "今日接诊", href: "/encounters" },
    ],
  },
  {
    title: "病历管理",
    items: [
      { label: "病历列表", href: "/medical-records" },
      { label: "处方模板", href: "/templates", badge: "未接入" },
    ],
  },
  {
    title: "药品管理",
    items: [
      { label: "西药 / 成药", href: "/pharmacy/drugs" },
      { label: "中药饮片", href: "/pharmacy/herbs", badge: "未接入" },
      { label: "院内协定方", href: "/pharmacy/formulas" },
      { label: "药房发药", href: "/pharmacy/dispensing", badge: "未接入" },
    ],
  },
  {
    title: "理疗康复",
    items: [{ label: "治疗项目", href: "/therapy" }],
  },
  {
    title: "个性化配置",
    items: [
      { label: "医师风格", href: "/doctors" },
    ],
  },
  {
    title: "随访管理",
    items: [
      { label: "随访计划", href: "/followups" },
      { label: "风险预警", href: "/alerts" },
      { label: "患者随访端", href: "/patient", badge: "患者端" },
    ],
  },
  {
    title: "系统管理",
    items: [
      { label: "知识库", href: "/knowledge" },
      { label: "员工权限", href: "/admin/staff", badge: "未接入" },
      { label: "审计日志", href: "/admin/audit", badge: "未接入" },
      { label: "基础报表", href: "/admin/reports", badge: "未接入" },
    ],
  },
];

function isActive(pathname: string, href: string): boolean {
  // 精确匹配，或子路径匹配（/encounters 匹配 /encounters/enc-003）
  if (pathname === href) return true;
  // 但 /encounters/enc-003 不应高亮 /encounters（当用户在详情页时高亮临床诊疗室）
  if (href !== "/" && pathname.startsWith(href + "/")) return true;
  return false;
}

export default function Sidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Before mount: no active highlight (stable SSR output).
  // After mount: apply active highlighting based on real pathname.
  const activePath = mounted ? pathname : "";

  return (
    <>
      {/* 移动端汉堡菜单按钮 — 只在 <lg 屏幕显示，用于打开侧边栏抽屉 */}
      {/* 桌面端侧边栏始终可见 (lg:translate-x-0)，不需要此按钮 */}
      {!open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="打开导航菜单"
          className="fixed top-2.5 left-2 z-50 lg:hidden inline-flex items-center justify-center w-9 h-9 rounded-md bg-slate-900 text-white shadow-md hover:bg-slate-700 transition"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
      )}

      {/* 移动端遮罩 */}
      {open && (
        <div
          className="fixed inset-0 bg-black/30 z-30 lg:hidden"
          onClick={() => setOpen(false)}
        />
      )}

      <aside
        className={`fixed lg:sticky top-0 left-0 z-40 h-screen w-60 shrink-0
          bg-slate-900 text-slate-300 overflow-y-auto transition-transform
          ${open ? "translate-x-0" : "-translate-x-full lg:translate-x-0"}`}
      >
        {/* 品牌区 */}
        <div className="px-4 py-4 border-b border-slate-700/50">
          <div className="flex items-center gap-2">
            <span className="text-lg">🏥</span>
            <div>
              <div className="font-bold text-white text-sm">象元 AI 诊疗</div>
              <div className="text-[10px] text-slate-500">中西医结合门诊</div>
            </div>
          </div>
        </div>

        {/* 导航组 */}
        <nav className="py-2">
          {NAV.map((group) => (
            <div key={group.title} className="mb-1">
              <div className="px-4 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                {group.title}
              </div>
              {group.items.map((item, idx) => {
                const active = isActive(activePath, item.href);
                return (
                  <Link
                    key={`${item.href}-${idx}`}
                    href={item.href}
                    onClick={() => setOpen(false)}
                    className={`flex items-center gap-2 px-4 py-2 text-sm transition
                      ${active
                        ? "bg-brand-600/20 text-white border-l-2 border-brand-500"
                        : "hover:bg-slate-800 hover:text-white border-l-2 border-transparent"
                      }`}
                  >
                    <span className="flex-1 truncate">{item.label}</span>
                    {item.badge && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-400">
                        {item.badge}
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        {/* 底部标识 */}
        <div className="px-4 py-3 mt-auto border-t border-slate-700/50">
          <div className="text-[10px] text-slate-600">
            HIS: MockHIS（未接入真实 HIS）
          </div>
          <div className="text-[10px] text-slate-600 mt-0.5">
            私有化部署 · 数据严禁出网
          </div>
        </div>
      </aside>
    </>
  );
}
