"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiGet } from "@/lib/api";

// 医生工作台 — Client Component（updatev2_3：与今日接诊/患者列表一致，
// 避免每次进入工作台都走 SSR 整页渲染，确保侧栏固定、仅右侧内容切换）。
export default function HomePage() {
  const [patients, setPatients] = useState<any[]>([]);
  const [encounters, setEncounters] = useState<any[]>([]);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [pendingRx, setPendingRx] = useState<any[]>([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      apiGet<any>("/api/patients").catch((e: any) => { setErr(e.message); return []; }),
      apiGet<any>("/api/encounters").catch(() => []),
      apiGet<any>("/api/alerts").catch(() => ({ alerts: [] })),
      apiGet<any>("/api/prescriptions/pending").catch(() => []),
    ]).then(([p, e, a, rx]) => {
      setPatients(Array.isArray(p) ? p : (p?.items || []));
      setEncounters(Array.isArray(e) ? e : (e?.items || []));
      setAlerts(a?.alerts || []);
      setPendingRx(Array.isArray(rx) ? rx : (rx?.items || []));
    }).finally(() => setLoading(false));
  }, []);

  const pendingEnc = encounters.filter((e: any) => e.status === "PENDING" || e.status === "IN_PROGRESS");
  const completedEnc = encounters.filter((e: any) => e.status === "SUBMITTED" || e.status === "COMPLETED");

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">象元 AI 诊疗</h1>
        <p className="text-sm text-slate-500 mt-1">
          中西医结合门诊临床副驾 · 私有化部署。AI 输出仅供医生参考，
          处方须执业医师签字后提交。
        </p>
      </div>

      {err && (
        <div className="card border-red-300 bg-red-50 text-red-700 text-sm">
          后端不可达：{err}（AI 失败按 instruct #30 降级，不阻塞 HIS）
        </div>
      )}

      {/* V1.0: 双入口卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Link href="/intake" className="card border-2 border-brand-500 hover:border-brand-600 hover:shadow-md transition p-6">
          <div className="flex items-start gap-3">
            <span className="text-3xl">🙋</span>
            <div className="flex-1">
              <div className="font-semibold text-lg text-brand-700">患者自主问诊</div>
              <p className="text-sm text-slate-600 mt-1">
                患者自助填写病情、语音录入、上传检验报告（OCR）和舌象，提交后进入医生待接诊队列
              </p>
              <div className="text-xs text-brand-600 mt-2 font-medium">进入问诊 →</div>
            </div>
          </div>
        </Link>
        <Link href="/encounters/new" className="card border-2 border-slate-400 hover:border-brand-500 hover:shadow-md transition p-6">
          <div className="flex items-start gap-3">
            <span className="text-3xl">👨‍⚕️</span>
            <div className="flex-1">
              <div className="font-semibold text-lg text-slate-800">医生直接接诊</div>
              <p className="text-sm text-slate-600 mt-1">
                医生搜索/新建患者，录入主诉病史（支持语音），补充检验/舌象，自动生成病历草稿并进入诊疗室
              </p>
              <div className="text-xs text-slate-700 mt-2 font-medium">新建就诊 →</div>
            </div>
          </div>
        </Link>
      </div>

      {/* 今日待办概览 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Link href="/encounters" className="card hover:border-brand-500 transition">
          <div className="text-2xl font-bold text-brand-600">{loading ? "…" : encounters.length}</div>
          <div className="text-xs text-slate-600">就诊病例总数</div>
          <div className="text-[10px] text-slate-400 mt-1">待处理 {pendingEnc.length} · 已完成 {completedEnc.length}</div>
        </Link>
        <Link href="/patients" className="card hover:border-brand-500 transition">
          <div className="text-2xl font-bold text-brand-600">{loading ? "…" : patients.length}</div>
          <div className="text-xs text-slate-600">在管患者</div>
        </Link>
        <Link href="/alerts" className="card hover:border-red-400 transition">
          <div className="text-2xl font-bold text-red-600">{loading ? "…" : alerts.length}</div>
          <div className="text-xs text-slate-600">待处理预警</div>
        </Link>
        <Link href="/encounters" className="card hover:border-brand-500 transition">
          <div className="text-2xl font-bold text-brand-600">→</div>
          <div className="text-xs text-slate-600">继续诊疗</div>
          <div className="text-[10px] text-slate-400 mt-1">就诊列表</div>
        </Link>
      </div>

      {/* 待接诊队列 */}
      {pendingEnc.length > 0 && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold">待接诊队列</h2>
            <Link href="/encounters?status=PENDING" className="text-xs text-brand-600 hover:underline">查看全部 →</Link>
          </div>
          <div className="space-y-2">
            {pendingEnc.slice(0, 6).map((e: any) => (
              <Link key={e.id} href={`/encounters/${e.id}`} className="flex items-center justify-between p-2 rounded hover:bg-slate-50 transition text-sm">
                <div className="flex items-center gap-2">
                  {e.source === "patient" && <span className="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">患者提交</span>}
                  {e.source === "doctor" && <span className="text-xs px-1.5 py-0.5 rounded bg-purple-100 text-purple-700">医生采集</span>}
                  <span className="font-mono text-xs text-slate-500">{e.id}</span>
                  <span className="font-medium">{e.chief_complaint}</span>
                </div>
                <span className="badge-warn">{e.status}</span>
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* 诊疗闭环引导 */}
      <div className="card bg-brand-50/60 border-brand-200">
        <h2 className="font-semibold mb-2 text-brand-700">诊疗闭环工作流</h2>
        <div className="text-xs text-slate-600 flex items-center gap-1 flex-wrap">
          <span>扫码接诊</span>→<span>AI 分析</span>→<span>采纳诊断</span>→
          <span>生成治疗方案</span>→<span>编辑处方（克数/药味）</span>→
          <span>签字提交</span>→<span>随访</span>
        </div>
        <p className="text-xs text-slate-400 mt-1">
          处方与理疗方案在医生采纳诊断后按诊断生成（合并一次调用，约 30-60s）。
        </p>
      </div>

      {/* 待处理处方 */}
      {pendingRx.length > 0 && (
        <div className="card border-l-4 border-l-amber-400">
          <div className="flex items-center justify-between mb-2">
            <h2 className="font-semibold">待编辑/签字处方</h2>
            <span className="badge-warn text-xs">{pendingRx.length}</span>
          </div>
          <div className="space-y-1">
            {pendingRx.slice(0, 6).map((rx: any) => (
              <Link key={rx.id} href={`/encounters/${rx.encounter_id}`}
                className="flex items-center justify-between p-2 rounded hover:bg-slate-50 transition text-sm">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-slate-500">{rx.id}</span>
                  {rx.target_diagnosis_id && <span className="text-xs text-brand-600">按诊断</span>}
                </div>
                <span className="badge-muted text-xs">{rx.status}</span>
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* 典型病例 */}
      <div className="card">
        <h2 className="font-semibold mb-2">最近就诊</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm">
          {encounters.slice(0, 5).map((e: any) => (
            <Link key={e.id} href={`/encounters/${e.id}`} className="p-2 rounded hover:bg-slate-50 flex items-center justify-between">
              <span className="font-mono text-xs text-slate-500">{e.id}</span>
              <span className="truncate ml-2">{e.chief_complaint || "未填写主诉"}</span>
              <span className="text-xs text-slate-400 ml-2">{e.status}</span>
            </Link>
          ))}
          {encounters.length === 0 && (
            <div className="text-xs text-slate-400">暂无就诊记录</div>
          )}
        </div>
      </div>
    </div>
  );
}
