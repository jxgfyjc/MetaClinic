"use client";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, ArrowLeft, ArrowRight, Camera, CheckCircle2, FileAudio, FlaskConical, Plus, RefreshCw, ScanLine, ShieldCheck, Trash2, X } from "lucide-react";
import { api, ApiError, errMsg, newKey } from "@/lib/api";
import { Spinner } from "@/components/ui";
import { VoiceCapture } from "@/components/VoiceCapture";

const LINK_KEY = "xy_intake_link", SESS_KEY = "xy_intake_session";
type F = { name: string; gender: string; age: string; phone: string; chief_complaint: string; hpi: string; symptoms: string[]; current_meds: { name: string; dose: string; frequency: string }[]; allergies: string[]; no_allergies_confirmed: boolean; chronic: string[]; step: number };
const EMPTY: F = { name: "", gender: "", age: "", phone: "", chief_complaint: "", hpi: "", symptoms: [], current_meds: [], allergies: [], no_allergies_confirmed: false, chronic: [], step: 0 };
const STEPS = ["基本信息", "您的不适", "过敏与既往", "上传资料", "确认提交"];
const ERR: Record<string, string> = { asr_no_speech: "没有听清：请靠近手机重新说一遍，或直接在文字框里输入", asr_unavailable: "语音识别暂时不可用，请改用文字输入，或稍后重试", ocr_unavailable: "图片识别暂时不可用，您仍可提交，医生会直接查看原图" };
const MB = 1024 * 1024;

type Stage = "boot" | "invalid" | "form" | "done";

