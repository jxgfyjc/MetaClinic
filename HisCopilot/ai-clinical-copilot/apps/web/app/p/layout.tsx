"use client";
// Patient-facing pages (mobile first). No staff chrome, no cookies: access is by a scoped token that arrives in the URL
// FRAGMENT (`#t=…`, never sent to servers in requests/referrers), is moved to sessionStorage and removed from the address bar.
import type { ReactNode } from "react";
import { Logo } from "@/components/Logo";

export default function PatientLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-gradient-to-b from-brand-50/70 to-ink-50">
      <header className="mx-auto flex max-w-xl items-center gap-2.5 px-4 pb-1 pt-5"><Logo size={30} /><span className="text-[15px] font-semibold tracking-tight text-ink-900">MetaClinic</span><span className="ml-auto text-[11px] text-ink-400">患者端</span></header>
      <main className="mx-auto max-w-xl px-4 pb-28 pt-3">{children}</main>
    </div>
  );
}
