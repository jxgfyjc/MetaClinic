"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import Sidebar from "./Sidebar";
import Breadcrumb from "./Breadcrumb";
import { getDoctorInfo, type DoctorInfo } from "@/lib/auth";

// AppShell — conditional layout: patient intake/followup pages have no sidebar
// updatev2_2 §1c: strict patient/doctor isolation.
//   /intake, /patient → patient layout (no doctor sidebar, no doctor header)
//   everything else  → doctor layout (requires doctor_token via middleware)
// Uses mounted guard to avoid SSR/CSR hydration mismatch.

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [mounted, setMounted] = useState(false);
  const [doctor, setDoctor] = useState<DoctorInfo | null>(null);
  useEffect(() => setMounted(true), []);
  useEffect(() => {
    if (!mounted) return;
    // Only fetch doctor info on doctor-facing routes
    const isPatient =
      pathname?.startsWith("/intake") || pathname?.startsWith("/patient");
    if (!isPatient) {
      getDoctorInfo().then(setDoctor);
    }
  }, [mounted, pathname]);

  // Before mount: render a stable shell to match SSR output.
  // After mount: apply the patient/doctor layout split.
  const isPatient =
    mounted &&
    (pathname?.startsWith("/intake") || pathname?.startsWith("/patient"));

  if (isPatient) {
    // Patient-facing: no sidebar, no doctor header, no link to doctor side
    return (
      <div className="min-h-screen bg-slate-50">
        {/* Minimal patient header — no "医生端→" link (§1 isolation) */}
        <header className="bg-brand-600 text-white px-4 h-12 flex items-center justify-between sticky top-0 z-10">
          <span className="font-semibold text-sm">🏥 象元 AI 诊疗 · 患者问诊</span>
        </header>
        <main className="max-w-md mx-auto p-4">{children}</main>
        <footer className="text-center text-xs text-slate-400 py-4">
          私有化部署 · 数据严禁出网 · AI 输出仅供医生参考
        </footer>
      </div>
    );
  }

  // Doctor-facing: full sidebar + header
  const doctorLabel = doctor
    ? `${doctor.id} ${doctor.name}`
    : "未登录";
  return (
    <div className="flex">
      <Sidebar />
      <div className="flex-1 min-w-0">
        <header className="sticky top-0 z-10 bg-white border-b border-slate-200 px-4 h-12 flex items-center gap-4">
          <span className="font-semibold text-brand-700 text-sm">象元 AI 诊疗</span>
          <nav className="flex items-center gap-3 text-xs text-slate-500 ml-auto">
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-green-500" />
              AI 引擎在线
            </span>
            <span>·</span>
            <span className="text-amber-600">HIS: MockHIS（未接入真实 HIS）</span>
            <span>·</span>
            <span>{doctorLabel}</span>
          </nav>
        </header>
        <main className="p-4 lg:p-6 max-w-7xl mx-auto">
          <Breadcrumb />
          {children}
        </main>
        <footer className="p-4 text-center text-xs text-slate-400 border-t border-slate-200">
          私有化部署 · 患者数据严禁出网 · 所有 AI 输出仅供医生参考，最终由执业医师签字
        </footer>
      </div>
    </div>
  );
}
