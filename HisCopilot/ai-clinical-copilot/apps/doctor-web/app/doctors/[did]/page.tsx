import { apiGet } from "@/lib/api";
import type { DoctorProfile } from "@/lib/api";
import Link from "next/link";

export const dynamic = "force-dynamic";

// 医师个人经验库 — 展示某医师的风格配置和历史经验
export default async function DoctorDetailPage({
  params,
}: {
  params: Promise<{ did: string }>;
}) {
  const { did } = await params;
  let profile: DoctorProfile | null = null;
  let err = "";
  try {
    profile = await apiGet<DoctorProfile>(`/api/doctors/${did}/profile`);
  } catch (e: any) {
    err = e.message;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">{did} 经验库</h1>
        <Link href="/doctors" className="text-xs text-brand-600 hover:underline">← 返回医师列表</Link>
      </div>
      {err && (
        <div className="card border-red-300 bg-red-50 text-red-700 text-sm">加载失败：{err}</div>
      )}

      {profile && (
        <>
          <div className="card">
            <h2 className="font-semibold mb-3">诊疗风格</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
              <div>
                <div className="text-xs text-slate-500 mb-1">科室</div>
                <div>{profile.dept || "—"}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500 mb-1">擅长领域</div>
                <div>{(profile.specialties || []).join("、") || "—"}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500 mb-1">辨证偏好</div>
                <div className="whitespace-pre-wrap">{profile.syndrome_preference || "—"}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500 mb-1">方剂偏好</div>
                <div className="whitespace-pre-wrap">{profile.formula_preference || "—"}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500 mb-1">用药偏好</div>
                <div className="whitespace-pre-wrap">{profile.herb_preference || "—"}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500 mb-1">非药物偏好</div>
                <div className="whitespace-pre-wrap">{profile.non_drug_preference || "—"}</div>
              </div>
            </div>
          </div>

          <div className="card">
            <h2 className="font-semibold mb-2">经验笔记</h2>
            {(profile.experience_notes || []).length > 0 ? (
              <ul className="text-sm space-y-2">
                {profile.experience_notes.map((note, i) => (
                  <li key={i} className="border-l-2 border-brand-300 pl-3 text-slate-700">{note}</li>
                ))}
              </ul>
            ) : (
              <span className="text-sm text-slate-400">暂无经验笔记</span>
            )}
          </div>

          <div className="card text-sm">
            <Link href={`/encounters/enc-003`} className="text-brand-600 hover:underline">
              → 用此医师风格分析 enc-003 病例
            </Link>
            <p className="text-xs text-slate-400 mt-1">
              在诊疗室中选择此医师，AI 将按上述偏好生成差异化诊疗方案。
            </p>
          </div>
        </>
      )}
    </div>
  );
}
