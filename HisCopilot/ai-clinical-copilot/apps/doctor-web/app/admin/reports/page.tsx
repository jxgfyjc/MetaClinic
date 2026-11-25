"use client";

import { useEffect, useState } from "react";
import DemoBanner from "@/components/DemoBanner";
import { apiGet } from "@/lib/api";

// 基础报表 — Client Component（侧栏固定、右侧内容切换）
export default function ReportsPage() {
  const [encounterCount, setEncounterCount] = useState(0);
  const [patientCount, setPatientCount] = useState(0);
  const [alertCount, setAlertCount] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      apiGet<any>("/api/encounters").catch(() => []),
      apiGet<any>("/mock-his/patients").catch(() => []),
      apiGet<any>("/api/alerts").catch(() => ({ alerts: [] })),
    ]).then(([e, p, a]) => {
      setEncounterCount(Array.isArray(e) ? e.length : (e?.items?.length || 0));
      setPatientCount(Array.isArray(p) ? p.length : (p?.items?.length || 0));
      setAlertCount(a?.alerts?.length || 0);
    }).finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">基础报表</h1>
      <DemoBanner message="仅门诊概览接通实时数据，详细统计报表未接入" />

      {/* 门诊概览（真实数据） */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="card">
          <div className="text-2xl font-bold text-brand-600">{loading ? "…" : encounterCount}</div>
          <div className="text-xs text-slate-600">就诊总数</div>
        </div>
        <div className="card">
          <div className="text-2xl font-bold text-brand-600">{loading ? "…" : patientCount}</div>
          <div className="text-xs text-slate-600">患者总数</div>
        </div>
        <div className="card">
          <div className="text-2xl font-bold text-red-600">{loading ? "…" : alertCount}</div>
          <div className="text-xs text-slate-600">风险预警</div>
        </div>
        <div className="card">
          <div className="text-2xl font-bold text-slate-400">—</div>
          <div className="text-xs text-slate-600">门诊收入 (未接通)</div>
        </div>
      </div>

      {/* 报表列表 */}
      <div className="card">
        <h2 className="font-semibold mb-2">报表清单</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {[
            { name: "门诊日志", status: "未实现" },
            { name: "经营统计", status: "未实现" },
            { name: "营收统计", status: "未实现" },
            { name: "经营日报", status: "未实现" },
            { name: "医务统计", status: "未实现" },
            { name: "医师绩效", status: "未实现" },
            { name: "进销存报表", status: "未实现" },
            { name: "患者消费统计", status: "未实现" },
          ].map((r) => (
            <div key={r.name} className="flex items-center justify-between p-2 rounded hover:bg-slate-50">
              <span className="text-sm">{r.name}</span>
              <span className="badge-muted text-xs">{r.status}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
