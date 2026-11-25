import { apiGet } from "@/lib/api";
import type { Encounter, Patient } from "@/lib/api";
import AnalyzePanel from "./AnalyzePanel";

export const dynamic = "force-dynamic";

// updatev2_3 §3b: no synthetic pid fallback. If encounter load fails, show
// the error explicitly instead of silently rendering with null data.
export default async function EncounterDetail({ params }: { params: Promise<{ eid: string }> }) {
  const { eid } = await params;
  let encounter: Encounter | null = null;
  let patient: Patient | null = null;
  let encErr = "";

  try {
    encounter = await apiGet<Encounter>(`/api/encounters/${eid}`);
  } catch (e: any) {
    encErr = e.message;
  }

  const pid = encounter?.patient_id || "";
  if (pid) {
    try { patient = await apiGet<Patient>(`/api/patients/${pid}`); } catch {}
  }

  if (encErr && !encounter) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold">就诊 {eid}</h1>
        <div className="card border-red-300 bg-red-50 text-red-700 text-sm">
          加载就诊失败：{encErr}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-xl font-semibold">就诊 {eid}</h1>
        {encounter?.source === "patient" && (
          <span className="text-xs px-2 py-0.5 rounded bg-blue-100 text-blue-700">患者自主问诊</span>
        )}
        {encounter?.source === "doctor" && (
          <span className="text-xs px-2 py-0.5 rounded bg-purple-100 text-purple-700">医生直接采集</span>
        )}
        {encounter?.case_type && <span className="badge-muted">{encounter.case_type}</span>}
        {encounter?.status && <span className="badge-muted">{encounter.status}</span>}
      </div>
      <AnalyzePanel eid={eid} encounter={encounter} patient={patient} />
    </div>
  );
}