export default function Intake() {
  const [stage, setStage] = useState<Stage>("boot"); const [reason, setReason] = useState("");
  const [token, setToken] = useState(""); const [info, setInfo] = useState<{ clinic: string; doctor: any } | null>(null);
  const [f, setF] = useState<F>(EMPTY); const [files, setFiles] = useState<any[]>([]); const [loaded, setLoaded] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [err, setErr] = useState<Record<string, string>>({}); const [busy, setBusy] = useState(false); const [consent, setConsent] = useState(false); const [done, setDone] = useState<{ no: string; msg: string } | null>(null);
  const submitKey = useRef(newKey()); const tokRef = useRef(""); tokRef.current = token;
  const set = <K extends keyof F>(k: K, v: F[K]) => setF((s) => ({ ...s, [k]: v }));
  const localUrls = useRef<Record<string, string>>({});

  // ------------------------------------------------------------------ boot: link token → session token → restore draft
  useEffect(() => {
    (async () => {
      try {
        let link = sessionStorage.getItem(LINK_KEY) || "";
        const m = /(?:^#|&)t=([^&]+)/.exec(location.hash);
        if (m) { link = decodeURIComponent(m[1]); sessionStorage.setItem(LINK_KEY, link); sessionStorage.removeItem(SESS_KEY); history.replaceState(null, "", location.pathname); }
        let sess = sessionStorage.getItem(SESS_KEY) || "";
        if (sess) {
          try {
            const d = await api.get("/p/intake/draft", { token: sess });
            setToken(sess); setFiles(d.files || []); setInfo(await api.get("/p/intake/link", { token: link }).catch(() => ({ clinic: "MetaClinic", doctor: null })));
            if (d.submitted) { setDone({ no: d.encounter_no || "", msg: "您的资料已提交，请在候诊区等待医生叫号。" }); setStage("done"); return; }
            restore(d.draft); setStage("form"); setLoaded(true); return;
          } catch (e) { if (!(e instanceof ApiError) || e.status === 0) { setReason("网络不稳定，请检查网络后刷新页面重试。"); setStage("invalid"); return; } sessionStorage.removeItem(SESS_KEY); sess = ""; }
        }
        if (!link) { setReason("没有找到问诊凭证。请重新扫描医生提供的二维码。"); setStage("invalid"); return; }
        const meta = await api.get("/p/intake/link", { token: link });
        const s = await api.post("/p/intake/sessions", {}, { token: link });
        sessionStorage.setItem(SESS_KEY, s.token); setToken(s.token); setInfo(meta); setStage("form"); setLoaded(true);
      } catch (e) {
        setReason(e instanceof ApiError && e.status === 0 ? "网络连接失败，请检查网络后刷新重试。" : e instanceof ApiError && e.status === 429 ? "操作过于频繁，请稍后再试。" : "二维码无效或已过期。请向医护人员重新获取二维码。"); setStage("invalid");
      }
    })();
    return () => { Object.values(localUrls.current).forEach((u) => URL.revokeObjectURL(u)); };
    // eslint-disable-next-line
  }, []);
  const restore = (d: any) => setF({ ...EMPTY, ...d, age: d?.age != null ? String(d.age) : "", symptoms: d?.symptoms || [], allergies: d?.allergies || [], chronic: d?.chronic || [], current_meds: d?.current_meds || [], no_allergies_confirmed: !!d?.no_allergies_confirmed, step: Math.min(4, Number(d?.step) || 0) });

  // ------------------------------------------------------------------ autosave (debounced; never blocks typing)
  const first = useRef(true);
  useEffect(() => {
    if (!loaded || stage !== "form") return;
    if (first.current) { first.current = false; return; }
    setSaveState("saving");
    const t = setTimeout(async () => {
      try { await api.put("/p/intake/draft", { draft: { ...f, age: f.age.trim() } }, { token: tokRef.current }); setSaveState("saved"); }
      catch (e) { setSaveState("error"); if (e instanceof ApiError && e.code === "intake_expired") { sessionStorage.removeItem(SESS_KEY); setReason("问诊已超时，请重新扫描二维码（已填写的内容未能保留）。"); setStage("invalid"); } }
    }, 1200);
    return () => clearTimeout(t);
  }, [f, loaded, stage]);

  // ------------------------------------------------------------------ files
  const refreshFiles = useCallback(async () => { try { const d = await api.get("/p/intake/draft", { token: tokRef.current }); setFiles(d.files || []); } catch { /* transient */ } }, []);
  const working = files.some((x) => x.proc_status === "pending" || x.proc_status === "processing");
  useEffect(() => { if (!working || stage !== "form") return; const t = setInterval(refreshFiles, 2000); return () => clearInterval(t); }, [working, stage, refreshFiles]);
  const upload = (kind: "audio" | "lab_image" | "tongue_image", purpose = "") => async (file: File) => {
    const lim = kind === "audio" ? 25 : 15; setErr((e) => ({ ...e, [kind]: "" }));
    if (file.size > lim * MB) return setErr((e) => ({ ...e, [kind]: `文件太大（${(file.size / MB).toFixed(1)} MB），请控制在 ${lim} MB 以内` }));
    setBusy(true);
    try {
      const fd = new FormData(); fd.append("kind", kind); fd.append("purpose", purpose); fd.append("file", file);
      const r = await api.upload("/p/intake/files", fd, { token });
      if (kind !== "audio") localUrls.current[r.file.id] = URL.createObjectURL(file);
      setFiles((x) => (x.some((y) => y.id === r.file.id) ? x : [...x, r.file]));
      if (!r.created) setErr((e) => ({ ...e, [kind]: "这个文件已经上传过了" }));
    } catch (e) { setErr((x) => ({ ...x, [kind]: errMsg(e) })); } finally { setBusy(false); }
  };
  const retry = async (id: string) => { try { await api.post(`/p/intake/files/${id}/retry`, {}, { token }); await refreshFiles(); } catch (e) { setErr((x) => ({ ...x, files: errMsg(e) })); } };
  const remove = async (id: string) => { try { await api.del(`/p/intake/files/${id}`, { token }); setFiles((x) => x.filter((y) => y.id !== id)); } catch (e) { setErr((x) => ({ ...x, files: errMsg(e) })); } };
  const fix = async (id: string, text: string) => { try { const r = await api.patch(`/p/intake/files/${id}/text`, { text }, { token }); setFiles((x) => x.map((y) => (y.id === id ? r.file : y))); } catch (e) { setErr((x) => ({ ...x, files: errMsg(e) })); } };

  // ------------------------------------------------------------------ validation / submit
  const problems = useMemo(() => {
    const p: Record<string, string> = {};
    if (!f.name.trim()) p.name = "请填写姓名";
    if (f.age.trim() && !(/^\d{1,3}$/.test(f.age.trim()) && +f.age <= 130)) p.age = "请输入正确的年龄";
    if (f.phone.trim() && !/^1[3-9]\d{9}$/.test(f.phone.replace(/[\s-]/g, ""))) p.phone = "请输入 11 位手机号";
    if (f.chief_complaint.trim().length < 2) p.chief_complaint = "请简单写一下哪里不舒服";
    if (!f.allergies.length && !f.no_allergies_confirmed) p.allergy = "请填写过敏史；如果没有，请勾选“没有药物或食物过敏”";
    return p;
  }, [f]);
  const stepOk = (i: number) => (i === 0 ? !problems.name && !problems.age && !problems.phone : i === 1 ? !problems.chief_complaint : i === 2 ? !problems.allergy : true);
  const [showErr, setShowErr] = useState(false);
  const next = () => { if (!stepOk(f.step)) return setShowErr(true); setShowErr(false); set("step", f.step + 1); window.scrollTo({ top: 0 }); };
  const submit = async () => {
    setShowErr(true);
    const bad = [0, 1, 2].find((i) => !stepOk(i)); if (bad != null) { set("step", bad); return; }
    if (!consent) return;
    setBusy(true); setErr((e) => ({ ...e, submit: "" }));
    try {
      const r = await api.post("/p/intake/submit", { name: f.name.trim(), gender: f.gender, age: f.age.trim() ? +f.age : null, phone: f.phone.replace(/[\s-]/g, ""), allergies: f.allergies, no_allergies_confirmed: f.no_allergies_confirmed,
        chronic: f.chronic, chief_complaint: f.chief_complaint.trim(), hpi: f.hpi.trim(), symptoms: f.symptoms, current_meds: f.current_meds.filter((m) => m.name.trim()), consent }, { token, idempotencyKey: submitKey.current });
      setDone({ no: r.encounter_no, msg: r.message }); setStage("done"); window.scrollTo({ top: 0 });
    } catch (e) { setErr((x) => ({ ...x, submit: errMsg(e) })); } finally { setBusy(false); }
  };

  // ------------------------------------------------------------------ views
  if (stage === "boot") return <div className="grid h-[60vh] place-items-center text-ink-300"><Spinner size={26} /></div>;
  if (stage === "invalid") return (
    <div className="card card-pad mt-6 text-center"><div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-amber-50 text-amber-600"><AlertTriangle size={26} /></div>
      <h1 className="mt-4 text-lg font-semibold text-ink-900">无法开始问诊</h1><p className="mt-2 text-sm leading-relaxed text-ink-600">{reason}</p><button className="btn-secondary mt-5" onClick={() => location.reload()}><RefreshCw size={14} />刷新重试</button></div>);
  if (stage === "done" && done) return (
    <div className="card card-pad mt-6 text-center"><div className="mx-auto grid h-16 w-16 place-items-center rounded-full bg-jade-50 text-jade-600"><CheckCircle2 size={34} /></div>
      <h1 className="mt-4 text-xl font-semibold text-ink-950">提交成功</h1>{done.no && <div className="mt-3"><div className="text-xs text-ink-400">您的就诊号</div><div className="mt-1 font-mono text-2xl font-semibold tracking-wider text-brand-700">{done.no}</div></div>}
      <p className="mt-4 text-sm leading-relaxed text-ink-600">{done.msg}</p>
      <ul className="mt-5 space-y-1.5 rounded-xl bg-ink-50 p-3.5 text-left text-[13px] text-ink-600"><li>· 医生会先阅读您提交的资料，再叫号问诊。</li><li>· 请携带身份证件与既往检查资料。</li><li>· 如症状突然加重（如胸痛、呼吸困难、意识不清），请<b className="text-danger-600">立即告知工作人员或拨打 120</b>。</li></ul>
      <p className="mt-4 text-[11px] text-ink-400">您可以关闭本页面。</p></div>);

  const step = f.step; const audios = files.filter((x) => x.kind === "audio"); const labs = files.filter((x) => x.kind === "lab_image"); const tongues = files.filter((x) => x.kind === "tongue_image");
  const e = (k: string) => (showErr ? problems[k] : undefined);
  return (
    <div className="space-y-4">
      <div className="px-1"><h1 className="text-xl font-semibold text-ink-950">{info?.clinic || "MetaClinic"} · 就诊前问诊</h1>
        <p className="mt-1 text-[13px] text-ink-500">{info?.doctor ? `接诊医生：${info.doctor.name}${info.doctor.title ? `（${info.doctor.title}）` : ""}。` : ""}请如实填写，约 3–5 分钟。内容会自动保存，中途退出也不会丢失。</p></div>
      <ol className="flex items-center gap-1 px-1" aria-label="进度">{STEPS.map((s, i) => <li key={s} className="flex flex-1 flex-col items-center gap-1"><div className={`h-1.5 w-full rounded-full ${i < step ? "bg-brand-500" : i === step ? "bg-brand-300" : "bg-ink-200"}`} /><span className={`text-[10.5px] ${i === step ? "font-semibold text-brand-700" : "text-ink-400"}`}>{s}</span></li>)}</ol>

      {step === 0 && <Panel title="基本信息">
        <L label="姓名" required error={e("name")}><input className={`input ${e("name") ? "input-err" : ""}`} value={f.name} onChange={(x) => set("name", x.target.value)} autoComplete="name" maxLength={64} /></L>
        <div className="grid grid-cols-2 gap-3"><L label="性别"><div className="flex gap-2">{["男", "女"].map((g) => <button key={g} type="button" onClick={() => set("gender", f.gender === g ? "" : g)} aria-pressed={f.gender === g} className={`flex-1 rounded-lg border py-2 text-sm font-medium transition ${f.gender === g ? "border-brand-500 bg-brand-50 text-brand-700" : "border-ink-200 bg-white text-ink-600"}`}>{g}</button>)}</div></L>
          <L label="年龄" error={e("age")}><input className={`input ${e("age") ? "input-err" : ""}`} inputMode="numeric" value={f.age} onChange={(x) => set("age", x.target.value.replace(/\D/g, "").slice(0, 3))} placeholder="岁" /></L></div>
        <L label="手机号" error={e("phone")} hint="用于复诊和随访联系；同手机号的历史档案会自动关联。选填。"><input className={`input ${e("phone") ? "input-err" : ""}`} inputMode="numeric" autoComplete="tel" value={f.phone} onChange={(x) => set("phone", x.target.value)} /></L></Panel>}

      {step === 1 && <Panel title="您哪里不舒服？">
        <L label="主要不适" required error={e("chief_complaint")} hint="一句话：什么症状、持续多久。例如：头痛 3 天，伴头晕"><input className={`input ${e("chief_complaint") ? "input-err" : ""}`} value={f.chief_complaint} onChange={(x) => set("chief_complaint", x.target.value)} maxLength={200} /></L>
        <L label="详细经过（选填）" hint="什么时候开始、有什么诱因、吃过什么药、做过什么检查。也可以在“上传资料”里直接说话录音。"><textarea className="input min-h-[120px]" value={f.hpi} onChange={(x) => set("hpi", x.target.value)} maxLength={4000} /></L>
        <L label="伴随症状（选填）"><Chips value={f.symptoms} onChange={(v) => set("symptoms", v)} placeholder="输入后点“添加”，如：口干、乏力" suggestions={["发热", "咳嗽", "头晕", "乏力", "口干", "失眠", "恶心", "腹痛", "胸闷"]} /></L>
        <L label="目前正在吃的药（选填）" hint="包括中药、保健品。"><Meds value={f.current_meds} onChange={(v) => set("current_meds", v)} /></L></Panel>}

      {step === 2 && <Panel title="过敏与既往病史">
        <div className={`rounded-xl border p-3 ${showErr && problems.allergy ? "border-danger-300 bg-danger-50/40" : "border-ink-100"}`}>
          <L label="药物 / 食物过敏" required hint="如：青霉素、头孢、磺胺、海鲜、花粉">
            <Chips value={f.allergies} onChange={(v) => { set("allergies", v); if (v.length) set("no_allergies_confirmed", false); }} placeholder="输入过敏原后点“添加”" suggestions={["青霉素", "头孢", "磺胺", "海鲜", "花生", "阿司匹林"]} /></L>
          <label className="mt-3 flex items-start gap-2.5 rounded-lg bg-ink-50 p-2.5 text-sm text-ink-700"><input type="checkbox" className="mt-0.5 h-[18px] w-[18px] accent-brand-600" checked={f.no_allergies_confirmed} disabled={f.allergies.length > 0} onChange={(x) => set("no_allergies_confirmed", x.target.checked)} /><span>没有药物或食物过敏<span className="block text-xs text-ink-400">只有您确认没有时才勾选；不确定请不要勾选，向医生说明。</span></span></label>
          {showErr && problems.allergy && <div className="mt-2 text-xs text-danger-600">{problems.allergy}</div>}</div>
        <L label="既往病史（选填）" hint="如：高血压、糖尿病、冠心病、哮喘"><Chips value={f.chronic} onChange={(v) => set("chronic", v)} placeholder="输入后点“添加”" suggestions={["高血压", "糖尿病", "冠心病", "哮喘", "胃病", "甲状腺疾病"]} /></L>
        {f.gender === "女" && <div className="rounded-lg bg-amber-50 p-2.5 text-xs text-amber-800">如您<b>可能怀孕或正在哺乳</b>，请务必当面告诉医生，这会影响用药选择。</div>}</Panel>}

      {step === 3 && <Panel title="上传资料（都是选填）" subtitle="资料越全，医生越快看清病情。这些文件只用于本次诊疗。">
        <Block icon={<FileAudio size={18} />} title="语音描述" desc="直接说出您的不适，系统转成文字，医生会同时看到文字和录音。">
          <VoiceCapture onFile={upload("audio", "病史")} busy={busy} />{err.audio && <div className="mt-1.5 text-xs text-danger-600">{err.audio}</div>}
          <div className="mt-2 space-y-2">{audios.map((a) => <FileCard key={a.id} a={a} onRetry={() => retry(a.id)} onRemove={() => remove(a.id)} onFix={(t) => fix(a.id, t)} onUse={(t) => set("hpi", f.hpi.trim() ? `${f.hpi.trim()}\n${t}` : t)} />)}</div></Block>
        <Block icon={<FlaskConical size={18} />} title="检验单 / 检查报告" desc="拍清楚整张报告（光线均匀、不反光）。系统会识别数值，医生仍会对照原图核对。">
          <PickImage label="拍照 / 选择图片" icon={<ScanLine size={15} />} busy={busy} onFile={upload("lab_image", "检验")} />{err.lab_image && <div className="mt-1.5 text-xs text-danger-600">{err.lab_image}</div>}
          <div className="mt-2 space-y-2">{labs.map((a) => <FileCard key={a.id} a={a} thumb={localUrls.current[a.id]} onRetry={() => retry(a.id)} onRemove={() => remove(a.id)} />)}</div></Block>
        <Block icon={<Camera size={18} />} title="舌头照片" desc="自然光下张口伸舌，舌面完全展开，对焦清晰；不要吃有色食物后拍。">
          <PickImage label="拍照 / 选择图片" icon={<Camera size={15} />} busy={busy} onFile={upload("tongue_image", "舌象")} capture />{err.tongue_image && <div className="mt-1.5 text-xs text-danger-600">{err.tongue_image}</div>}
          <div className="mt-2 space-y-2">{tongues.map((a) => <FileCard key={a.id} a={a} thumb={localUrls.current[a.id]} onRetry={() => retry(a.id)} onRemove={() => remove(a.id)} />)}</div></Block>
        {err.files && <div className="text-xs text-danger-600">{err.files}</div>}</Panel>}

      {step === 4 && <Panel title="确认并提交">
        <dl className="divide-y divide-ink-50 text-[13.5px]">{[["姓名", `${f.name || "—"}${f.gender ? ` · ${f.gender}` : ""}${f.age ? ` · ${f.age}岁` : ""}`], ["手机", f.phone || "未填写"], ["主要不适", f.chief_complaint || "—"], ["详细经过", f.hpi || "未填写"], ["伴随症状", f.symptoms.join("、") || "未填写"],
          ["过敏史", f.allergies.length ? f.allergies.join("、") : f.no_allergies_confirmed ? "没有药物或食物过敏" : "未填写"], ["既往病史", f.chronic.join("、") || "未填写"], ["在用药物", f.current_meds.filter((m) => m.name.trim()).map((m) => m.name).join("、") || "未填写"], ["上传资料", `${audios.length} 段录音 · ${labs.length} 张检验单 · ${tongues.length} 张舌象`]].map(([k, v]) => <div key={k} className="flex gap-3 py-2"><dt className="w-20 shrink-0 text-ink-400">{k}</dt><dd className="min-w-0 flex-1 whitespace-pre-wrap break-words text-ink-900">{v}</dd></div>)}</dl>
        {working && <div className="flex items-center gap-2 rounded-lg bg-brand-50 p-2.5 text-xs text-brand-800"><Spinner size={13} />有文件还在识别中，您可以直接提交，医生会看到最新结果。</div>}
        <label className={`flex items-start gap-2.5 rounded-xl border p-3 text-[13px] leading-relaxed ${showErr && !consent ? "border-danger-300 bg-danger-50/40" : "border-ink-100 bg-white"}`}><input type="checkbox" className="mt-0.5 h-[18px] w-[18px] shrink-0 accent-brand-600" checked={consent} onChange={(x) => setConsent(x.target.checked)} />
          <span><ShieldCheck size={14} className="mr-1 inline text-brand-600" />我同意诊所使用以上个人信息与资料用于本次诊疗，并同意由 AI 辅助整理病情供医生参考。<b>AI 不会替代医生诊断</b>，最终诊疗决定由医生作出。</span></label>
        {showErr && !consent && <div className="text-xs text-danger-600">请先勾选同意</div>}
        {err.submit && <div role="alert" className="rounded-lg border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-600">{err.submit}</div>}</Panel>}

      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-ink-100 bg-white/95 px-4 py-3 backdrop-blur"><div className="mx-auto flex max-w-xl items-center gap-3">
        <button className="btn-secondary" disabled={step === 0 || busy} onClick={() => { set("step", step - 1); window.scrollTo({ top: 0 }); }} aria-label="上一步"><ArrowLeft size={16} /></button>
        <span className="text-[11px] text-ink-400" aria-live="polite">{saveState === "saving" ? "保存中…" : saveState === "saved" ? "已自动保存" : saveState === "error" ? <span className="text-danger-600">保存失败，请检查网络</span> : ""}</span>
        <div className="ml-auto">{step < 4 ? <button className="btn-primary btn-lg" onClick={next}>下一步<ArrowRight size={16} /></button> : <button className="btn-success btn-lg" disabled={busy} onClick={submit}>{busy ? <Spinner size={16} /> : <CheckCircle2 size={17} />}提交给医生</button>}</div></div></div>
    </div>
  );
}

// ================================================================== small pieces
function Panel({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) { return <section className="card card-pad space-y-4"><div><h2 className="text-[17px] font-semibold text-ink-950">{title}</h2>{subtitle && <p className="mt-0.5 text-xs text-ink-500">{subtitle}</p>}</div>{children}</section>; }
function L({ label, required, error, hint, children }: { label: string; required?: boolean; error?: string; hint?: string; children: ReactNode }) {
  return <div><label className="mb-1 block text-[13px] font-medium text-ink-700">{label}{required && <span className="ml-0.5 text-danger-500">*</span>}</label>{children}{error ? <div className="mt-1 text-xs text-danger-600">{error}</div> : hint ? <div className="mt-1 text-xs text-ink-400">{hint}</div> : null}</div>;
}
function Block({ icon, title, desc, children }: { icon: ReactNode; title: string; desc: string; children: ReactNode }) { return <div className="rounded-xl border border-ink-100 p-3.5"><div className="flex items-center gap-2 font-medium text-ink-900"><span className="text-brand-600">{icon}</span>{title}</div><p className="mb-3 mt-1 text-xs leading-relaxed text-ink-500">{desc}</p>{children}</div>; }

function Chips({ value, onChange, placeholder, suggestions = [] }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string; suggestions?: string[] }) {
  const [t, setT] = useState(""); const add = (x: string) => { const v = x.trim(); if (v && !value.includes(v)) onChange([...value, v]); setT(""); };
  return (
    <div className="space-y-2">
      {value.length > 0 && <div className="flex flex-wrap gap-1.5">{value.map((s) => <span key={s} className="tag-brand py-1 text-xs">{s}<button type="button" aria-label={`移除${s}`} onClick={() => onChange(value.filter((x) => x !== s))}><X size={12} /></button></span>)}</div>}
      <div className="flex gap-2"><input className="input" value={t} placeholder={placeholder} onChange={(e) => setT(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(t); } }} maxLength={64} /><button type="button" className="btn-secondary shrink-0" onClick={() => add(t)} disabled={!t.trim()}>添加</button></div>
      <div className="flex flex-wrap gap-1.5">{suggestions.filter((s) => !value.includes(s)).map((s) => <button key={s} type="button" className="rounded-full border border-ink-200 bg-white px-2.5 py-1 text-xs text-ink-600 active:bg-ink-50" onClick={() => add(s)}>＋{s}</button>)}</div>
    </div>);
}

