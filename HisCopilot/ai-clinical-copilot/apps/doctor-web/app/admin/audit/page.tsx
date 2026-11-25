"use client";

import { useEffect, useState } from "react";
import DemoBanner from "@/components/DemoBanner";
import { apiGet } from "@/lib/api";

// 审计日志 — Client Component（侧栏固定、右侧内容切换）
export default function AuditPage() {
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    apiGet<{ logs: any[] }>("/api/audit?limit=100")
      .then((r) => setLogs(r?.logs || []))
      .catch((e: any) => setErr(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">审计日志</h1>
      <DemoBanner message="审计日志为只读视图（数据来自 data/audit.ndjson，未接入真实 HIS 审计接口）" />

      {err && (
        <div className="card border-red-300 bg-red-50 text-red-700 text-sm">加载失败：{err}</div>
      )}

      <div className="card overflow-x-auto">
        {loading ? (
          <div className="text-sm text-slate-400 py-4 text-center">加载中…</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500 border-b">
              <tr>
                <th className="py-2">时间</th>
                <th>操作人</th>
                <th>操作</th>
                <th>对象</th>
                <th>详情</th>
                <th>级别</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((a: any, i: number) => (
                <tr key={a.id || i} className="border-b last:border-0 hover:bg-slate-50">
                  <td className="text-xs text-slate-500 py-2">{a.time}</td>
                  <td className="font-mono text-xs">{a.actor}</td>
                  <td>{a.action}</td>
                  <td className="font-mono text-xs">{a.target}</td>
                  <td className="text-xs text-slate-600">{a.detail}</td>
                  <td>
                    <span className={
                      a.level === "ERROR" ? "badge-err" :
                      a.level === "WARN" ? "badge-warn" : "badge-ok"
                    }>
                      {a.level}
                    </span>
                  </td>
                </tr>
              ))}
              {logs.length === 0 && (
                <tr><td colSpan={6} className="text-center text-slate-400 py-4">暂无审计日志</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      <div className="card text-sm text-slate-600">
        <p>所有关键操作均写审计日志（audit.ndjson + DB 双写），包括：AI 分析启动/完成、病历保存、处方状态变更、HIS 提交、随访风险评估等。</p>
      </div>
    </div>
  );
}
