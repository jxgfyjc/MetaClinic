"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiGet, apiDelete } from "@/lib/api";

// updatev2_3 §1+§2: Client Component (same fetch pattern as 今日接诊)
// so navigation is seamless, + delete UI (single + batch).
export default function MedicalRecordsPage() {
  const [records, setRecords] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  async function loadRecords() {
    setLoading(true); setErr("");
    try {
      const data = await apiGet<any[]>("/api/medical-records");
      setRecords(Array.isArray(data) ? data : []);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadRecords(); }, []);

  function toggle(id: string) {
    setSelected(prev => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  }
  function toggleAll() {
    setSelected(prev =>
      prev.size === records.length ? new Set() : new Set(records.map(r => r.id)));
  }

  async function deleteOne(id: string) {
    if (!confirm(`确定删除病历「${id}」？\n此操作不可撤销。`))
      return;
    try {
      await apiDelete(`/api/medical-records/${id}`);
      await loadRecords();
      setSelected(prev => { const n = new Set(prev); n.delete(id); return n; });
    } catch (e: any) {
      alert(`删除失败: ${e.message}`);
    }
  }

  async function deleteBatch() {
    if (selected.size === 0) return;
    if (!confirm(`确定批量删除 ${selected.size} 条病历记录？\n此操作不可撤销。`))
      return;
    try {
      await apiDelete(`/api/medical-records`, { ids: Array.from(selected) });
      setSelected(new Set());
      await loadRecords();
    } catch (e: any) {
      alert(`批量删除失败: ${e.message}`);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">病历管理</h1>
        {selected.size > 0 && (
          <button onClick={deleteBatch} className="btn-ghost text-xs text-red-600 border-red-300">
            批量删除 ({selected.size})
          </button>
        )}
      </div>
      {err && (
        <div className="card border-red-300 bg-red-50 text-red-700 text-sm">加载失败：{err}</div>
      )}

      <div className="card">
        <h2 className="font-semibold mb-2">病历记录（{records.length}）</h2>
        {loading ? (
          <div className="text-sm text-slate-400 py-4 text-center">加载中…</div>
        ) : records.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-slate-500 border-b">
                <tr>
                  <th className="py-2 w-8">
                    <input type="checkbox" checked={selected.size === records.length && records.length > 0}
                      onChange={toggleAll} className="accent-brand-500" />
                  </th>
                  <th className="py-2">病历号</th>
                  <th>就诊号</th>
                  <th>患者</th>
                  <th>创建时间</th>
                  <th>内容摘要</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {records.map((r: any) => (
                  <tr key={r.id} className="border-b last:border-0 hover:bg-slate-50">
                    <td className="py-2">
                      <input type="checkbox" checked={selected.has(r.id)}
                        onChange={() => toggle(r.id)} className="accent-brand-500" />
                    </td>
                    <td className="py-2 font-mono text-xs">{r.id}</td>
                    <td className="font-mono text-xs">{r.encounter_id}</td>
                    <td className="font-mono text-xs">{r.patient_id || "—"}</td>
                    <td className="text-slate-500 text-xs">{r.created_at?.slice(0, 19)}</td>
                    <td className="text-xs text-slate-600 max-w-xs truncate">
                      {r.raw_text || JSON.stringify(r.content || {}).slice(0, 60)}
                    </td>
                    <td className="flex items-center gap-2">
                      <Link href={`/medical-records/${r.id}`} className="text-brand-600 text-xs hover:underline">
                        查看 →
                      </Link>
                      <button onClick={() => deleteOne(r.id)}
                        className="text-red-500 text-xs hover:underline">删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-slate-400">暂无病历记录。请在就诊中保存病历后查看。</p>
        )}
      </div>

      <div className="card text-sm text-slate-600">
        <p>病历从就诊中保存并提交后，会通过 MockHIS Adapter 记录到此处（MockHIS：未接入真实 HIS）。</p>
        <p className="text-xs text-slate-400 mt-1">
          病历保存为幂等操作 — 重复提交不会覆盖已编辑病历。
        </p>
      </div>
    </div>
  );
}