function Meds({ value, onChange }: { value: F["current_meds"]; onChange: (v: F["current_meds"]) => void }) {
  return (
    <div className="space-y-2">{value.map((m, i) => (
      <div key={i} className="grid grid-cols-[1fr_auto] gap-2 rounded-lg border border-ink-100 p-2"><div className="space-y-1.5"><input className="input py-1.5" placeholder="药名" aria-label="药名" value={m.name} onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} /><div className="grid grid-cols-2 gap-1.5"><input className="input py-1.5" placeholder="每次多少" aria-label="剂量" value={m.dose} onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, dose: e.target.value } : x)))} /><input className="input py-1.5" placeholder="一天几次" aria-label="频次" value={m.frequency} onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, frequency: e.target.value } : x)))} /></div></div>
        <button type="button" className="self-start p-1.5 text-ink-300" aria-label="删除" onClick={() => onChange(value.filter((_, j) => j !== i))}><Trash2 size={16} /></button></div>))}
      <button type="button" className="btn-secondary btn-sm" onClick={() => onChange([...value, { name: "", dose: "", frequency: "" }])}><Plus size={13} />添加一种药</button></div>);
}

function PickImage({ label, icon, busy, onFile, capture }: { label: string; icon: ReactNode; busy: boolean; onFile: (f: File) => void; capture?: boolean }) {
  return <label className={`btn-secondary cursor-pointer ${busy ? "pointer-events-none opacity-60" : ""}`}>{busy ? <Spinner size={15} /> : icon}{label}<input type="file" accept="image/jpeg,image/png,image/webp" className="hidden" {...(capture ? { capture: "user" as any } : {})} disabled={busy} onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} /></label>;
}

