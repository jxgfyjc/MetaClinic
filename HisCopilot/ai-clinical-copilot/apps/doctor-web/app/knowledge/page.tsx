"use client";

import { useState } from "react";
import { apiGet } from "@/lib/api";

export default function KnowledgePage() {
  const [q, setQ] = useState("糖尿病");
  const [results, setResults] = useState<any[]>([]);
  const [err, setErr] = useState("");

  async function search() {
    setErr(""); setResults([]);
    try {
      const r = await apiGet(`/api/knowledge/search?q=${encodeURIComponent(q)}&top_k=6`);
      setResults(r.results || []);
    } catch (e: any) { setErr(e.message); }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">知识库检索（RAG）</h1>
      <div className="card flex gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)}
               className="flex-1 border rounded px-3 py-1.5 text-sm"
               placeholder="检索词，如：糖尿病 / 失眠 / 高血压" />
        <button onClick={search} className="btn-primary">检索</button>
      </div>
      {err && <div className="card border-red-300 bg-red-50 text-red-700 text-sm">{err}</div>}
      <div className="space-y-2">
        {results.map((r, i) => (
          <div key={i} className="card">
            <div className="flex items-center justify-between">
              <span className="font-medium">{r.title}</span>
              <span className="badge-muted">{r.trust_tier}</span>
            </div>
            <div className="text-xs text-slate-500 mt-1">{r.source_type} · {r.domain} · 相关度 {r.relevance_score?.toFixed?.(3) ?? r.relevance_score}</div>
            <div className="text-sm mt-1 line-clamp-3">{r.excerpt}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
