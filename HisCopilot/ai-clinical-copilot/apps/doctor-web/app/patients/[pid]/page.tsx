import { apiGet } from "@/lib/api";
import type { Patient, Encounter } from "@/lib/api";
import Link from "next/link";

export const dynamic = "force-dynamic";

// updatev2_3 §4a: Patient detail page — per-person long-term profile +
// historical encounter timeline. Each encounter row has explicit
// "查看本次就诊" and "新建就诊" actions.
export default async function PatientDetailPage({
  params,
}: {
  params: Promise<{ pid: string }>;
}) {
  const { pid } = await params;
  let patient: Patient | null = null;
  let encounters: Encounter[] = [];
  let err = "";
  try {
    patient = await apiGet<Patient>(`/api/patients/${pid}`);
  } catch (e: any) {
    err = e.message;
  }
  try {
    const all = await apiGet<Encounter[]>("/api/encounters");
    encounters = all.filter((e) => e.patient_id === pid);
  } catch {}

  if (err && !patient) {
    return (
      <div className="space-y-4">
        <Link href="/patients" className="text-sm text-brand-600 hover:underline">← 返回患者列表</Link>
        <div className="card border-red-300 bg-red-50 text-red-700 text-sm">
          加载患者失败：{err}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link href="/patients" className="text-sm text-brand-600 hover:underline">← 患者列表</Link>
          <h1 className="text-xl font-semibold">{patient?.name || "未填写"}</h1>
        </div>
        <Link href={`/encounters/new?pid=${pid}`} className="btn-primary text-sm">+ 新建就诊</Link>
      </div>

      {/* Patient profile */}
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

      {/* Encounter timeline */}
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
