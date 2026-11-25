"use client";
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { AppShell } from "@/components/AppShell";
import { Spinner } from "@/components/ui";

// All staff pages live under this ONE layout: the shell (sidebar + header) mounts once and persists across
// navigations — switching pages only swaps the content area, so there is no full-page reload and no lost state.
export default function StaffLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const path = usePathname();
  useEffect(() => {
    if (!loading && !user) router.replace(`/login?next=${encodeURIComponent(path)}`);
  }, [loading, user, router, path]);
  useEffect(() => {
    if (user?.must_change_password && path !== "/account") router.replace("/account?force=1");
  }, [user, path, router]);
  if (loading || !user) return <div className="grid h-screen place-items-center text-ink-400"><Spinner size={22} /></div>;
  return <AppShell>{children}</AppShell>;
}
