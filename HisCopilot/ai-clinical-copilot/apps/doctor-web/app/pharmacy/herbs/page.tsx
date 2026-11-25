"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";

// 中药饮片管理 — Client Component（侧栏固定、右侧内容切换）
type Herb = {
  id: string;
  name: string;
  generic_name?: string;
  spec?: string;
  unit?: string;
  form?: string;
  usage?: string;
  source?: string;
  category?: string;
  his_code?: string;
  note?: string;
};

export default function HerbsPage() {
  const [herbs, setHerbs] = useState<Herb[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    apiGet<Herb[]>("/api/drugs?category=tcm-herb")
      .then((r) => setHerbs(Array.isArray(r) ? r : []))
      .catch((e: any) => setErr(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">中药饮片管理</h1>
        <span className="text-xs text-slate-500">共 {herbs.length} 味饮片</span>
      </div>

      {err && (
        <div className="card border-red-300 bg-red-50 text-red-700 text-sm">
          加载失败：{err}
        </div>
      )}

      <div className="card overflow-x-auto">
        {loading ? (
          <div className="text-sm text-slate-400 py-4 text-center">加载中…</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500 border-b">
              <tr>
                <th className="py-2">编码</th>
                <th>饮片名称</th>
                <th>通用名</th>
                <th>规格</th>
                <th>单位</th>
                <th>来源</th>
              </tr>
            </thead>
            <tbody>
              {herbs.map((h) => (
                <tr key={h.id} className="border-b last:border-0 hover:bg-slate-50">
                  <td className="py-2 font-mono text-xs">{h.id}</td>
                  <td className="font-medium">{h.name}</td>
                  <td className="text-slate-500 text-xs">{h.generic_name || "—"}</td>
                  <td className="text-xs text-slate-600">{h.spec || "—"}</td>
                  <td className="text-xs text-slate-600">{h.unit || "g"}</td>
                  <td>
                    <span className="badge-muted">{h.source || "imported"}</span>
                  </td>
                </tr>
              ))}
              {herbs.length === 0 && (
                <tr>
                  <td colSpan={6} className="text-center text-slate-400 py-4">
                    暂无饮片数据
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      <div className="card text-sm text-slate-600">
        <p>
          饮片数据来源：药品库（{herbs.length} 条），可在处方编辑器中搜索选用。
        </p>
        <p className="text-xs text-slate-400 mt-1">
          HIS: MockHIS（未接入真实 HIS）
        </p>
      </div>
    </div>
  );
}
