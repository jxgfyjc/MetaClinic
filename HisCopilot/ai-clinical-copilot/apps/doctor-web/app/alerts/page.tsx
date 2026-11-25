"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";

// 风险预警 — Client Component（侧栏固定、右侧内容切换）
export default function AlertsPage() {
  const [alerts, setAlerts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    apiGet<any>("/api/alerts")
      .then((r) => setAlerts(r?.alerts || []))
      .catch((e: any) => setErr(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">风险预警</h1>
        <span className="text-sm text-slate-500">共 {alerts.length} 条预警</span>
      </div>
      {err && (
        <div className="card border-red-300 bg-red-50 text-red-700 text-sm">加载失败：{err}</div>
      )}

      {loading ? (
        <div className="text-sm text-slate-400 py-4 text-center">加载中…</div>
      ) : alerts.length > 0 ? (
        <div className="space-y-3">
          {alerts.map((a: any) => (
            <div key={a.id} className="card border-red-200">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-lg">🚨</span>
                  <span className="font-semibold text-red-700">
                    {a.risk_level || "HIGH"} 风险
                  </span>
                </div>
                <span className="text-xs text-slate-400">
                  {a.created_at?.slice(0, 19) || ""}
                </span>
              </div>
              <div className="text-sm mt-2 text-slate-700">
                {a.reason || a.risk_reason || ""}
              </div>
              {a.recommended_action && (
                <div className="text-sm mt-1 text-red-600">
                  建议处置：{a.recommended_action}
                </div>
              )}
              <div className="text-xs text-slate-500 mt-2">
                随访：{a.followup_id || a.followup_plan_id || "—"} · 患者：{a.patient_id || "—"}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="card text-center py-8">
          <div className="text-4xl mb-2">✅</div>
          <p className="text-sm text-slate-500">暂无风险预警</p>
          <p className="text-xs text-slate-400 mt-1">
            患者随访回答触发高风险关键词时将自动进入预警列表
          </p>
        </div>
      )}
    </div>
  );
}
