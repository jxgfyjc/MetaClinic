"use client";

import { useState, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { apiPost } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [doctorId, setDoctorId] = useState("dr-001");
  const [password, setPassword] = useState("demo123");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // If already logged in, redirect home
    (async () => {
      try {
        const r = await fetch(`${process.env.NEXT_PUBLIC_API_BASE || ""}/api/auth/me`, {
          credentials: "include",
        });
        if (r.ok) {
          const redirect = searchParams.get("redirect") || "/";
          router.replace(redirect);
        }
      } catch {
        // not logged in — show form
      }
    })();
  }, [router, searchParams]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await apiPost("/api/auth/login", { doctor_id: doctorId, password });
      const redirect = searchParams.get("redirect") || "/";
      router.replace(redirect);
      router.refresh();
    } catch (err: any) {
      setError(err.message || "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      <header className="bg-brand-600 text-white px-4 h-12 flex items-center">
        <span className="font-semibold text-sm">🏥 象元 AI 诊疗</span>
      </header>
      <main className="flex-1 flex items-center justify-center p-4">
        <div className="w-full max-w-sm bg-white rounded-lg shadow-md border border-slate-200 p-6">
          <h1 className="text-lg font-semibold text-slate-800 mb-1">医生登录</h1>
          <p className="text-xs text-slate-500 mb-4">
            请使用医生工号登录。患者请通过二维码进入问诊页。
          </p>
          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label className="block text-xs text-slate-600 mb-1">医生工号</label>
              <input
                type="text"
                value={doctorId}
                onChange={(e) => setDoctorId(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-brand-500"
                placeholder="dr-001"
                required
              />
            </div>
            <div>
              <label className="block text-xs text-slate-600 mb-1">密码</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-brand-500"
                placeholder="demo123"
                required
              />
            </div>
            {error && (
              <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded px-2 py-1">
                {error}
              </p>
            )}
            <button
              type="submit"
              disabled={loading}
              className="w-full bg-brand-600 text-white text-sm font-medium py-2 rounded hover:bg-brand-700 disabled:opacity-50"
            >
              {loading ? "登录中…" : "登录"}
            </button>
          </form>
          <div className="mt-4 text-xs text-slate-400 space-y-1">
            <p>演示账号（默认密码 demo123）：</p>
            <p>· dr-001 张明（全科/中西医结合）</p>
            <p>· dr-002 李华（中医科）</p>
            <p>· dr-003 王芳（全科）</p>
          </div>
          <div className="mt-4 pt-3 border-t border-slate-100 text-center">
            <Link href="/intake" className="text-xs text-brand-600 hover:underline">
              患者问诊入口 →
            </Link>
          </div>
        </div>
      </main>
      <footer className="text-center text-xs text-slate-400 py-3">
        私有化部署 · 患者数据严禁出网
      </footer>
    </div>
  );
}
