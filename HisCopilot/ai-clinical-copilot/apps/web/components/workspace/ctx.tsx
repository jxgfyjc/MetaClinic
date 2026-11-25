"use client";
import { createContext, useContext, type MutableRefObject } from "react";

export type Bundle = {
  encounter: any; patient: any; artifacts: any[]; labs: any[]; analysis: any | null; diagnoses: any[]; plan: any | null;
  prescriptions: any[]; voided_prescriptions: number;
  staleness: { analysis_stale: boolean; plan_stale_inputs: boolean; plan_stale_diagnoses: boolean; inputs_version: number };
  active_jobs: { id: string; kind: string; status: string; progress: number; message: string }[];
  previous_encounter: { id: string; encounter_no: string; created_at: string; chief_complaint: string | null } | null;
  can: { edit_facts: boolean; claim: boolean };
};
export type Step = "facts" | "ai" | "plan" | "rx" | "record" | "followup";
export type Ws = {
  eid: string; b: Bundle; reload: () => Promise<any>; readOnly: boolean;
  /** may THIS user act on this visit at all (own/unassigned visit, not cancelled)? Unlike `readOnly` it stays true after finalization — amendments, follow-up and release happen then. */
  mayAct: boolean;
  ensureStarted: () => Promise<void>; go: (s: Step) => void;
  hpiAppend: MutableRefObject<((t: string) => void) | null>;
};
export const WsCtx = createContext<Ws>(null as any);
export const useWs = () => useContext(WsCtx);

export const isBusy = (b?: Bundle | null) => !!b && (
  b.active_jobs.length > 0 || b.artifacts.some((a) => a.proc_status === "pending" || a.proc_status === "processing") ||
  ["queued", "running"].includes(b.analysis?.status) || b.plan?.status === "generating");
