"use client";
import { useRef, useState } from "react";
import useSWR from "swr";
import { FileUp, Plus, Trash2, X } from "lucide-react";
import { api } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { Banner, useToast } from "@/components/ui";
import { MAPPING_FIELDS } from "@/lib/style";
import { Collapse } from "@/components/workspace/bits";

const fmtSize = (n: number) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

/** 新建导入批次：多文件 + 粘贴文字 / 经验笔记 + （可选）列映射。上传后跳转到该批次的向导页。 */
export function ImportNew({ pid, onCreated }: { pid: string; onCreated: (iid: string) => void }) {
  const act = useAction(); const toast = useToast();
  const { data: opt } = useSWR("/style/options");
  const [files, setFiles] = useState<File[]>([]); const [text, setText] = useState(""); const [asNote, setAsNote] = useState(false); const [label, setLabel] = useState("");
  const [map, setMap] = useState<{ f: string; c: string }[]>([]); const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const maxMb = opt?.max_file_mb || 20;
  const tooBig = files.filter((f) => f.size > maxMb * 1048576);
  const add = (fs: FileList | File[]) => setFiles((cur) => { const names = new Set(cur.map((f) => f.name + f.size)); return [...cur, ...Array.from(fs).filter((f) => !names.has(f.name + f.size))].slice(0, 40); });
  const canSubmit = (files.length > 0 || text.trim().length > 0) && tooBig.length === 0;

  const submit = () => act.run("import", async () => {
    const fd = new FormData();
    fd.append("label", label);
    files.forEach((f) => fd.append("files", f));
    if (text.trim()) { fd.append("text", text); fd.append("as_note", String(asNote)); }
    const m = Object.fromEntries(map.filter((r) => r.f && r.c.trim()).map((r) => [r.f, r.c.trim()]));
    if (Object.keys(m).length) fd.append("mapping", JSON.stringify(m));
    const r = await api.upload<any>(`/style/profiles/${pid}/imports`, fd);
    if (r.rejected?.length) toast("warn", `有 ${r.rejected.length} 个文件未导入：` + r.rejected.map((x: any) => `${x.name || "(无名)"}（${x.message}）`).join("；"));
    onCreated(r.import.id);
  }, { ok: "已创建导入批次" });

  return (
    <div className="space-y-4">
      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }}
        className={`rounded-xl border-2 border-dashed p-5 text-center transition ${drag ? "border-brand-400 bg-brand-50/50" : "border-ink-200"}`}>
        <FileUp className="mx-auto text-ink-300" size={26} />
        <p className="mt-2 text-sm text-ink-700">拖入历史病历 / 处方文件，或 <button type="button" className="font-medium text-brand-700 hover:underline" onClick={() => input.current?.click()}>选择文件</button></p>
        <p className="mt-1 text-xs text-ink-400">支持：{(opt?.accepted || ["txt", "csv", "json", "docx", "xlsx", "pdf（文字版）", "jpg/png/webp（OCR）"]).join("、")}；单个文件不超过 {maxMb} MB，一批最多 40 个。</p>
        <p className="mt-1 text-xs text-ink-400">扫描版 PDF 请先导出为图片再上传；旧版 .doc/.xls 请另存为 .docx/.xlsx。</p>
        <input ref={input} type="file" multiple className="hidden" accept=".txt,.md,.csv,.tsv,.json,.jsonl,.docx,.xlsx,.pdf,.jpg,.jpeg,.png,.webp" onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = ""; }} />
      </div>
      {files.length > 0 && (
        <ul className="divide-y divide-ink-50 rounded-xl border border-ink-100">
          {files.map((f, i) => (
            <li key={f.name + f.size} className="flex items-center gap-2 px-3 py-2 text-sm">
              <span className="min-w-0 flex-1 truncate">{f.name}</span><span className={`text-xs ${f.size > maxMb * 1048576 ? "text-danger-600" : "text-ink-400"}`}>{fmtSize(f.size)}{f.size > maxMb * 1048576 ? "（超过上限）" : ""}</span>
              <button type="button" className="rounded p-1 text-ink-400 hover:bg-ink-100" aria-label="移除" onClick={() => setFiles(files.filter((_, j) => j !== i))}><X size={14} /></button>
            </li>))}
        </ul>)}
      <div>
        <label className="label">或直接粘贴文字（多份病例可用“病例一/病例二”或空行分隔）</label>
        <textarea className="input" rows={5} value={text} onChange={(e) => setText(e.target.value)} placeholder={"例：病例一：男，58岁。头晕头痛2周，舌红苔薄黄，脉弦数，辨证肝阳上亢。治以平肝潜阳，予天麻钩藤饮加减：天麻10g 钩藤15g(后下) 石决明30g(先煎)……"} />
        <label className="mt-2 flex items-start gap-2 text-sm text-ink-700"><input type="checkbox" className="mt-0.5 h-4 w-4 accent-brand-600" checked={asNote} onChange={(e) => setAsNote(e.target.checked)} /><span>这是<b>医师自述的经验笔记</b>（医话、心得），不是病例。笔记不计入病例统计，会以“医师自述”标注。</span></label>
      </div>
      <Collapse title="高级：指定表格列对应的字段（通常不用填）">
        <p className="mb-2 text-xs text-ink-500">系统会按列名自动识别（主诉、处方、证型、病历号…）。若您的表头很特别，可在此指定“标准字段 → 您表格里的列名”。导入后可在向导页看到识别结果。</p>
        <div className="space-y-2">
          {map.map((r, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <select className="input w-44" value={r.f} onChange={(e) => setMap(map.map((x, j) => j === i ? { ...x, f: e.target.value } : x))}><option value="">选择字段</option>{MAPPING_FIELDS.map(([k, n]) => <option key={k} value={k}>{n}</option>)}</select>
              <span className="text-ink-400">←</span>
              <input className="input w-52" placeholder="您表格里的列名" value={r.c} onChange={(e) => setMap(map.map((x, j) => j === i ? { ...x, c: e.target.value } : x))} />
              <button type="button" className="btn-ghost btn-sm" onClick={() => setMap(map.filter((_, j) => j !== i))}><Trash2 size={13} /></button>
            </div>))}
          <button type="button" className="btn-secondary btn-sm" onClick={() => setMap([...map, { f: "", c: "" }])}><Plus size={13} />添加一条</button>
        </div>
      </Collapse>
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[14rem] flex-1"><label className="label">批次备注（可选）</label><input className="input" maxLength={120} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="如：2019–2022 门诊病历" /></div>
        <button className="btn-primary" disabled={!canSubmit || act.any} onClick={submit}>{act.is("import") ? "上传中…" : "开始导入"}</button>
      </div>
      <Banner kind="info" title="隐私">上传后系统会先<b>自动遮盖</b>姓名、电话、证件号、地址、病历号等信息，并要求您<b>逐份核对确认</b>后才会进入下一步；抽取完成后，原始文件会自动从服务器删除。</Banner>
    </div>
  );
}
