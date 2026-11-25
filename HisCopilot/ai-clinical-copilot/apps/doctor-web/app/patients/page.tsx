"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiGet, apiDelete, type Patient, type Encounter } from "@/lib/api";

// updatev2_3 §1: 患者列表 — Client Component, card-grid layout mirroring
// 今日接诊 (same fetch pattern + visual style), so navigation between the
// two lists feels seamless. Patient detail opens inline (no full-page nav).

export default function PatientsPage() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [openPid, setOpenPid] = useState<string | null>(null);

  async function loadPatients() {
    setLoading(true); setErr("");
    try {
      const data = await apiGet<Patient[] | { items: Patient[] }>("/api/patients");
      setPatients(Array.isArray(data) ? data : (data?.items || []));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadPatients(); }, []);

  function toggle(id: string) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function toggleAll() {
    setSelected(prev =>
      prev.size === patients.length ? new Set() : new Set(patients.map(p => p.id)));
  }

  async function deleteOne(id: string, name: string) {
    if (!confirm(`确定删除患者「${name || id}」？\n此操作不可撤销，该患者的所有就诊、诊断、处方、病历、随访等关联数据将一并删除。`))
      return;
    try {
      await apiDelete(`/api/patients/${id}`);
      await loadPatients();
      setSelected(prev => { const n = new Set(prev); n.delete(id); return n; });
    } catch (e: any) {
      alert(`删除失败: ${e.message}`);
    }
  }

  async function deleteBatch() {
    if (selected.size === 0) return;
    if (!confirm(`确定批量删除 ${selected.size} 条患者记录？\n此操作不可撤销，所有关联数据将一并删除。`))
      return;
    try {
      await apiDelete(`/api/patients`, { ids: Array.from(selected) });
      setSelected(new Set());
      await loadPatients();
    } catch (e: any) {
      alert(`批量删除失败: ${e.message}`);
    }
  }

  if (openPid) {
    return <PatientDetailInline pid={openPid} onBack={() => setOpenPid(null)} />;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">患者列表</h1>
        {selected.size > 0 && (
          <button onClick={deleteBatch} className="btn-ghost text-xs text-red-600 border-red-300">
            批量删除 ({selected.size})
          </button>
        )}
      </div>

      {err && <div className="card border-red-300 bg-red-50 text-red-700 text-sm">加载失败：{err}</div>}

      {loading ? (
        <div className="text-sm text-slate-400">加载中…</div>
      ) : patients.length === 0 ? (
        <div className="card text-center py-8 text-sm text-slate-400">暂无患者记录</div>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <input type="checkbox" checked={selected.size === patients.length && patients.length > 0}
              onChange={toggleAll} className="accent-brand-500" />
            <span>全选 ({patients.length})</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {patients.map((p) => (
              <div key={p.id} className={`card hover:border-brand-500 ${selected.has(p.id) ? "border-brand-500 bg-brand-50/30" : ""}`}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <input type="checkbox" checked={selected.has(p.id)}
                      onChange={() => toggle(p.id)} className="accent-brand-500" />
                    <button onClick={() => setOpenPid(p.id)}
                      className="font-medium text-left hover:underline">{p.name || "未填写"}</button>
                    <span className="font-mono text-xs text-slate-500">{p.id}</span>
                  </div>
                  <div className="flex items-center gap-1">
                    {(p.allergies || []).length > 0
                      ? p.allergies!.map((a, i) => (
                          <span key={i} className="badge-warn text-[10px]">{a.allergen}</span>
                        ))
                      : <span className="badge-muted text-[10px]">无过敏</span>}
                  </div>
                </div>
                <div className="mt-1 text-xs text-slate-500">
                  {p.gender || "未填写"} · {p.age || "未填写"}岁
                  {p.phone && <span className="text-slate-400"> · {p.phone}</span>}
                </div>
                <div className="mt-2 flex justify-end gap-2">
                  <button onClick={() => setOpenPid(p.id)}
                    className="text-brand-600 text-xs hover:underline">详情 →</button>
                  <button onClick={() => deleteOne(p.id, p.name)}
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

// Inline patient detail — renders in-place, same visual style as the list.
// "查看本次就诊" navigates to /encounters/{eid} (the encounter workspace).
function PatientDetailInline({ pid, onBack }: { pid: string; onBack: () => void }) {
  const [patient, setPatient] = useState<Patient | null>(null);
  const [encounters, setEncounters] = useState<Encounter[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    setLoading(true); setErr("");
    Promise.all([
      apiGet<Patient>(`/api/patients/${pid}`).catch(() => null),
      apiGet<Encounter[]>("/api/encounters").catch(() => []),
    ]).then(([p, all]) => {
      setPatient(p);
      setEncounters((all as Encounter[]).filter((e) => e.patient_id === pid));
    }).catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  }, [pid]);

  if (loading) return <div className="text-sm text-slate-400 py-4 text-center">加载中…</div>;
  if (err && !patient) return (
    <div className="space-y-4">
      <button onClick={onBack} className="text-sm text-brand-600 hover:underline">← 返回患者列表</button>
      <div className="card border-red-300 bg-red-50 text-red-700 text-sm">加载患者失败：{err}</div>
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="text-sm text-brand-600 hover:underline">← 患者列表</button>
          <h1 className="text-xl font-semibold">{patient?.name || "未填写"}</h1>
        </div>
        <Link href={`/encounters/new?pid=${pid}`} className="btn-primary text-sm">+ 新建就诊</Link>
      </div>

      <div className="card border-l-4 border-l-brand-500">
        <h2 className="font-semibold mb-2 text-brand-700">患者档案</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
          <div><span className="text-slate-500">姓名：</span>{patient?.name || "未填写"}</div>
          <div><span className="text-slate-500">性别：</span>{patient?.gender || "未填写"}</div>
          <div><span className="text-slate-500">年龄：</span>{patient?.age || "未填写"}</div>
          <div><span className="text-slate-500">手机：</span>{patient?.phone || "未填写"}</div>
          <div><span className="text-slate-500">医保：</span>{(patient as any)?.insurance || "未填写"}</div>
          <div className="col-span-2">
            <span className="text-slate-500">过敏史：</span>
            {(patient?.allergies || []).length > 0
              ? patient!.allergies!.map((a, i) => (
                  <span key={i} className="badge-warn mr-1">{a.allergen}/{a.severity}</span>
                ))
              : <span className="text-slate-400">未填写</span>}
          </div>
        </div>
      </div>

      <div className="card">
        <h2 className="font-semibold mb-3">就诊历史（{encounters.length}）</h2>
        {encounters.length === 0 ? (
          <div className="text-center py-6 text-sm text-slate-400">
            暂无就诊记录
            <div className="mt-2">
              <Link href={`/encounters/new?pid=${pid}`} className="btn-primary text-sm">+ 新建就诊</Link>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {encounters.map((e, i) => (
              <div key={e.id} className="border rounded p-3 hover:border-brand-400">
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-slate-400">#{encounters.length - i}</span>
                    <span className="font-mono text-xs text-slate-500">{e.id}</span>
                    <span className="badge-muted">{e.status}</span>
                    {e.source === "patient" && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">患者提交</span>
                    )}
                    {e.source === "doctor" && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-100 text-purple-700">医生采集</span>
                    )}
                  </div>
                  <div className="text-xs text-slate-500">
                    {(e as any).created_at?.slice(0, 19) || "—"}
                  </div>
                </div>
                <div className="mt-1 text-sm font-medium">
                  {e.chief_complaint || "（未填写主诉）"}
                </div>
                {(e as any).case_type && (
                  <div className="text-xs text-slate-400 mt-0.5">{(e as any).case_type}</div>
                )}
                <div className="flex gap-2 mt-2">
                  <Link href={`/encounters/${e.id}`} className="btn-primary text-xs">
                    查看本次就诊 →
                  </Link>
                  <Link href={`/encounters/new?pid=${pid}`} className="btn-ghost text-xs">
                    新建就诊
                  </Link>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
