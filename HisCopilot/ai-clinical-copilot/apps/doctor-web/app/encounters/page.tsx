"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { API_BASE, apiDelete } from "@/lib/api";

// updatev2_3 §4b: Today's encounters list — honors ?pid= query param.
// Per-encounter-event entries; one patient can have multiple encounters.
// Status filter tabs + new encounter button + source badge.

type StatusFilter = "ALL" | "PENDING" | "IN_PROGRESS" | "COMPLETED";

const TABS: { key: StatusFilter; label: string; apiStatus?: string }[] = [
  { key: "ALL", label: "全部" },
  { key: "PENDING", label: "待接诊", apiStatus: "PENDING" },
  { key: "IN_PROGRESS", label: "进行中", apiStatus: "IN_PROGRESS" },
  { key: "COMPLETED", label: "已完成" },
];

export default function EncountersPage() {
  const searchParams = useSearchParams();
  const pidFilter = searchParams.get("pid") || "";
  const [encs, setEncs] = useState<any[]>([]);
  const [filter, setFilter] = useState<StatusFilter>("ALL");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    loadEncounters(filter);
  }, [filter, pidFilter]);

  async function loadEncounters(f: StatusFilter) {
    setLoading(true);
    try {
      const tab = TABS.find(t => t.key === f);
      const qs = tab?.apiStatus ? `?status=${tab.apiStatus}` : "";
      const r = await fetch(`${API_BASE}/api/encounters${qs}`, { credentials: "include" });
      if (r.status === 401) { window.location.href = "/login"; return; }
      const data = await r.json();
      let list = Array.isArray(data) ? data : (data?.items || []);
      if (f === "COMPLETED") {
        list = list.filter((e: any) => ["SUBMITTED", "SIGNED", "COMPLETED"].includes(e.status));
      }
      // §4b: filter by patient_id when pid param is present
      if (pidFilter) {
        list = list.filter((e: any) => e.patient_id === pidFilter);
      }
      setEncs(list);
    } catch {
      setEncs([]);
    } finally {
      setLoading(false);
    }
  }

  const title = pidFilter
    ? `就诊列表（按患者筛选）`
    : "今日接诊";

  function toggle(id: string) {
    setSelected(prev => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  }
  function toggleAll() {
    setSelected(prev =>
      prev.size === encs.length ? new Set() : new Set(encs.map((e:any) => e.id)));
  }

  async function deleteOne(id: string) {
    if (!confirm(`确定删除就诊记录「${id}」？\n此操作不可撤销，该就诊下的诊断、处方、病历、随访等关联数据将一并删除。`))
      return;
    try {
      await apiDelete(`/api/encounters/${id}`);
      await loadEncounters(filter);
      setSelected(prev => { const n = new Set(prev); n.delete(id); return n; });
    } catch (e: any) {
      alert(`删除失败: ${e.message}`);
    }
  }

  async function deleteBatch() {
    if (selected.size === 0) return;
    if (!confirm(`确定批量删除 ${selected.size} 条就诊记录？\n此操作不可撤销，所有关联数据将一并删除。`))
      return;
    try {
      await apiDelete(`/api/encounters`, { ids: Array.from(selected) });
      setSelected(new Set());
      await loadEncounters(filter);
    } catch (e: any) {
      alert(`批量删除失败: ${e.message}`);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{title}</h1>
          {pidFilter && (
            <span className="text-xs text-slate-500">患者ID: {pidFilter}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {selected.size > 0 && (
            <button onClick={deleteBatch} className="btn-ghost text-xs text-red-600 border-red-300">
              批量删除 ({selected.size})
            </button>
          )}
          <Link href="/encounters/new" className="btn-primary text-sm">+ 新建就诊</Link>
        </div>
      </div>

      {/* Status filter tabs */}
      <div className="flex gap-1 border-b border-slate-200">
        {TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => setFilter(tab.key)}
            className={`px-3 py-1.5 text-sm border-b-2 transition
              ${filter === tab.key
                ? "border-brand-600 text-brand-700 font-medium"
                : "border-transparent text-slate-500 hover:text-slate-700"}`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="text-sm text-slate-400">加载中…</div>
      ) : encs.length === 0 ? (
        <div className="card text-center py-8 text-sm text-slate-400">
          暂无就诊记录
          <div className="mt-2">
            <Link href="/encounters/new" className="btn-primary text-sm">+ 新建就诊</Link>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {/* select-all row */}
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <input type="checkbox" checked={selected.size === encs.length && encs.length > 0}
              onChange={toggleAll} className="accent-brand-500" />
            <span>全选 ({encs.length})</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {encs.map((e: any) => (
            <div key={e.id} className={`card hover:border-brand-500 ${selected.has(e.id) ? "border-brand-500 bg-brand-50/30" : ""}`}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <input type="checkbox" checked={selected.has(e.id)}
                    onChange={() => toggle(e.id)} className="accent-brand-500" />
                  <Link href={`/encounters/${e.id}`} className="font-mono text-xs text-slate-500 hover:underline">{e.id}</Link>
                </div>
                <div className="flex items-center gap-1">
                  {e.source === "patient" && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">患者提交</span>
                  )}
                  {e.source === "doctor" && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-100 text-purple-700">医生采集</span>
                  )}
                  <span className="badge-muted">{e.status}</span>
                </div>
              </div>
              <Link href={`/encounters/${e.id}`} className="block mt-1 font-medium hover:underline">{e.chief_complaint || "（未填写主诉）"}</Link>
              <div className="text-xs text-slate-500 mt-1">
                患者 {e.patient_id} · 医生 {e.doctor_id || "未填写"}
                {e.case_type && <span className="text-slate-400"> · {e.case_type}</span>}
              </div>
              <div className="mt-2 flex justify-end">
                <button onClick={() => deleteOne(e.id)}
                  className="text-red-500 text-xs hover:underline">删除</button>
              </div>
            </div>
          ))}
          </div>
        </div>
      )}
    </div>
  );
}
