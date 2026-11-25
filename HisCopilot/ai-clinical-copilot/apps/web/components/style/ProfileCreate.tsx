"use client";
import { useState } from "react";
import useSWR from "swr";
import { Modal, Field } from "@/components/ui";
import { api } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { useAuth } from "@/lib/auth";

type Kind = "master" | "personal";

export function ProfileCreate({ open, onClose, onDone, hasPersonal }: { open: boolean; onClose: () => void; onDone: (id: string) => void; hasPersonal: boolean }) {
  const { user } = useAuth();
  const isAdmin = user?.role === "ADMIN";
  const act = useAction();
  const { data: docs } = useSWR(open && isAdmin ? "/doctors" : null);
  const [kind, setKind] = useState<Kind>(isAdmin ? "master" : hasPersonal ? "master" : "personal");
  const [name, setName] = useState(isAdmin ? "" : user?.display_name || "");
  const [title, setTitle] = useState(""); const [dept, setDept] = useState(""); const [intro, setIntro] = useState("");
  const [owner, setOwner] = useState(""); const [reviewer, setReviewer] = useState("");
  const proxyNeedsReviewer = isAdmin && !owner && !reviewer;
  const valid = name.trim().length > 0 && !proxyNeedsReviewer;

  const submit = () => act.run("create", async () => {
    const r = await api.post("/style/profiles", { kind, subject_name: name.trim(), subject_title: title, subject_dept: dept, intro, owner_user_id: owner || null, reviewer_id: reviewer || null });
    onDone(r.profile.id);
  }, { ok: "已创建" });

  return (
    <Modal open={open} onClose={onClose} width="max-w-xl" title="新建经验画像"
      footer={<><button className="btn-secondary" onClick={onClose}>取消</button><button className="btn-primary" disabled={!valid || act.any} onClick={submit}>创建</button></>}>
      <div className="space-y-4">
        {!isAdmin && (
          <div className="grid gap-2 sm:grid-cols-2">
            {([["master", "名医 / 资深医师经验库", "沉淀成熟的诊疗经验；经管理员备案授权后，可共享给其他医师参考。"], ["personal", "我的个人诊疗习惯", "只有您自己能看到和使用，用于让 AI 更贴近您的习惯。每位医师一份。"]] as [Kind, string, string][]).map(([k, t, h]) => (
              <label key={k} className={`flex cursor-pointer items-start gap-2 rounded-xl border p-3 text-sm ${kind === k ? "border-brand-300 bg-brand-50/60" : "border-ink-100"} ${k === "personal" && hasPersonal ? "opacity-50" : ""}`}>
                <input type="radio" className="mt-1 accent-brand-600" checked={kind === k} disabled={k === "personal" && hasPersonal} onChange={() => { setKind(k); if (k === "personal") setName(user?.display_name || ""); }} />
                <span><span className="font-medium text-ink-800">{t}</span><span className="block text-xs text-ink-500">{h}{k === "personal" && hasPersonal ? "（您已有一份）" : ""}</span></span>
              </label>))}
          </div>)}
        {isAdmin && <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">管理员代建：您可以为已故/外聘名医建立经验库并导入资料，但<b>不能审核或发布</b>临床内容，必须指定一位医师负责临床审核。共享前还需填写授权来源。</p>}
        <Field label="经验所属医师姓名" required><input className="input" value={name} maxLength={64} onChange={(e) => setName(e.target.value)} placeholder="如：周某某" /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="职称"><input className="input" value={title} maxLength={64} onChange={(e) => setTitle(e.target.value)} placeholder="如：主任医师 / 国医大师" /></Field>
          <Field label="科室 / 专长方向"><input className="input" value={dept} maxLength={64} onChange={(e) => setDept(e.target.value)} placeholder="如：脑病科" /></Field>
        </div>
        <Field label="简介（擅长、学术背景）" hint="人工填写，会显示给使用者；不要写患者信息。"><textarea className="input" rows={3} maxLength={2000} value={intro} onChange={(e) => setIntro(e.target.value)} /></Field>
        {isAdmin && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="所有者账号（可选）" hint="该医师本人若有系统账号，选择后由其本人审核发布。">
              <select className="input" value={owner} onChange={(e) => setOwner(e.target.value)}><option value="">无（外聘/已故）</option>{docs?.items?.map((d: any) => <option key={d.id} value={d.id}>{d.name}{d.dept ? ` · ${d.dept}` : ""}</option>)}</select></Field>
            <Field label={owner ? "临床审核医师（可选）" : "临床审核医师"} required={!owner} error={proxyNeedsReviewer ? "无所有者账号时必须指定" : undefined}>
              <select className="input" value={reviewer} onChange={(e) => setReviewer(e.target.value)}><option value="">请选择</option>{docs?.items?.map((d: any) => <option key={d.id} value={d.id}>{d.name}{d.dept ? ` · ${d.dept}` : ""}</option>)}</select></Field>
          </div>)}
      </div>
    </Modal>
  );
}