function FileCard({ a, thumb, onRetry, onRemove, onFix, onUse }: { a: any; thumb?: string; onRetry: () => void; onRemove: () => void; onFix?: (t: string) => void; onUse?: (t: string) => void }) {
  const [txt, setTxt] = useState(a.text || ""); const touched = useRef(false); useEffect(() => { if (!touched.current) setTxt(a.text || ""); }, [a.text]);
  const run = a.proc_status === "pending" || a.proc_status === "processing";
  return (
    <div className="rounded-lg border border-ink-100 bg-white p-2.5 text-[13px]">
      <div className="flex items-center gap-2.5">{thumb && /* eslint-disable-next-line @next/next/no-img-element */ <img src={thumb} alt="" className="h-12 w-12 rounded object-cover" />}
        <div className="min-w-0 flex-1"><div className="truncate font-medium text-ink-800">{a.kind === "audio" ? "语音描述" : a.kind === "lab_image" ? "检验单" : "舌象照片"}<span className="ml-1.5 text-xs font-normal text-ink-400">{a.original_name}</span></div>
          <div className="text-xs">{run ? <span className="flex items-center gap-1 text-brand-700"><Spinner size={11} />{a.kind === "audio" ? "正在转成文字…" : a.kind === "lab_image" ? "正在识别（约 10–20 秒）…" : "处理中…"}</span> : a.proc_status === "failed" ? <span className="text-danger-600">处理失败</span> : a.proc_status === "succeeded" ? <span className="text-jade-700">{a.kind === "lab_image" ? `已识别 ${a.lab_items_found ?? 0} 项` : "已识别"}</span> : <span className="text-jade-700">已收到</span>}</div></div>
        <button type="button" className="p-1.5 text-ink-300" aria-label="删除" onClick={onRemove}><Trash2 size={16} /></button></div>
      {a.proc_status === "failed" && <div className="mt-2 rounded bg-danger-50 p-2 text-xs text-danger-700">{ERR[a.error_code] || a.error_detail || "请重试"}<div className="mt-1.5"><button type="button" className="btn-secondary btn-sm" onClick={onRetry}><RefreshCw size={12} />重试</button></div></div>}
      {a.kind === "audio" && a.proc_status === "succeeded" && (
        <div className="mt-2 space-y-1.5"><div className="text-xs text-ink-500">识别结果可能有错别字，请核对并修改：</div>
          <textarea className="input min-h-[72px] text-sm" value={txt} onChange={(e) => { touched.current = true; setTxt(e.target.value); }} aria-label="语音转文字内容" />
          <div className="flex gap-2"><button type="button" className="btn-secondary btn-sm" disabled={txt.trim() === (a.text || "").trim()} onClick={() => { onFix?.(txt); touched.current = false; }}>保存修改</button>{txt.trim() && <button type="button" className="btn-ghost btn-sm" onClick={() => onUse?.(txt.trim())}>补充到“详细经过”</button>}</div></div>)}
      {a.kind === "lab_image" && a.proc_status === "succeeded" && (a.lab_items_found ?? 0) === 0 && <div className="mt-1.5 text-xs text-amber-700">没有识别出数值，不影响提交，医生会直接查看您上传的原图。</div>}
    </div>);
}
