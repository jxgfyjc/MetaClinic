"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { apiGet } from "@/lib/api";

// updatev2_2 §6: Final medical record detail page.
// Reopenable without re-running AI. Export to PDF via window.print().

export default function MedicalRecordDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [record, setRecord] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!id) return;
    apiGet<any>(`/api/medical-records/${id}`)
      .then(setRecord)
      .catch((e) => setError(e.message));
  }, [id]);

  if (error) {
    return (
      <div className="card text-red-700">
        加载失败：{error}
      </div>
    );
  }
  if (!record) {
    return <div className="text-sm text-slate-400">加载中…</div>;
  }

  const c: any = record.content || {};
  const patient = c.patient_info || {};
  const enc = c.encounter_info || {};
  const diagnoses = c.confirmed_diagnoses || [];
  const treatment = c.treatment_plan || {};
  const rxDetails = c.prescription_details || {};
  const rx = rxDetails.structured_json?.prescription || treatment.prescription || {};
  const therapyItems = c.therapy_items || [];
  const sign = c.doctor_sign || {};
  const followup = c.followup_plan || {};
  const auditTrail = c.audit_trail || [];

  return (
    <div className="space-y-4 print-area">
      <div className="flex items-center justify-between print:hidden">
        <h1 className="text-xl font-semibold">诊疗记录</h1>
        <div className="flex items-center gap-2">
          <span className={`badge ${record.status === "finalized" ? "badge-ok" : "badge-warn"}`}>
            {record.status === "finalized" ? "已定稿" : "草稿"}
          </span>
          <button onClick={() => window.print()} className="btn-primary text-xs">
            🖨 导出 PDF
          </button>
        </div>
      </div>

      {/* Header for print */}
      <div className="card print-card">
        <div className="text-center mb-4 pb-3 border-b border-slate-200">
          <h1 className="text-lg font-bold">象元 AI 诊疗 · 诊疗记录</h1>
          <p className="text-xs text-slate-500">
            记录号: {record.id} · {record.status === "finalized" ? "已定稿" : "草稿（未定稿）"}
          </p>
        </div>

        {/* Patient info */}
        <section className="mb-4">
          <h2 className="font-semibold text-sm border-b border-slate-200 pb-1 mb-2">患者信息</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
            <div><b>姓名：</b>{patient.name || "—"}</div>
            <div><b>性别：</b>{patient.sex || patient.gender || "—"}</div>
            <div><b>年龄：</b>{patient.age || "—"}岁</div>
            <div><b>电话：</b>{patient.phone || "—"}</div>
            <div><b>就诊号：</b>{enc.id || record.encounter_id}</div>
            <div><b>主诉：</b>{c.chief_complaint || enc.chief_complaint || "—"}</div>
            <div><b>就诊类型：</b>{enc.case_type || "—"}</div>
            <div><b>就诊时间：</b>{enc.created_at?.slice(0, 19) || record.created_at?.slice(0, 19)}</div>
          </div>
        </section>

        {/* History */}
        {(c.raw_text || c.history) && (
          <section className="mb-4">
            <h2 className="font-semibold text-sm border-b border-slate-200 pb-1 mb-2">现病史</h2>
            <p className="text-sm whitespace-pre-wrap">{c.raw_text || JSON.stringify(c.history)}</p>
          </section>
        )}

        {/* Allergies & meds */}
        {(c.allergies?.length > 0 || c.medications?.length > 0) && (
          <section className="mb-4">
            <h2 className="font-semibold text-sm border-b border-slate-200 pb-1 mb-2">过敏史与用药</h2>
            {c.allergies?.length > 0 && (
              <div className="text-sm">
                <b>过敏史：</b>
                {c.allergies.map((a: any, i: number) => (
                  <span key={i} className="badge-err mr-1">{a.allergen || a}</span>
                ))}
              </div>
            )}
            {c.medications?.length > 0 && (
              <div className="text-sm mt-1">
                <b>当前用药：</b>
                {c.medications.map((m: any, i: number) => (
                  <span key={i} className="badge-muted mr-1">{m.name} {m.dose} {m.freq}</span>
                ))}
              </div>
            )}
          </section>
        )}

        {/* Labs */}
        {c.labs?.length > 0 && (
          <section className="mb-4">
            <h2 className="font-semibold text-sm border-b border-slate-200 pb-1 mb-2">检验结果</h2>
            <div className="text-sm">
              {c.labs.map((l: any, i: number) => (
                <span key={i} className={`mr-2 ${l.flag === "HIGH" ? "text-amber-600" : ""}`}>
                  {l.item}={l.value}{l.unit} ({l.ref})
                </span>
              ))}
            </div>
          </section>
        )}

        {/* Diagnoses */}
        {diagnoses.length > 0 && (
          <section className="mb-4">
            <h2 className="font-semibold text-sm border-b border-slate-200 pb-1 mb-2">诊断</h2>
            <div className="space-y-1 text-sm">
              {diagnoses.map((d: any, i: number) => (
                <div key={i}>
                  <span className="font-medium">{d.module === "western" ? "西医诊断" : "中医证型"}：</span>
                  {d.western_diagnosis || d.tcm_syndrome}
                  <span className="text-xs text-slate-500 ml-2">
                    ({d.review_status})
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Prescription */}
        {rx && (rx.western_medications?.length > 0 || rx.herbal_drafts?.length > 0) && (
          <section className="mb-4">
            <h2 className="font-semibold text-sm border-b border-slate-200 pb-1 mb-2">处方</h2>
            {rx.western_medications?.length > 0 && (
              <div className="text-sm mb-1">
                <b>西药/成药：</b>
                <ul className="list-disc pl-5 text-xs mt-1">
                  {rx.western_medications.map((m: any, i: number) => (
                    <li key={i}>{m.medication} {m.dose} {m.frequency} {m.usage} — {m.reason}</li>
                  ))}
                </ul>
              </div>
            )}
            {rx.herbal_drafts?.length > 0 && (
              <div className="text-sm mb-1">
                <b>中药：</b>
                {rx.herbal_drafts.map((h: any, i: number) => (
                  <div key={i} className="text-xs mt-1">
                    <span className="badge-muted mr-1">{h.formula_type}</span>
                    {h.name}：{h.herbs?.join("、")}
                  </div>
                ))}
              </div>
            )}
            {rx.safety_notes?.length > 0 && (
              <div className="text-xs text-red-700 mt-1">
                <b>安全提示：</b>{rx.safety_notes.join("；")}
              </div>
            )}
          </section>
        )}

        {/* Therapy items */}
        {therapyItems.length > 0 && (
          <section className="mb-4">
            <h2 className="font-semibold text-sm border-b border-slate-200 pb-1 mb-2">理疗方案</h2>
            <ul className="text-sm space-y-1">
              {therapyItems.map((t: any, i: number) => {
                const content = t.status === "EDITED" ? { ...t.original_content, ...t.edited_content } : t.original_content;
                return (
                  <li key={i} className="text-xs">
                    <span className="font-medium">{content?.name}</span>
                    <span className="badge-muted ml-1">{content?.category}</span>
                    <span className="ml-1 text-slate-500">({t.status})</span>
                    {content?.manipulation && <span className="ml-2">手法：{content.manipulation}</span>}
                    {content?.frequency && <span className="ml-2">频率：{content.frequency}</span>}
                    {content?.course && <span className="ml-2">疗程：{content.course}</span>}
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        {/* Doctor signature */}
        <section className="mb-4">
          <h2 className="font-semibold text-sm border-b border-slate-200 pb-1 mb-2">签字信息</h2>
          <div className="text-sm">
            <b>主治医师：</b>{sign.doctor_name || record.doctor_id || "—"}
            {record.finalized_at && (
              <span className="ml-4 text-xs text-slate-500">
                定稿时间: {record.finalized_at.slice(0, 19)}
              </span>
            )}
            {sign.signature_id && (
              <span className="ml-4 text-xs text-slate-500">
                签名ID: {sign.signature_id}
              </span>
            )}
          </div>
        </section>

        {/* Followup */}
        {followup.id && (
          <section className="mb-4">
            <h2 className="font-semibold text-sm border-b border-slate-200 pb-1 mb-2">随访计划</h2>
            <div className="text-sm">
              {followup.days}天后随访 · {followup.questions?.length || 0} 个问题
            </div>
          </section>
        )}

        {/* Audit trail */}
        {auditTrail.length > 0 && (
          <section className="print:hidden">
            <h2 className="font-semibold text-sm border-b border-slate-200 pb-1 mb-2">操作审计</h2>
            <ul className="text-xs text-slate-500 space-y-0.5">
              {auditTrail.slice(0, 15).map((a: any, i: number) => (
                <li key={i}>
                  {a.ts?.slice(0, 19)} · {a.actor} · {a.action}
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="text-center text-xs text-slate-400 mt-4 pt-3 border-t border-slate-200">
          象元 AI 诊疗 · 私有化部署 · 本记录由执业医师签字确认 · AI 输出仅供参考
        </div>
      </div>
    </div>
  );
}
