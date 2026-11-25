"use client";
import { useEffect, useRef, useState } from "react";
import { Camera, FileAudio, FlaskConical, Image as ImageIcon, Plus, RefreshCw, ScanLine, Trash2, Upload } from "lucide-react";
import { api, errMsg, fmtTime } from "@/lib/api";
import { Banner, Spinner, StatusChip, useConfirm, useToast } from "@/components/ui";
import { VoiceCapture } from "@/components/VoiceCapture";
import { ImageViewer } from "@/components/ImageViewer";
import { useAction } from "@/lib/useAction";
import { Bullets, Card, Collapse, Elapsed } from "./bits";
import { useWs } from "./ctx";

const MB = 1024 * 1024;
const ERR_HINT: Record<string, string> = {
  asr_no_speech: "没有识别到人声：请靠近麦克风重录，或直接在左侧“现病史”中输入",
  asr_unavailable: "语音识别服务当前不可用：稍后重试，或直接输入文字",
  ocr_unavailable: "OCR 服务当前不可用：稍后重试，或在下方手工录入检验结果",
  model_oom: "显存繁忙，请稍后重试",
};

function ProcState({ a, onRetry, busy }: { a: any; onRetry: () => void; busy: boolean }) {
  if (a.proc_status === "pending" || a.proc_status === "processing")
    return <div className="flex items-center gap-2 text-xs text-brand-700"><Spinner size={13} /><span>{a.proc_status === "pending" ? "排队中" : a.kind === "audio" ? "语音识别中" : a.kind === "lab_image" ? "OCR 识别中（约 10–20 秒）" : "分析中"}…</span><Elapsed since={a.created_at} /></div>;
  if (a.proc_status === "failed")
    return (
      <div className="rounded-lg border border-danger-100 bg-danger-50/70 p-2.5 text-xs text-danger-700" role="alert">
        <div className="font-medium">处理失败{a.attempt_count > 1 ? `（已尝试 ${a.attempt_count} 次）` : ""}</div>
        <div className="mt-0.5 break-words text-danger-600">{ERR_HINT[a.error_code] || a.error_detail || "未知原因"}{a.error_code && <span className="ml-1 font-mono opacity-60">[{a.error_code}]</span>}</div>
        <button type="button" className="btn-secondary btn-sm mt-2" disabled={busy} onClick={onRetry}>{busy ? <Spinner size={12} /> : <RefreshCw size={12} />}重试处理</button>
      </div>);
  return null;
}

