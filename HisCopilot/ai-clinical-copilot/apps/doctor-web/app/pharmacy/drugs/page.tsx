"use client";

import { useState, useEffect, useRef } from "react";
import { apiGet, apiPost, apiPatch, apiUpload, API_BASE } from "@/lib/api";

// V1.2 §3: Drug library — search / filter / CRUD / CSV import
// DB-first via /api/drugs and /api/drugs/search. Doctor can add/modify/delete.
// CSV import via POST /api/drugs/import (multipart).

type Drug = {
  id: string; name: string; generic_name?: string;
  category: string; form?: string; strength?: string;
  unit?: string; usage?: string; source?: string;
  hospital_dict?: any;
};

export default function DrugsPage() {
  const [drugs, setDrugs] = useState<Drug[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<"all" | "western" | "tcm-herb">("all");
  const [msg, setMsg] = useState("");

  // edit modal
  const [editing, setEditing] = useState<Drug | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function loadDrugs() {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (q) { params.set("q", q); }
      if (cat !== "all") params.set("category", cat);
      const r = await apiGet<{ drugs: Drug[] }>(`/api/drugs/search?${params}`);
      setDrugs(r.drugs || []);
    } catch (e: any) {
      setMsg(`加载失败: ${e.message}`);
    } finally { setLoading(false); }
  }

  useEffect(() => { loadDrugs(); }, []);
  // debounce search
  useEffect(() => {
    const t = setTimeout(loadDrugs, 300);
    return () => clearTimeout(t);
  }, [q, cat]);

  async function deleteDrug(id: string) {
    if (!confirm(`确认删除药品 ${id}?`)) return;
    try {
      await fetch(`${API_BASE}/api/drugs/${id}`, { method: "DELETE" });
      setMsg("✅ 已删除");
      loadDrugs();
    } catch (e: any) { setMsg(`删除失败: ${e.message}`); }
  }

  async function importCsv(file: File) {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await apiUpload<{ imported: number; skipped: number }>(`/api/drugs/import`, fd);
      setMsg(`✅ 导入 ${r.imported} 条，跳过 ${r.skipped} 条`);
      loadDrugs();
    } catch (e: any) { setMsg(`导入失败: ${e.message}`); }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">药品库管理</h1>
        <div className="flex gap-2">
          <button onClick={() => fileRef.current?.click()} className="btn-ghost text-xs">CSV 导入</button>
          <input ref={fileRef} type="file" accept=".csv" className="hidden"
            onChange={e => { const f = e.target.files?.[0]; if (f) importCsv(f); }} />
          <button onClick={() => { setShowAdd(true); setEditing(null); }} className="btn-primary text-xs">+ 新增药品</button>
        </div>
      </div>

      {msg && <div className="text-sm px-3 py-2 rounded bg-slate-100">{msg}</div>}

      {/* Search + filter */}
      <div className="card flex gap-2 items-center">
        <input value={q} onChange={e => setQ(e.target.value)}
          className="flex-1 border rounded px-3 py-2 text-sm"
          placeholder="搜索药品名称或通用名…" />
        <select value={cat} onChange={e => setCat(e.target.value as any)}
          className="border rounded px-3 py-2 text-sm">
          <option value="all">全部分类</option>
          <option value="western">西药/成药</option>
          <option value="tcm-herb">中药饮片</option>
        </select>
        <span className="text-xs text-slate-400">{drugs.length} 条</span>
      </div>

      {/* Table */}
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500 border-b">
            <tr>
              <th className="py-2">编码</th>
              <th>药品名称</th>
              <th>通用名</th>
              <th>分类</th>
              <th>剂型</th>
              <th>规格</th>
              <th>单位</th>
              <th>用法</th>
              <th>来源</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={10} className="py-4 text-center text-slate-400">加载中…</td></tr>
            ) : drugs.length === 0 ? (
              <tr><td colSpan={10} className="py-4 text-center text-slate-400">无匹配药品</td></tr>
            ) : drugs.map(d => (
              <tr key={d.id} className="border-b last:border-0 hover:bg-slate-50">
                <td className="py-2 font-mono text-xs">{d.id}</td>
                <td className="font-medium">{d.name}</td>
                <td className="text-slate-500">{d.generic_name || "—"}</td>
                <td><span className="badge-muted">{d.category === "tcm-herb" ? "中药" : "西药"}</span></td>
                <td>{d.form || "—"}</td>
                <td>{d.strength || "—"}</td>
                <td>{d.unit || "—"}</td>
                <td className="text-xs">{d.usage || "—"}</td>
                <td className="text-xs">{d.source || "demo"}</td>
                <td className="text-xs">
                  <button onClick={() => { setEditing(d); setShowAdd(true); }}
                    className="text-brand-600 hover:underline mr-2">编辑</button>
                  <button onClick={() => deleteDrug(d.id)}
                    className="text-red-500 hover:underline">删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Add/Edit modal */}
      {showAdd && (
        <DrugEditModal
          drug={editing}
          onClose={() => setShowAdd(false)}
          onSaved={() => { setShowAdd(false); loadDrugs(); }}
        />
      )}

      <div className="card text-sm text-slate-600">
        <p>药品库支持搜索、新增、编辑、删除及 CSV 批量导入。数据写入数据库（Drug 表）。</p>
        <p className="text-xs text-slate-400 mt-1">
          CSV 格式：name,generic_name,spec,form,unit,usage,category,source,his_code
        </p>
      </div>
    </div>
  );
}

