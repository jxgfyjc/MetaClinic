"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";

// 院内协定方管理 — Client Component（侧栏固定、右侧内容切换）
export default function FormulasPage() {
  const [formulas, setFormulas] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    apiGet<any>("/mock-his/formulas")
      .then((data) => setFormulas(Array.isArray(data) ? data : []))
      .catch((e: any) => setErr(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">院内协定方</h1>
        <button className="btn-primary" disabled>新增协定方</button>
      </div>
      {err && (
        <div className="card border-red-300 bg-red-50 text-red-700 text-sm">加载失败：{err}</div>
      )}

      {loading ? (
        <div className="text-sm text-slate-400 py-4 text-center">加载中…</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {formulas.map((f: any) => (
            <div key={f.id} className="card">
              <div className="flex items-center justify-between">
                <span className="font-medium">{f.name}</span>
                <span className="badge-muted">{f.formula_type || "协定方"}</span>
              </div>
              <div className="text-sm mt-1">
                <span className="text-slate-500">适应症：</span>{f.indication || "—"}
              </div>
              {f.herbs && (
                <div className="mt-2">
                  <div className="text-xs text-slate-500 mb-1">组成：</div>
                  <div className="flex flex-wrap gap-1">
                    {f.herbs.map((h: any, i: number) => (
                      <span key={i} className="text-xs px-1.5 py-0.5 rounded bg-slate-100">
                        {h.name} {h.dose}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              <div className="mt-2 text-xs font-mono text-slate-400">编码：{f.id}</div>
            </div>
          ))}
        </div>
      )}

      <div className="card text-sm text-slate-600">
        <p>共 {formulas.length} 条协定方记录。数据来源：MockHIS 协定方字典（未接入真实 HIS）。</p>
      </div>
    </div>
  );
}
