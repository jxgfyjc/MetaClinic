"use client";
import { useState } from "react";
import useSWR from "swr";
import { Pencil, Plus, Search } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Banner, Empty, ErrorBox, Field, Modal, SkeletonList, Spinner, Tabs, useDebounced, useToast } from "@/components/ui";
import { useAction } from "@/lib/useAction";

const TABS = [["western", "西药"], ["patent", "中成药"], ["herb", "中药饮片"], ["formula", "方剂"], ["program", "治疗项目"]] as const;
const SRC: Record<string, [string, string]> = { sample: ["示例数据", "tag-sim"], reference: ["通用参考", "tag-neutral"], hospital: ["本院目录", "tag-ok"] };

export default function Catalog() {
  const { can } = useAuth(); const [tab, setTab] = useState<string>("western"); const [q, setQ] = useState(""); const dq = useDebounced(q, 250); const [edit, setEdit] = useState<any>(null);
  const drug = !["formula", "program"].includes(tab);
  const key = drug ? `/catalog/drugs?kind=${tab}&limit=100&q=${encodeURIComponent(dq)}` : tab === "formula" ? `/catalog/formulas?q=${encodeURIComponent(dq)}` : "/catalog/therapy-programs";
  const { data, error, isLoading, mutate } = useSWR(key);
  const items: any[] = data?.items || [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="h-page">药品目录</h1><p className="mt-1 text-sm text-ink-500">处方药名会与目录比对；目录外的药物会被标记，药房可能无法调配。</p></div>
        {drug && can("catalog.write") && <button className="btn-primary" onClick={() => setEdit({ kind: tab })}><Plus size={16} />新增药品</button>}</div>
      <Banner kind="warn" title="当前目录包含“示例数据 / 通用参考”条目">这些不是贵院真实药库。请药剂科核对后在此维护为“本院目录”（可对接院内药品字典批量导入），再用于正式处方。</Banner>
      <div className="card overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 px-2 pt-1"><div className="min-w-0 flex-1"><Tabs value={tab} onChange={(v) => { setTab(v); setQ(""); }} items={TABS.map(([k, l]) => ({ key: k, label: l }))} /></div>
          {tab !== "program" && <div className="relative mr-2 w-full sm:w-60"><Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" /><input className="input rounded-full py-1.5 pl-8 text-xs" placeholder="搜索名称" value={q} onChange={(e) => setQ(e.target.value)} /></div>}</div>
        {error ? <div className="p-4"><ErrorBox error={error} onRetry={() => mutate()} /></div> : isLoading ? <SkeletonList rows={5} /> : items.length === 0 ? <Empty title="没有找到条目" /> : drug ? (
          <div className="overflow-x-auto"><table className="tbl"><thead><tr><th>名称</th><th>规格 / 剂型</th><th>{tab === "herb" ? "常规用量" : "默认用法"}</th><th>分类</th><th>备注 / 禁忌</th><th>来源</th>{can("catalog.write") && <th />}</tr></thead>
            <tbody>{items.map((d) => <tr key={d.id}><td className="font-medium">{d.name}{d.generic_name && d.generic_name !== d.name && <span className="ml-1 text-xs text-ink-400">{d.generic_name}</span>}</td><td>{[d.spec, d.form].filter(Boolean).join(" · ") || "—"}</td>
              <td>{tab === "herb" ? (d.dose_max_g ? `${d.dose_min_g ?? ""}${d.dose_min_g ? "–" : "≤"}${d.dose_max_g} g${d.special_method ? `（${d.special_method}）` : ""}` : "—") : [d.default_dose, d.default_frequency, d.default_route].filter(Boolean).join(" ") || "—"}</td><td>{d.drug_class || "—"}</td>
              <td className="max-w-[16rem] truncate text-ink-500" title={`${d.pregnancy_note} ${d.contraindication}`}>{[d.pregnancy_note, d.contraindication].filter(Boolean).join("；") || "—"}</td><td><span className={(SRC[d.source] || SRC.reference)[1]}>{(SRC[d.source] || [d.source])[0]}</span></td>
              {can("catalog.write") && <td><button className="btn-ghost btn-sm" onClick={() => setEdit(d)} aria-label={`编辑 ${d.name}`}><Pencil size={13} /></button></td>}</tr>)}</tbody></table></div>
        ) : tab === "formula" ? (
          <ul className="divide-y divide-ink-50">{items.map((f) => <li key={f.id} className="px-4 py-3"><div className="flex items-center gap-2"><span className="font-medium">{f.name}</span><span className="tag-neutral">{f.formula_type}</span><span className={(SRC[f.source] || SRC.reference)[1]}>{(SRC[f.source] || [f.source])[0]}</span></div>
            {f.indication && <div className="mt-0.5 text-xs text-ink-500">{f.indication}</div>}<div className="mt-1 text-[13px] text-ink-700">{(f.herbs || []).map((h: any) => `${h.name}${h.dose_g ? ` ${h.dose_g}g` : ""}`).join("、")}</div></li>)}</ul>
        ) : (
          <div className="overflow-x-auto"><table className="tbl"><thead><tr><th>项目</th><th>类别</th><th>适应证</th><th>目标</th><th>频次</th><th>禁忌</th><th>来源</th></tr></thead><tbody>{items.map((t) => <tr key={t.id}><td className="font-medium">{t.name}</td><td>{t.category}</td><td>{t.applicable}</td><td>{t.goal}</td><td>{t.frequency}</td><td className="text-danger-700">{t.contraindications}</td><td><span className={(SRC[t.source] || SRC.reference)[1]}>{(SRC[t.source] || [t.source])[0]}</span></td></tr>)}</tbody></table></div>)}
      </div>
      {edit && <DrugModal d={edit} onClose={() => setEdit(null)} onDone={() => { setEdit(null); mutate(); }} />}
    </div>
  );
}

function DrugModal({ d, onClose, onDone }: { d: any; onClose: () => void; onDone: () => void }) {
  const act = useAction(); const isNew = !d.id;
  const [f, setF] = useState({ kind: d.kind, name: d.name || "", generic_name: d.generic_name || "", spec: d.spec || "", form: d.form || "", unit: d.unit || "", default_dose: d.default_dose || "", default_frequency: d.default_frequency || "", drug_class: d.drug_class || "", pregnancy_note: d.pregnancy_note || "", contraindication: d.contraindication || "", dose_min_g: d.dose_min_g ?? "", dose_max_g: d.dose_max_g ?? "", special_method: d.special_method || "", his_code: d.his_code || "" });
  const set = (k: string, v: any) => setF((s) => ({ ...s, [k]: v }));
  const herb = f.kind === "herb";
  const save = () => act.run("save", async () => {
    const body: any = { ...f, dose_min_g: f.dose_min_g === "" ? null : Number(f.dose_min_g), dose_max_g: f.dose_max_g === "" ? null : Number(f.dose_max_g) };
    isNew ? await api.post("/catalog/drugs", body) : await api.patch(`/catalog/drugs/${d.id}`, body); onDone();
  }, { ok: "已保存" });
  return (
    <Modal open onClose={onClose} title={isNew ? "新增药品" : `编辑 ${d.name}`} width="max-w-xl" footer={<><button className="btn-secondary" onClick={onClose}>取消</button><button className="btn-primary" disabled={act.any || !f.name.trim()} onClick={save}>{act.is("save") ? <Spinner size={14} /> : null}保存</button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="名称" required><input className="input" value={f.name} onChange={(e) => set("name", e.target.value)} autoFocus /></Field><Field label="通用名"><input className="input" value={f.generic_name} onChange={(e) => set("generic_name", e.target.value)} /></Field>
        <Field label="规格"><input className="input" value={f.spec} onChange={(e) => set("spec", e.target.value)} /></Field><Field label="剂型"><input className="input" value={f.form} onChange={(e) => set("form", e.target.value)} /></Field>
        {!herb && <><Field label="默认单次剂量"><input className="input" value={f.default_dose} onChange={(e) => set("default_dose", e.target.value)} /></Field><Field label="默认频次"><input className="input" value={f.default_frequency} onChange={(e) => set("default_frequency", e.target.value)} /></Field>
          <Field label="药理分类（用于重复用药/相互作用规则）" hint="如 ACEI、ARB、他汀、NSAID、青霉素类"><input className="input" value={f.drug_class} onChange={(e) => set("drug_class", e.target.value)} /></Field></>}
        {herb && <><Field label="常规用量下限（g）"><input className="input" type="number" step="0.5" value={f.dose_min_g} onChange={(e) => set("dose_min_g", e.target.value)} /></Field><Field label="常规用量上限（g）"><input className="input" type="number" step="0.5" value={f.dose_max_g} onChange={(e) => set("dose_max_g", e.target.value)} /></Field>
          <Field label="特殊煎法"><input className="input" value={f.special_method} onChange={(e) => set("special_method", e.target.value)} placeholder="先煎 / 后下 / 包煎…" /></Field></>}
        <Field label="院内编码（HIS）"><input className="input" value={f.his_code} onChange={(e) => set("his_code", e.target.value)} /></Field>
        <div className="sm:col-span-2"><Field label="妊娠提示"><input className="input" value={f.pregnancy_note} onChange={(e) => set("pregnancy_note", e.target.value)} /></Field></div>
        <div className="sm:col-span-2"><Field label="禁忌 / 注意"><input className="input" value={f.contraindication} onChange={(e) => set("contraindication", e.target.value)} /></Field></div>
      </div>
      <p className="mt-3 text-xs text-ink-400">保存后条目来源将标记为“本院目录”。药物剂量范围与分类会直接影响处方安全校验，请核对后维护。</p>
    </Modal>);
}