function DrugEditModal({ drug, onClose, onSaved }: {
  drug: Drug | null; onClose: () => void; onSaved: () => void;
}) {
  const [form, setForm] = useState<Drug>(
    drug || { id: "", name: "", generic_name: "", category: "western",
      form: "", strength: "", unit: "", usage: "", source: "manual" }
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function save() {
    setBusy(true); setErr("");
    try {
      if (drug?.id) {
        await apiPatch(`/api/drugs/${drug.id}`, form);
      } else {
        await apiPost(`/api/drugs`, form);
      }
      onSaved();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50"
      onClick={onClose}>
      <div className="bg-white rounded-lg shadow-lg p-4 w-full max-w-md space-y-2"
        onClick={e => e.stopPropagation()}>
        <h2 className="font-semibold">{drug ? "编辑药品" : "新增药品"}</h2>
        {drug?.id && <div className="text-xs text-slate-400">ID: {drug.id}</div>}
        <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
          className="w-full border rounded px-2 py-1.5 text-sm" placeholder="药品名称 *" />
        <input value={form.generic_name} onChange={e => setForm({ ...form, generic_name: e.target.value })}
          className="w-full border rounded px-2 py-1.5 text-sm" placeholder="通用名" />
        <div className="flex gap-2">
          <select value={form.category} onChange={e => setForm({ ...form, category: e.target.value })}
            className="border rounded px-2 py-1.5 text-sm flex-1">
            <option value="western">西药/成药</option>
            <option value="tcm-herb">中药饮片</option>
          </select>
          <input value={form.form || ""} onChange={e => setForm({ ...form, form: e.target.value })}
            className="border rounded px-2 py-1.5 text-sm flex-1" placeholder="剂型" />
        </div>
        <div className="flex gap-2">
          <input value={form.strength || ""} onChange={e => setForm({ ...form, strength: e.target.value })}
            className="border rounded px-2 py-1.5 text-sm flex-1" placeholder="规格" />
          <input value={form.unit || ""} onChange={e => setForm({ ...form, unit: e.target.value })}
            className="border rounded px-2 py-1.5 text-sm flex-1" placeholder="单位" />
        </div>
        <input value={form.usage || ""} onChange={e => setForm({ ...form, usage: e.target.value })}
          className="w-full border rounded px-2 py-1.5 text-sm" placeholder="用法（如：口服 qd）" />
        {err && <div className="text-xs text-red-600">{err}</div>}
        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="btn-ghost text-sm">取消</button>
          <button onClick={save} disabled={busy || !form.name} className="btn-primary text-sm">
            {busy ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
