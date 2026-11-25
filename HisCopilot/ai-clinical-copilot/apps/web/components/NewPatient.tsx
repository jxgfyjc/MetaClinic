"use client";
import { useState } from "react";
import { Field, Modal } from "@/components/ui";
import { api, errMsg } from "@/lib/api";

export function NewPatient({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: (id: string) => void }) {
  const [f, setF] = useState({ name: "", gender: "", age: "", phone: "", allergies: "" });
  const [err, setErr] = useState<Record<string, string>>({}); const [busy, setBusy] = useState(false); const [dups, setDups] = useState<any[]>([]);
  const set = (k: string, v: string) => setF((s) => ({ ...s, [k]: v }));
  const submit = async (allow = false) => {
    const e: Record<string, string> = {};
    if (!f.name.trim()) e.name = "请填写姓名";
    if (f.phone && !/^1[3-9]\d{9}$/.test(f.phone.replace(/\s|-/g, ""))) e.phone = "请输入 11 位手机号";
    if (f.age && (+f.age < 0 || +f.age > 130)) e.age = "年龄不合理";
    setErr(e); if (Object.keys(e).length) return;
    setBusy(true);
    try {
      const r = await api.post("/patients", { name: f.name.trim(), gender: f.gender, age: f.age ? +f.age : null, phone: f.phone, allow_duplicate: allow,
        allergies: f.allergies.split(/[、,，;；\s]+/).filter(Boolean).map((a) => ({ allergen: a })) });
      if (r.duplicates?.length && !allow && r.duplicates.every((d: any) => d.id !== r.patient.id)) { setDups(r.duplicates); }
      onDone(r.patient.id);
    } catch (x) { setErr({ _: errMsg(x) }); } finally { setBusy(false); }
  };
  return (
    <Modal open={open} onClose={onClose} title="新建患者档案" footer={<><button className="btn-secondary" onClick={onClose}>取消</button><button className="btn-primary" onClick={() => submit()} disabled={busy}>创建</button></>}>
      <div className="grid gap-3.5 sm:grid-cols-2">
        <Field label="姓名" required error={err.name}><input className={`input ${err.name ? "input-err" : ""}`} value={f.name} onChange={(e) => set("name", e.target.value)} autoFocus /></Field>
        <Field label="手机" error={err.phone} hint="同手机号同姓名将自动合并为同一患者"><input className={`input ${err.phone ? "input-err" : ""}`} inputMode="numeric" value={f.phone} onChange={(e) => set("phone", e.target.value)} /></Field>
        <Field label="性别"><select className="input" value={f.gender} onChange={(e) => set("gender", e.target.value)}><option value="">未填写</option><option>男</option><option>女</option></select></Field>
        <Field label="年龄" error={err.age}><input className="input" type="number" value={f.age} onChange={(e) => set("age", e.target.value)} /></Field>
        <div className="sm:col-span-2"><Field label="过敏史" hint="多个用顿号分隔，如：青霉素、海鲜。不确定请留空，接诊时再核实。"><input className="input" value={f.allergies} onChange={(e) => set("allergies", e.target.value)} /></Field></div>
      </div>
      {err._ && <div className="mt-3 rounded-lg bg-danger-50 p-2.5 text-sm text-danger-600">{err._}</div>}
    </Modal>
  );
}