export function MaterialsPanel() {
  const ws = useWs(); const { b, eid, readOnly } = ws; const act = useAction(); const toast = useToast(); const confirm = useConfirm();
  const audios = b.artifacts.filter((a) => a.kind === "audio");
  const labArts = b.artifacts.filter((a) => a.kind === "lab_image");
  const tongues = b.artifacts.filter((a) => a.kind === "tongue_image");
  const manualLabs = b.labs.filter((l) => !l.artifact_id);
  const [showManual, setShowManual] = useState(false);

  // Uploads QUEUE (one after another) instead of going through the single-flight action lock — picking a second file while the first is
  // still uploading used to be dropped silently.
  const chain = useRef<Promise<void>>(Promise.resolve());
  const [uploading, setUploading] = useState<Record<string, number>>({});
  const upload = (kind: "audio" | "lab_image" | "tongue_image", purpose = "") => (file: File) => {
    const lim = kind === "audio" ? 25 : 15;
    if (file.size > lim * MB) return toast("err", `文件过大（${(file.size / MB).toFixed(1)} MB），上限 ${lim} MB`);
    if (file.size === 0) return toast("err", "文件内容为空");
    setUploading((u) => ({ ...u, [kind]: (u[kind] || 0) + 1 }));
    chain.current = chain.current.then(async () => {
      try {
        await ws.ensureStarted();
        const fd = new FormData(); fd.append("kind", kind); fd.append("purpose", purpose); fd.append("file", file);
        const r = await api.upload(`/encounters/${eid}/artifacts`, fd);
        if (!r.created) toast("info", "这个文件之前已上传过，已沿用原有记录");
        await ws.reload();
      } catch (e) { toast("err", `${file.name || "文件"} 上传失败：${errMsg(e)}`); }
      finally { setUploading((u) => ({ ...u, [kind]: Math.max(0, (u[kind] || 1) - 1) })); }
    });
  };
  const retry = (a: any) => act.run(`retry-${a.id}`, async () => { await api.post(`/artifacts/${a.id}/retry`); await ws.reload(); }, { onStale: ws.reload });
  const remove = async (a: any) => {
    const r = await confirm({ title: "删除这个文件？", body: a.kind === "lab_image" ? "同时会移除由它识别出的检验项。已生成的 AI 分析会标记为“资料已变化”。" : "已生成的 AI 分析会标记为“资料已变化”。", confirmText: "删除", danger: true });
    if (r.ok) act.run(`del-${a.id}`, async () => { await api.del(`/artifacts/${a.id}`); await ws.reload(); });
  };

  if (audios.length === 0 && labArts.length === 0 && tongues.length === 0 && manualLabs.length === 0 && !showManual) {
    return (
      <Card title="多模态资料采集（可选）" icon={<Upload size={16} className="text-brand-600" />}>
        <p className="mb-3 text-xs text-ink-500">支持口述病史录音转写、检验单拍照 OCR 识别与舌象观察；无补充资料时可直接进入下一步 AI 分析。</p>
        <div className="grid gap-2.5 sm:grid-cols-2">
          {!readOnly && <VoiceCapture onFile={upload("audio", "病史")} busy={!!uploading.audio} label="语音录入病史" />}
          {!readOnly && <FilePick label="上传检验单 (OCR)" icon={<ScanLine size={14} />} busy={!!uploading.lab_image} onFile={upload("lab_image", "检验")} />}
          {!readOnly && <FilePick label="上传舌象图" icon={<ImageIcon size={14} />} busy={!!uploading.tongue_image} onFile={upload("tongue_image", "舌象")} capture />}
          {!readOnly && <button type="button" className="btn-secondary justify-center" onClick={() => setShowManual(true)}><Plus size={14} />手工录入检验项</button>}
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {/* ------------------------------------------------ voice */}
      <Card title="语音记录" icon={<FileAudio size={16} className="text-brand-600" />}
        right={!readOnly && <VoiceCapture onFile={upload("audio", "病史")} busy={!!uploading.audio} label="语音录入" />}>
        {audios.length === 0 ? <div className="text-xs text-ink-400">可录入患者口述的病史。转写后请核对，再让 AI 使用。{readOnly ? "" : "也可直接在左侧输入文字。"}</div> :
          <ul className="space-y-3">{audios.map((a) => <AudioItem key={a.id} a={a} readOnly={readOnly} busy={act.any} onRetry={() => retry(a)} onRemove={() => remove(a)} />)}</ul>}
      </Card>

      {/* ------------------------------------------------ lab reports */}
      <Card title="检验单 / 检查报告" icon={<FlaskConical size={16} className="text-brand-600" />}
        right={!readOnly && <FilePick label="上传检验单" icon={<ScanLine size={14} />} busy={!!uploading.lab_image} onFile={upload("lab_image", "检验")} />}>
        {labArts.length === 0 && manualLabs.length === 0 && <div className="text-xs text-ink-400">拍照或上传检验单图片，系统用 OCR 提取数值。<b className="text-ink-500">提取结果必须对照原图核对</b>后才视为可靠资料。</div>}
        <div className="space-y-4">
          {labArts.map((a) => <LabArtifact key={a.id} a={a} rows={b.labs.filter((l) => l.artifact_id === a.id)} readOnly={readOnly} onRetry={() => retry(a)} onRemove={() => remove(a)} busy={act.any} />)}
          <ManualLabs rows={manualLabs} readOnly={readOnly} defaultOpen={showManual} />
        </div>
      </Card>

      {/* ------------------------------------------------ tongue */}
      <Card title="舌象" icon={<Camera size={16} className="text-brand-600" />}
        right={!readOnly && <FilePick label="上传舌象" icon={<ImageIcon size={14} />} busy={!!uploading.tongue_image} onFile={upload("tongue_image", "舌象")} capture />}>
        {tongues.length === 0 ? <div className="text-xs text-ink-400">自然光下伸舌拍照（舌面展开、对焦清晰）。图像模型只描述可见特征，是否采纳由医师决定。</div> :
          <ul className="space-y-3">{tongues.map((a) => <TongueItem key={a.id} a={a} readOnly={readOnly} busy={act.any} onRetry={() => retry(a)} onRemove={() => remove(a)} />)}</ul>}
      </Card>
    </div>
  );
}

function FilePick({ label, icon, busy, onFile, capture }: { label: string; icon: React.ReactNode; busy: boolean; onFile: (f: File) => void; capture?: boolean }) {
  return (
    <label className={`btn-secondary cursor-pointer ${busy ? "pointer-events-none opacity-60" : ""}`}>
      {busy ? <Spinner size={14} /> : icon}<span>{label}</span>
      <input type="file" accept="image/jpeg,image/png,image/webp" className="hidden" {...(capture ? { capture: "environment" as any } : {})} disabled={busy}
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
    </label>
  );
}

// ---------------------------------------------------------------------------------------------- audio
function AudioItem({ a, readOnly, busy, onRetry, onRemove }: { a: any; readOnly: boolean; busy: boolean; onRetry: () => void; onRemove: () => void }) {
  const ws = useWs(); const act = useAction(); const toast = useToast();
  const server = a.edited_text ?? a.result_text ?? "";
  const [txt, setTxt] = useState(server); const touched = useRef(false);
  useEffect(() => { if (!touched.current) setTxt(server); }, [server]);
  const dirty = txt.trim() !== (server || "").trim();
  const save = (confirmIt: boolean) => act.run(`txt-${a.id}`, async () => { await api.patch(`/artifacts/${a.id}/text`, { text: txt, confirm: confirmIt }); touched.current = false; await ws.reload(); }, { ok: confirmIt ? "已核对，AI 将按“已核对”使用这段文字" : "已保存修改", onStale: ws.reload });
  return (
    <li className="rounded-xl border border-ink-100 p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-500">
        <audio controls preload="none" src={a.url} className="h-8 max-w-[15rem]" />
        <span>{a.duration_s ? `${a.duration_s.toFixed(0)} 秒` : ""}</span><span>{fmtTime(a.created_at)}</span>{a.uploaded_by === "patient" && <span className="tag-brand">患者录入</span>}
        <StatusChip s={a.proc_status} />{a.proc_status === "succeeded" && (a.confirmed ? <span className="tag-ok">已核对</span> : <span className="tag-warn">未核对</span>)}
        {!readOnly && <button type="button" className="ml-auto text-ink-300 hover:text-danger-600" onClick={onRemove} aria-label="删除录音"><Trash2 size={14} /></button>}
      </div>
      <div className="mt-2"><ProcState a={a} onRetry={onRetry} busy={busy} />
        {a.proc_status === "succeeded" && (
          <div className="space-y-2">
            <textarea className="input min-h-[84px]" disabled={readOnly} value={txt} onChange={(e) => { touched.current = true; setTxt(e.target.value); }} placeholder="（未识别到文字）" aria-label="语音转写文本" />
            {a.edited_text && a.result_text && a.edited_text !== a.result_text && <details className="text-xs text-ink-400"><summary className="cursor-pointer">查看模型原始转写</summary><div className="mt-1 whitespace-pre-wrap rounded bg-ink-50 p-2 text-ink-600">{a.result_text}</div></details>}
            {!a.confirmed && <div className="text-xs text-amber-700">语音转写可能有同音字错误，AI 会把这段话标注为“未经核对”。请边听边校对。</div>}
            {!readOnly && (
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn-primary btn-sm" disabled={busy || !txt.trim() || (a.confirmed && !dirty)} onClick={() => save(true)}>{act.is(`txt-${a.id}`) ? <Spinner size={12} /> : null}{dirty ? "保存并确认无误" : "确认转写无误"}</button>
                {dirty && <button type="button" className="btn-secondary btn-sm" disabled={busy} onClick={() => save(false)}>仅保存修改</button>}
                <button type="button" className="btn-secondary btn-sm" disabled={!txt.trim()} onClick={() => { if (ws.hpiAppend.current) { ws.hpiAppend.current(txt.trim()); toast("ok", "已追加到现病史末尾（不会覆盖已有文字），请检查后保存"); } else toast("warn", "请先切换到“接诊资料”页"); }}>追加到现病史</button>
              </div>)}
          </div>)}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------------------------- lab
function LabArtifact({ a, rows, readOnly, onRetry, onRemove, busy }: { a: any; rows: any[]; readOnly: boolean; onRetry: () => void; onRemove: () => void; busy: boolean }) {
  const ws = useWs(); const act = useAction();
  const unverified = rows.filter((r) => !r.verified).length;
  return (
    <div className="rounded-xl border border-ink-100">
      <div className="flex flex-wrap items-center gap-2 border-b border-ink-100 px-3 py-2 text-xs text-ink-500">
        <span className="font-medium text-ink-700">{a.original_name || "检验单"}</span><span>{fmtTime(a.created_at)}</span>{a.uploaded_by === "patient" && <span className="tag-brand">患者上传</span>}<StatusChip s={a.proc_status} />
        {!readOnly && <button type="button" className="ml-auto text-ink-300 hover:text-danger-600" onClick={onRemove} aria-label="删除检验单"><Trash2 size={14} /></button>}
      </div>
      <div className="grid gap-3 p-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
        <ImageViewer src={`${a.url}?thumb=true`} alt="检验单原图" height={380} />
        <div className="min-w-0 space-y-2">
          <ProcState a={a} onRetry={onRetry} busy={busy} />
          {a.proc_status === "succeeded" && (<>
            {rows.length === 0 ? <Banner kind="warn" title="已识别文字，但没有解析出结构化的检验项">请对照左侧原图，在下方“手工录入”中补充关键项目；也可展开原始文本查看。</Banner> : (<>
              <div className="flex flex-wrap items-center gap-2">
                {unverified > 0 ? <span className="tag-warn">{unverified} 项待核对</span> : <span className="tag-ok">全部已核对</span>}
                <span className="hint">逐项对照左图；点击数值可直接修改。未核对的数值会被 AI 标注为“OCR 未核对”。</span>
                {!readOnly && unverified > 0 && <button type="button" className="btn-secondary btn-sm ml-auto" disabled={act.any} onClick={() => act.run("verify-all", async () => { await api.post(`/encounters/${ws.eid}/labs/verify-all`); await ws.reload(); }, { ok: "已将全部 OCR 检验项标记为已核对" })}>我已对照原图核对全部</button>}
              </div>
              <LabTable rows={rows} readOnly={readOnly} /></>)}
            {a.result_text && <Collapse title="OCR 原始识别文本（含未能解析的内容）"><pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs text-ink-600">{a.result_text}</pre></Collapse>}
          </>)}
        </div>
      </div>
    </div>
  );
}

function FlagChip({ f }: { f: string }) { return f === "H" ? <span className="tag-err">↑ 偏高</span> : f === "L" ? <span className="tag-warn">↓ 偏低</span> : f === "N" ? <span className="tag-ok">正常</span> : <span className="text-ink-300">—</span>; }

function LabTable({ rows, readOnly }: { rows: any[]; readOnly: boolean }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-ink-100">
      <table className="tbl text-[13px]"><thead><tr><th>项目</th><th>结果</th><th>单位</th><th>参考范围</th><th>判定</th><th className="text-center">已核对</th>{!readOnly && <th />}</tr></thead>
        <tbody>{rows.map((r) => <LabRow key={r.id} r={r} readOnly={readOnly} />)}</tbody></table>
    </div>
  );
}

function LabRow({ r, readOnly }: { r: any; readOnly: boolean }) {
  const ws = useWs(); const act = useAction(); const confirm = useConfirm();
  const [f, setF] = useState({ name: r.name, value: r.value, unit: r.unit || "", ref: r.ref || "" });
  const focused = useRef(false);
  useEffect(() => { if (!focused.current) setF({ name: r.name, value: r.value, unit: r.unit || "", ref: r.ref || "" }); }, [r.name, r.value, r.unit, r.ref]);
  const commit = () => {
    focused.current = false;
    const patch: any = {};
    (["name", "value", "unit", "ref"] as const).forEach((k) => { if (f[k].trim() !== (k === "unit" || k === "ref" ? (r[k] || "") : r[k])) patch[k] = f[k].trim(); });
    if (!Object.keys(patch).length) return;
    if (("value" in patch && !patch.value) || ("name" in patch && !patch.name)) { setF({ name: r.name, value: r.value, unit: r.unit || "", ref: r.ref || "" }); return; }
    act.run(`lab-${r.id}`, async () => { await api.patch(`/labs/${r.id}`, patch); await ws.reload(); }, { onStale: ws.reload });
  };
  const cell = (k: "name" | "value" | "unit" | "ref", w: string) => (
    <input className={`w-full rounded border border-transparent bg-transparent px-1.5 py-1 hover:border-ink-200 focus:border-brand-400 focus:bg-white focus:outline-none ${k === "value" && (r.flag === "H" || r.flag === "L") ? "font-semibold text-danger-600" : ""}`} style={{ minWidth: w }} disabled={readOnly}
      value={f[k]} aria-label={{ name: "项目", value: "结果", unit: "单位", ref: "参考范围" }[k]} onFocus={() => (focused.current = true)} onBlur={commit} onChange={(e) => setF({ ...f, [k]: e.target.value })} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />);
  return (
    <tr className={r.verified ? "" : "bg-amber-50/40"}>
      <td>{r.abbr && <span className="mr-1 font-mono text-[10px] text-ink-400">{r.abbr}</span>}{cell("name", "6rem")}</td><td>{cell("value", "3.5rem")}</td><td>{cell("unit", "3rem")}</td><td>{cell("ref", "4.5rem")}</td><td><FlagChip f={r.flag} /></td>
      <td className="text-center"><input type="checkbox" className="h-4 w-4 accent-brand-600" disabled={readOnly || act.any} checked={r.verified} aria-label={`${r.name} 已核对`}
        onChange={(e) => act.run(`lab-${r.id}`, async () => { await api.patch(`/labs/${r.id}`, { verified: e.target.checked }); await ws.reload(); })} /></td>
      {!readOnly && <td><button type="button" className="text-ink-300 hover:text-danger-600" aria-label={`删除${r.name}`} onClick={async () => { const c = await confirm({ title: `删除「${r.name}」？`, confirmText: "删除", danger: true }); if (c.ok) act.run(`lab-${r.id}`, async () => { await api.del(`/labs/${r.id}`); await ws.reload(); }); }}><Trash2 size={13} /></button></td>}
    </tr>);
}

function ManualLabs({ rows, readOnly, defaultOpen = false }: { rows: any[]; readOnly: boolean; defaultOpen?: boolean }) {
  const ws = useWs(); const act = useAction();
  const [open, setOpen] = useState(defaultOpen); const [f, setF] = useState({ name: "", value: "", unit: "", ref: "" }); const [err, setErr] = useState("");
  const add = () => {
    if (!f.name.trim() || !f.value.trim()) return setErr("请填写项目名称和结果");
    setErr("");
    act.run("lab-add", async () => { await api.post(`/encounters/${ws.eid}/labs`, { name: f.name.trim(), value: f.value.trim(), unit: f.unit.trim(), ref: f.ref.trim() }); setF({ name: "", value: "", unit: "", ref: "" }); await ws.reload(); }, { ok: "已添加" });
  };
  if (readOnly && rows.length === 0) return null;
  return (
    <div className="rounded-xl border border-dashed border-ink-200 p-3">
      <div className="flex items-center gap-2"><span className="text-[13px] font-medium text-ink-700">手工录入的检验项</span><span className="hint">医师亲自录入，视为已核对</span>
        {!readOnly && <button type="button" className="btn-secondary btn-sm ml-auto" onClick={() => setOpen(!open)}><Plus size={13} />添加</button>}</div>
      {rows.length > 0 && <div className="mt-2"><LabTable rows={rows} readOnly={readOnly} /></div>}
      {open && !readOnly && (
        <form className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-[1.4fr_1fr_1fr_1fr_auto]" onSubmit={(e) => { e.preventDefault(); add(); }}>
          <input className="input" placeholder="项目，如 空腹血糖" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} aria-label="项目名称" />
          <input className="input" placeholder="结果" value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} aria-label="结果" />
          <input className="input" placeholder="单位" value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} aria-label="单位" />
          <input className="input" placeholder="参考，如 3.9-6.1" value={f.ref} onChange={(e) => setF({ ...f, ref: e.target.value })} aria-label="参考范围" />
          <button className="btn-primary" disabled={act.any}>{act.is("lab-add") ? <Spinner size={13} /> : "保存"}</button>
          {err && <div className="col-span-full text-xs text-danger-600">{err}</div>}
        </form>)}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- tongue
function TongueItem({ a, readOnly, busy, onRetry, onRemove }: { a: any; readOnly: boolean; busy: boolean; onRetry: () => void; onRemove: () => void }) {
  const r = a.result || {}; const q = r.image_quality || {}; const unusable = a.proc_status === "succeeded" && r._usable === false;
  return (
    <li className="grid gap-3 rounded-xl border border-ink-100 p-3 sm:grid-cols-[10rem_1fr]">
      <div className="relative">{/* eslint-disable-next-line @next/next/no-img-element */}<img src={`${a.url}?thumb=true`} alt="舌象" className="h-36 w-full rounded-lg bg-ink-100 object-cover" />
        {!readOnly && <button type="button" className="absolute right-1.5 top-1.5 rounded-full bg-white/90 p-1 text-ink-400 shadow hover:text-danger-600" onClick={onRemove} aria-label="删除舌象"><Trash2 size={13} /></button>}</div>
      <div className="min-w-0 space-y-1.5 text-[13px]">
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-500"><StatusChip s={a.proc_status} />{a.uploaded_by === "patient" && <span className="tag-brand">患者上传</span>}<span>{fmtTime(a.created_at)}</span></div>
        <ProcState a={a} onRetry={onRetry} busy={busy} />
        {a.proc_status === "not_required" && <div className="text-xs text-ink-500">将在 AI 预分析时一并分析（图像模型占用 GPU，按顺序处理）。</div>}
        {unusable && <Banner kind="warn" title="图片质量不合格，AI 未采用">{(q.issues || []).join("；") || "请重新拍摄：自然光、舌面展开、对焦清晰。"}</Banner>}
        {a.proc_status === "succeeded" && !unusable && (<>
          <div className="text-xs text-ink-400">以下为图像模型的“可见特征描述”，非诊断；请结合您的望诊核对。</div>
          <Bullets items={r.observations} />
          {r.uncertain?.length > 0 && <div className="text-xs text-amber-700">不确定：{r.uncertain.join("；")}</div>}
          {r.not_observed?.length > 0 && <div className="text-xs text-ink-400">未见：{r.not_observed.join("；")}</div>}
        </>)}
      </div>
    </li>
  );
}
