"use client";

import { useState, useEffect } from "react";
import { apiPost, apiGet, API_BASE } from "@/lib/api";

// V1.2 §4: Patient followup end — submit answers + see doctor reply + timeline
// SMS/WeChat must show "未配置"

type TimelineItem = {
  type: string; label: string; time: string; detail?: string;
  doctor_reply?: string;
};

export default function PatientPage() {
  const [fid, setFid] = useState("followup-005");
  const [answers, setAnswers] = useState("头晕加重，胸闷，视物模糊");
  const [risk, setRisk] = useState<any>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  // timeline
  const [patientId, setPatientId] = useState("p-005");
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [timelineLoaded, setTimelineLoaded] = useState(false);

  async function submit() {
    setErr(""); setRisk(null); setBusy(true);
    try {
      const r = await apiPost(`/api/followups/${fid}/response`, {
        followup_plan_id: fid, patient_id: patientId,
        answers: { text: answers },
      });
      setRisk(r.risk);
      // refresh timeline
      loadTimeline();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  async function loadTimeline() {
    try {
      const r = await apiGet<{ timeline: TimelineItem[]; sms: string; wechat: string }>(
        `/api/patients/${patientId}/timeline`);
      setTimeline(r.timeline || []);
      setTimelineLoaded(true);
    } catch {
      setTimelineLoaded(true);
    }
  }

  useEffect(() => { loadTimeline(); }, [patientId]);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">患者随访端</h1>

      {/* SMS/WeChat status */}
      <div className="card text-sm flex gap-4">
        <span>短信通知：<b className="text-slate-400">未配置</b></span>
        <span>微信通知：<b className="text-slate-400">未配置</b></span>
      </div>

      {/* Submit answer */}
      <div className="card text-sm">
        <div className="flex items-center justify-between mb-1">
          <span>随访计划：<b>{fid}</b></span>
          <input value={patientId} onChange={e => setPatientId(e.target.value)}
            className="border rounded px-2 py-0.5 text-xs w-24" placeholder="患者ID" />
        </div>
        <p className="text-xs text-slate-500 mt-1">请如实描述近期症状，系统将自动评估风险等级。</p>
      </div>
      <div className="card">
        <textarea value={answers} onChange={e => setAnswers(e.target.value)}
          className="w-full border rounded p-2 text-sm min-h-24"
          placeholder="例如：头晕加重，胸闷，夜间不能平卧…" />
        <button onClick={submit} disabled={busy} className="btn-primary mt-2">
          {busy ? "评估中…" : "提交随访回答"}
        </button>
      </div>
      {err && <div className="card border-red-300 bg-red-50 text-red-700 text-sm">{err}</div>}
      {risk && (
        <div className="card">
          <div className="flex items-center gap-2">
            <span className="font-semibold">风险等级</span>
            <span className={risk.risk_level === "HIGH" ? "badge-err"
              : risk.risk_level === "MEDIUM" ? "badge-warn" : "badge-ok"}>
              {risk.risk_level}
            </span>
          </div>
          <div className="text-xs text-slate-600 mt-1">{risk.reason}</div>
          {risk.recommended_action && (
            <div className="text-sm mt-1 text-red-700">{risk.recommended_action}</div>
          )}
        </div>
      )}

      {/* Timeline — shows doctor replies flowing back to patient */}
      <div className="card">
        <h2 className="font-semibold mb-2">就诊时间线</h2>
        {!timelineLoaded ? (
          <div className="text-sm text-slate-400">加载中…</div>
        ) : timeline.length === 0 ? (
          <div className="text-sm text-slate-400">暂无记录</div>
        ) : (
          <div className="space-y-2">
            {timeline.map((t, i) => (
              <div key={i} className="flex gap-3 text-sm border-l-2 border-slate-200 pl-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="badge-muted text-xs">{t.type}</span>
                    <span className="font-medium">{t.label}</span>
                    <span className="text-xs text-slate-400 ml-auto">{t.time?.slice(0, 19)}</span>
                  </div>
                  {t.detail && <div className="text-xs text-slate-600 mt-0.5">{t.detail}</div>}
                  {t.doctor_reply && (
                    <div className="text-xs text-green-700 bg-green-50 rounded p-1.5 mt-1">
                      👨‍⚕️ 医生回复：{t.doctor_reply}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
