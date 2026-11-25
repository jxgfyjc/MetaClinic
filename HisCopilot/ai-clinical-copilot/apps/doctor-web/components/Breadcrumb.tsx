"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

// 面包屑 — 根据路径自动生成
const LABELS: Record<string, string> = {
  "": "工作台",
  intake: "患者自主问诊",
  patients: "患者管理",
  appointments: "预约管理",
  registration: "挂号接诊",
  encounters: "今日接诊",
  new: "新建就诊",
  "medical-records": "病历管理",
  templates: "处方模板",
  pharmacy: "药品管理",
  drugs: "西药/成药",
  herbs: "中药饮片",
  formulas: "院内协定方",
  dispensing: "药房发药",
  therapy: "理疗康复",
  doctors: "个性化配置",
  followups: "随访计划",
  alerts: "风险预警",
  patient: "患者随访端",
  knowledge: "知识库",
  admin: "系统管理",
  staff: "员工权限",
  audit: "审计日志",
  reports: "基础报表",
};

export default function Breadcrumb() {
  const pathname = usePathname();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Before mount: render minimal stable breadcrumb to match SSR.
  // After mount: build full breadcrumb from real pathname.
  const parts = mounted && pathname ? pathname.split("/").filter(Boolean) : [];

  const crumbs: { label: string; href: string }[] = [];
  let path = "";
  for (const part of parts) {
    path += "/" + part;
    const label = LABELS[part];
    if (label) {
      crumbs.push({ label, href: path });
    } else {
      // 动态 ID 段（如 enc-003, p-003, dr-001）
      crumbs.push({ label: part, href: path });
    }
  }

  if (crumbs.length === 0) {
    crumbs.push({ label: "工作台", href: "/" });
  }

  return (
    <div className="flex items-center gap-1 text-xs text-slate-500 mb-3">
      {crumbs.map((c, i) => (
        <span key={i} className="flex items-center gap-1">
          {i > 0 && <span className="text-slate-300">/</span>}
          {i < crumbs.length - 1 ? (
            <Link href={c.href} className="hover:text-brand-600">
              {c.label}
            </Link>
          ) : (
            <span className="text-slate-700 font-medium">{c.label}</span>
          )}
        </span>
      ))}
    </div>
  );
}
