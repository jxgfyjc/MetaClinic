"use client";
import { useCallback, useRef, useState } from "react";
import { useToast } from "@/components/ui";
import { ApiError, errMsg } from "./api";

/**
 * Single-flight action runner. Re-entrant calls (double-click, Enter + click) are dropped while one is running, the
 * button shows `busy`, errors become a toast (stale-version conflicts get a friendlier hint + an optional refresh).
 * Returns `undefined` when the action failed or was dropped, so callers can `if (!r) return`.
 */
export function useAction() {
  const toast = useToast();
  const lock = useRef<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const run = useCallback(async <T,>(key: string, fn: () => Promise<T>, o: { ok?: string; onStale?: () => void; onError?: (e: unknown) => void; quiet?: boolean } = {}): Promise<T | undefined> => {
    if (lock.current) {
      // the same action twice (double-click) is silently collapsed; a DIFFERENT action is a real user intent that would otherwise vanish
      if (lock.current !== key) toast("info", "上一个操作还在进行中，请稍候几秒后再试");
      return undefined;
    }
    lock.current = key; setBusy(key); setError(null);
    try {
      const r = await fn();
      if (o.ok) toast("ok", o.ok);
      return r;
    } catch (e) {
      setError(e as Error);
      if (e instanceof ApiError && e.isStale) { toast("warn", "内容已被其他窗口或后台任务更新，已为您刷新，请核对后重试"); o.onStale?.(); }
      else if (!o.quiet) toast("err", errMsg(e));
      o.onError?.(e);
      return undefined;
    } finally { lock.current = null; setBusy(null); }
  }, [toast]);
  return { run, busy, is: (k: string) => busy === k, any: busy !== null, error, clearError: () => setError(null) };
}
