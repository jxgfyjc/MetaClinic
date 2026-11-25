"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";

// 理疗康复治疗项目 — Client Component（侧栏固定、右侧内容切换）
export default function TherapyPage() {
  const [programs, setPrograms] = useState<any[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([
      apiGet<any>("/api/therapy-programs").catch((e: any) => { setErr(e.message); return []; }),
      apiGet<any>("/api/therapy-programs/categories").catch(() => ({ categories: [] })),
    ]).then(([p, c]) => {
      setPrograms(Array.isArray(p) ? p : []);
      setCategories(c?.categories || []);
    }).finally(() => setLoading(false));
  }, []);

  // 按 category 分组
  const grouped: Record<string, any[]> = {};
  for (const p of programs) {
    const c = p.category || "其他";
    if (!grouped[c]) grouped[c] = [];
    grouped[c].push(p);
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">理疗康复治疗项目</h1>
        <button className="btn-primary" disabled>新增项目</button>
      </div>
      {err && (
        <div className="card border-red-300 bg-red-50 text-red-700 text-sm">加载失败：{err}</div>
      )}

      {/* 分类标签 */}
      {categories.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {categories.map((c) => (
            <span key={c} className="badge-muted">{c}</span>
          ))}
        </div>
      )}

      {loading ? (
        <div className="text-sm text-slate-400 py-4 text-center">加载中…</div>
      ) : (
        <>
          {/* 按分类展示 */}
          {Object.entries(grouped).map(([cat, items]) => (
            <div key={cat}>
              <h2 className="font-semibold text-sm text-slate-600 mb-2">{cat}</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {items.map((p) => (
                  <div key={p.id} className="card">
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{p.name}</span>
                      <span className={
                        p.source_type === "hospital" ? "badge-ok" :
                        p.source_type === "knowledge" ? "badge-muted" : "badge-warn"
                      }>
                        {p.source_type === "hospital" ? "院内" :
                         p.source_type === "knowledge" ? "知识库" : "AI生成"}
                      </span>
                    </div>
                    {p.hospital_code && (
                      <div className="text-xs font-mono text-slate-400 mt-1">编码：{p.hospital_code}</div>
                    )}
                    <div className="text-sm mt-2">
                      <span className="text-slate-500">适用场景：</span>{p.applicable_scenarios}
                    </div>
                    <div className="text-sm mt-1">
                      <span className="text-slate-500">治疗目标：</span>{p.therapeutic_goal}
                    </div>
                    <div className="text-sm mt-1">
                      <span className="text-slate-500">频次/疗程：</span>{p.frequency_duration}
                    </div>
                    {p.contraindications && (
                      <div className="text-xs text-red-600 mt-2">
                        ⚠️ 禁忌：{p.contraindications}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}

          {programs.length === 0 && !err && (
            <div className="card text-sm text-slate-400">
              暂无治疗项目。请在就诊中运行 AI 分析生成综合治疗方案。
            </div>
          )}
        </>
      )}

      <div className="card text-sm text-slate-600">
        <p>
          治疗项目来源分为三类：
          <span className="badge-ok ml-1">院内</span>（有 hospital_code，已配置）/
          <span className="badge-muted ml-1">知识库</span>（标准方案）/
          <span className="badge-warn ml-1">AI生成</span>（需医生审核）
        </p>
        <p className="text-xs text-slate-400 mt-1">
          未配置收费编码的治疗项目不得伪造（updatev1.md §4）。
        </p>
      </div>
    </div>
  );
}
