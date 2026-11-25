"use client";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { StatusChip } from "@/components/ui";
import { fmtTime } from "@/lib/api";

export type Enc = { id: string; encounter_no: string; status: string; source: string; chief_complaint: string | null; created_at: string;
  patient: { id: string; name: string; gender: string | null; age: number | null; is_sample: boolean }; doctor: { name: string } | null;
  analysis_status: string | null; pending_diagnoses: number; open_prescriptions: number; failed_artifacts: number };

export function EncRow({ e }: { e: Enc }) {
  return (
    <li>
      <Link href={`/encounters/${e.id}`} className="flex items-center gap-3 px-4 py-3 transition hover:bg-brand-50/40">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-ink-100 text-sm font-semibold text-ink-600">{e.patient.name.slice(0, 1)}</div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5"><span className="font-medium text-ink-900">{e.patient.name}</span><span className="text-xs text-ink-400">{e.patient.gender || "性别未填"} · {e.patient.age != null ? `${e.patient.age}岁` : "年龄未填"}</span>
            {e.source === "patient_intake" && <span className="tag-brand">患者自填</span>}{e.patient.is_sample && <span className="tag-sim">示例数据</span>}</div>
          <div className="truncate text-[13px] text-ink-500">{e.chief_complaint || <span className="text-ink-300">主诉未填写</span>}</div>
        </div>
        <div className="hidden flex-col items-end gap-1 sm:flex">
          <div className="flex items-center gap-1.5">
            {e.analysis_status === "running" || e.analysis_status === "queued" ? <StatusChip s="running" label="AI 分析中" /> : e.analysis_status === "failed" ? <StatusChip s="failed" label="AI 分析失败" /> : null}
            {e.pending_diagnoses > 0 && <span className="tag-warn">{e.pending_diagnoses} 个诊断待审</span>}{e.failed_artifacts > 0 && <span className="tag-err">{e.failed_artifacts} 项资料处理失败</span>}
          </div>
          <span className="text-[11px] text-ink-300">{fmtTime(e.created_at)} · {e.encounter_no}</span>
        </div>
        <StatusChip s={e.status} /><ArrowRight size={14} className="text-ink-200" />
      </Link>
    </li>
  );
}
