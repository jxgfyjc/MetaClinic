"use client";
import { SWRConfig } from "swr";
import { AuthProvider } from "@/lib/auth";
import { ConfirmProvider, ToastProvider } from "@/components/ui";
import { fetcher } from "@/lib/api";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SWRConfig value={{ fetcher, revalidateOnFocus: false, shouldRetryOnError: false, dedupingInterval: 2000 }}>
      <ToastProvider><ConfirmProvider><AuthProvider>{children}</AuthProvider></ConfirmProvider></ToastProvider>
    </SWRConfig>
  );
}
