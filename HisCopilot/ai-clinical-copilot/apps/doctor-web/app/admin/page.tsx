import Link from "next/link";

// 系统管理入口 — 导航到子模块
export default function AdminIndexPage() {
  const modules = [
    { href: "/knowledge", title: "知识库", desc: "RAG 检索，临床指南/文献" },
    { href: "/admin/staff", title: "员工权限", desc: "员工列表、角色权限管理" },
    { href: "/admin/audit", title: "审计日志", desc: "操作留痕、风险审计" },
    { href: "/admin/reports", title: "基础报表", desc: "门诊统计、经营报表" },
  ];

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">系统管理</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {modules.map((m) => (
          <Link key={m.href} href={m.href} className="card hover:border-brand-500 transition">
            <div className="flex items-center justify-between">
              <span className="font-medium">{m.title}</span>
              <span className="text-brand-600 text-xs">→</span>
            </div>
            <div className="text-xs text-slate-500 mt-1">{m.desc}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}
