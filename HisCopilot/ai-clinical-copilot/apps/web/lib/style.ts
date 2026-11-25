// 医师经验画像：标签、状态映射、小工具（纯数据，无 React）。后端是唯一事实来源，这里只负责“怎么叫它”。

export const CATEGORY_ORDER = ["formula", "reasoning", "principle", "modification", "herb_pair", "preference", "outcome", "followup", "note"] as const;
export const CATEGORY_LABEL: Record<string, string> = {
  reasoning: "辨证思路", principle: "治疗原则", formula: "常用方药", herb_pair: "药物配伍", modification: "加减规律",
  preference: "用药偏好", outcome: "疗效反馈", followup: "随访习惯", note: "经验笔记",
};
export const CATEGORY_HINT: Record<string, string> = {
  reasoning: "遇到某类证候时，该医师反复关注哪些症状、舌脉", principle: "对某类证候通常采用的治法",
  formula: "核心药（多数病例使用）与常加减药，附中位剂量", herb_pair: "共现明显高于随机的配伍（不含全库都在用的药）",
  modification: "随兼症出现的加减做法", preference: "全库最常用药物及剂量倾向", outcome: "病历中自述的复诊疗效（非随访验证）", followup: "复诊/复查间隔习惯", note: "医师自述的经验笔记",
};

export const IMPORT_STATUS: Record<string, [string, string]> = {
  parsing: ["解析中", "tag-brand"], needs_review: ["待校验", "tag-warn"], extracting: ["抽取中", "tag-brand"], extracted: ["已抽取", "tag-ok"], failed: ["失败", "tag-err"], cancelled: ["已取消", "tag-neutral"],
};
export const CASE_STATUS: Record<string, [string, string]> = {
  pending_review: ["待确认", "tag-warn"], confirmed: ["已确认", "tag-ok"], excluded: ["已排除", "tag-neutral"],
};
export const REVIEW_STATUS: Record<string, [string, string]> = {
  pending: ["待审核", "tag-warn"], approved: ["已通过", "tag-ok"], rejected: ["已驳回", "tag-neutral"],
};
export const VERSION_STATUS: Record<string, [string, string]> = { draft: ["草稿", "tag-warn"], active: ["已发布", "tag-ok"], retired: ["已被取代", "tag-neutral"] };
export const AUTH_STATUS: Record<string, [string, string]> = { pending: ["待管理员备案", "tag-warn"], approved: ["已备案", "tag-ok"], revoked: ["已撤回", "tag-neutral"] };
export const BASIS_LABEL: Record<string, string> = { self: "本人授权", family: "家属授权", institution: "单位授权", publication: "公开出版物", other: "其他依据" };
export const CONF_LABEL: Record<string, string> = { high: "较高", medium: "中等", low: "较低" };
export const VERDICT_STYLE: Record<string, string> = { match: "tag-ok", partial: "tag-warn", mismatch: "tag-err", unknown: "tag-neutral" };
export const STANCE_STYLE: Record<string, string> = { adopted: "tag-ok", adjusted: "tag-brand", not_adopted: "tag-neutral", info_only: "tag-neutral" };
export const PARSE_STATUS: Record<string, [string, string]> = { pending: ["待解析", "tag-neutral"], parsed: ["已解析", "tag-ok"], failed: ["失败", "tag-err"], unsupported: ["不支持", "tag-err"] };

/** 标准字段 → 中文名（列映射编辑器用；key 与后端 cases.COLS 一致） */
export const MAPPING_FIELDS: [string, string][] = [
  ["complaint", "主诉/症状"], ["history", "现病史/病情"], ["tcm", "辨证/舌脉"], ["diagnosis", "诊断"], ["formula", "方名"], ["herbs", "处方（药味+剂量）"],
  ["advice", "治法/医嘱"], ["outcome", "疗效/复诊"], ["date", "就诊日期"], ["patient_key", "病历号"], ["name", "患者姓名"],
];

export type Flag = { code: string; severity: "info" | "warn" | "critical" | "error"; message: string };
export const hasCritical = (flags?: Flag[]) => !!flags?.some((f) => f.severity === "critical");

export function chip(map: Record<string, [string, string]>, k: string): [string, string] { return map[k] || [k, "tag-neutral"]; }

/** 在病例文本里把证据片段标出来（逐字匹配；找不到就原样返回，不做任何模糊猜测）。 */
export function splitByQuotes(text: string, quotes: string[]): { t: string; hit: boolean }[] {
  const spans: [number, number][] = [];
  for (const q of quotes) {
    if (!q) continue;
    const i = text.indexOf(q);
    if (i >= 0) spans.push([i, i + q.length]);
  }
  spans.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const s of spans) {
    const last = merged[merged.length - 1];
    if (last && s[0] <= last[1]) last[1] = Math.max(last[1], s[1]); else merged.push([...s] as [number, number]);
  }
  const out: { t: string; hit: boolean }[] = [];
  let pos = 0;
  for (const [a, b] of merged) {
    if (a > pos) out.push({ t: text.slice(pos, a), hit: false });
    out.push({ t: text.slice(a, b), hit: true });
    pos = b;
  }
  if (pos < text.length) out.push({ t: text.slice(pos), hit: false });
  return out.length ? out : [{ t: text, hit: false }];
}
