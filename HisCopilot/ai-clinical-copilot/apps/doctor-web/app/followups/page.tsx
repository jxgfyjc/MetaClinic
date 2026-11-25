"use client";

import { useState, useEffect } from "react";
import { apiGet, apiPost, apiDelete, API_BASE } from "@/lib/api";
import Link from "next/link";

// V1.2 §4: Bidirectional followup — doctor creates plan → patient fills
// → results enter doctor queue → doctor replies → patient sees reply.
// SMS/WeChat channels must show "未配置".

type FollowupPlan = {
  id: string; encounter_id: string; patient_id: string;
  followup_type: string; scheduled_date: string;
  status: string; questions?: any[]; created_at?: string;
};

type FollowupResponse = {
  id: string; followup_plan_id: string; patient_id: string;
  answers: any; risk_level?: string; risk_reason?: string;
  doctor_reply?: string; doctor_reply_at?: string;
  created_at?: string;
};

export default function FollowupsPage() {
  const [tab, setTab] = useState<"plans" | "pending">("plans");
  const [plans, setPlans] = useState<FollowupPlan[]>([]);
  const [pending, setPending] = useState<FollowupResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  async function loadPlans() {
    setLoading(true); setErr("");
    try {
      const r = await apiGet<{ followups: FollowupPlan[] } | FollowupPlan[]>("/api/followups");
      setPlans(Array.isArray(r) ? r : r.followups || []);
    } catch (e: any) { setErr(e.message); }
    finally { setLoading(false); }
  }

  async function loadPending() {
    setLoading(true); setErr("");
    try {
      const r = await apiGet<{ responses: FollowupResponse[] }>("/api/followups/responses/pending");
      setPending(r.responses || []);
    } catch (e: any) { setErr(e.message); }
    finally { setLoading(false); }
  }

  useEffect(() => { if (tab === "plans") loadPlans(); else loadPending(); }, [tab]);

  function toggle(id: string) {
    setSelected(prev => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  }
  function toggleAll() {
    setSelected(prev =>
      prev.size === plans.length ? new Set() : new Set(plans.map(p => p.id)));
  }

  async function deleteOne(id: string) {
    if (!confirm(`确定删除随访计划「${id}」？\n此操作不可撤销，该计划的随访回复与风险事件将一并删除。`))
      return;
    try {
      await apiDelete(`/api/followups/${id}`);
      await loadPlans();
      setSelected(prev => { const n = new Set(prev); n.delete(id); return n; });
    } catch (e: any) {
      alert(`删除失败: ${e.message}`);
    }
  }

  async function deleteBatch() {
    if (selected.size === 0) return;
    if (!confirm(`确定批量删除 ${selected.size} 条随访计划？\n此操作不可撤销，所有关联数据将一并删除。`))
      return;
    try {
      await apiDelete(`/api/followups`, { ids: Array.from(selected) });
      setSelected(new Set());
      await loadPlans();
    } catch (e: any) {
      alert(`批量删除失败: ${e.message}`);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">随访管理</h1>
        <div className="flex items-center gap-2">
          {tab === "plans" && selected.size > 0 && (
            <button onClick={deleteBatch} className="btn-ghost text-xs text-red-600 border-red-300">
              批量删除 ({selected.size})
            </button>
          )}
          <Link href="/encounters/enc-003" className="btn-primary text-xs">从就诊创建随访</Link>
        </div>
      </div>

      {/* SMS / WeChat status — V1.2 §4 must show 未配置 */}
      <div className="card text-sm flex gap-4">
        <span>短信通知：<b className="text-slate-400">未配置</b></span>
        <span>微信通知：<b className="text-slate-400">未配置</b></span>
        <span className="text-xs text-slate-400 ml-auto">私有化部署，通知渠道未接入</span>
      </div>

      {/* Tabs */}
      <div className="flex gap-2">
        <button onClick={() => setTab("plans")}
          className={`px-3 py-1.5 rounded text-sm ${tab === "plans" ? "bg-brand-600 text-white" : "bg-slate-100"}`}>
          随访计划 ({plans.length})
        </button>
        <button onClick={() => setTab("pending")}
          className={`px-3 py-1.5 rounded text-sm ${tab === "pending" ? "bg-brand-600 text-white" : "bg-slate-100"}`}>
          待回复队列 ({pending.length})
        </button>
      </div>

      {err && <div className="card border-red-300 bg-red-50 text-red-700 text-sm">{err}</div>}

      {loading && <div className="text-sm text-slate-400">加载中…</div>}

      {/* Plans tab */}
      {tab === "plans" && !loading && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500 border-b">
              <tr>
                <th className="py-2 w-8">
                  <input type="checkbox" checked={selected.size === plans.length && plans.length > 0}
                    onChange={toggleAll} className="accent-brand-500" />
                </th>
                <th className="py-2">随访ID</th><th>就诊号</th><th>患者</th>
                <th>类型</th><th>计划日期</th><th>状态</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {plans.length === 0 ? (
                <tr><td colSpan={8} className="py-4 text-center text-slate-400">暂无随访计划</td></tr>
              ) : plans.map(f => (
                <tr key={f.id} className="border-b last:border-0 hover:bg-slate-50">
                  <td className="py-2">
                    <input type="checkbox" checked={selected.has(f.id)}
                      onChange={() => toggle(f.id)} className="accent-brand-500" />
                  </td>
                  <td className="py-2 font-mono text-xs">{f.id}</td>
                  <td className="font-mono text-xs">{f.encounter_id}</td>
                  <td>{f.patient_id}</td>
                  <td>{f.followup_type || "常规"}</td>
                  <td>{f.scheduled_date || f.created_at?.slice(0, 10) || "—"}</td>
                  <td><span className={f.status === "completed" ? "badge-ok" : "badge-warn"}>{f.status || "pending"}</span></td>
                  <td className="flex items-center gap-2">
                    <Link href="/patient" className="text-brand-600 text-xs hover:underline">患者端 →</Link>
                    <button onClick={() => deleteOne(f.id)}
                      className="text-red-500 text-xs hover:underline">删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pending responses tab — doctor reply queue */}
      {tab === "pending" && !loading && (
        <div className="space-y-3">
          {pending.length === 0 ? (
            <div className="card text-center py-8 text-slate-400">暂无待回复</div>
          ) : pending.map(r => (
            <PendingResponseCard key={r.id} response={r} onReplied={loadPending} />
          ))}
        </div>
      )}
    </div>
  );
}

function PendingResponseCard({ response, onReplied }: {
  response: FollowupResponse; onReplied: () => void;
}) {
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function sendReply() {
    setBusy(true); setErr("");
    try {
      await apiPost(`/api/followups/responses/${response.id}/reply`, {
        doctor_reply: reply,
      });
      onReplied();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  const answersText = typeof response.answers === "string"
    ? response.answers
    : response.answers?.text || JSON.stringify(response.answers, null, 2);

  return (
    <div className="card space-y-2">
      <div className="flex items-center justify-between">
        <span className="font-medium text-sm">回复 {response.id}</span>
        {response.risk_level && (
          <span className={response.risk_level === "HIGH" ? "badge-err" : "badge-warn"}>
            风险: {response.risk_level}
          </span>
        )}
      </div>
      <div className="text-xs text-slate-500">
        患者: {response.patient_id} · 计划: {response.followup_plan_id} · {response.created_at?.slice(0, 19)}
      </div>
      <div className="text-sm bg-slate-50 rounded p-2 whitespace-pre-wrap">{answersText}</div>
      {response.risk_reason && (
        <div className="text-xs text-amber-700">风险原因：{response.risk_reason}</div>
      )}
      {response.doctor_reply ? (
        <div className="text-xs text-green-700 bg-green-50 rounded p-2">
          已回复：{response.doctor_reply} ({response.doctor_reply_at?.slice(0, 19)})
        </div>
      ) : (
        <div className="space-y-1">
          <textarea value={reply} onChange={e => setReply(e.target.value)}
            className="w-full border rounded p-2 text-sm min-h-16"
            placeholder="医生回复内容（将返回患者端）…" />
          {err && <div className="text-xs text-red-600">{err}</div>}
          <button onClick={sendReply} disabled={busy || !reply}
            className="btn-primary text-xs">
            {busy ? "发送中…" : "回复患者"}
          </button>
        </div>
      )}
    </div>
  );
}
