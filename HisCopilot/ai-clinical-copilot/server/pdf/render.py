"""PDF documents (fpdf2 + bundled Source Han Serif SC): medical record and prescription.

Layout rules: A4, 15mm margins, running footer with document id / page n of N / generation time, clinic header,
a structured patient block, section headings, real tables for drugs, and a signature block that states exactly
what was signed and when. Documents carry a visible DOCUMENT-STATE banner (草稿 / 已定稿 / 已更正 / 模拟 HIS) so an
unsigned draft can never be mistaken for a final record, and a short verification hash (content hash prefix).
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from server.config import REPO_ROOT, get_settings

FONT_DIR = REPO_ROOT / "assets" / "fonts"
NAVY = (22, 52, 99)
GREY = (110, 118, 129)
LIGHT = (240, 243, 248)
RED = (176, 40, 40)
AMBER = (176, 110, 0)


class Doc(FPDF):
    def __init__(self, title: str, doc_id: str, banner: tuple[str, tuple[int, int, int]] | None = None):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.doc_title, self.doc_id, self.banner = title, doc_id, banner
        self.set_margins(15, 16, 15)
        self.set_auto_page_break(auto=True, margin=18)
        self.add_font("CN", "", str(FONT_DIR / "cn-regular.otf"))
        self.add_font("CN", "B", str(FONT_DIR / "cn-medium.otf"))
        self.alias_nb_pages()
        self.add_page()

    @property
    def uw(self) -> float:
        return self.w - self.l_margin - self.r_margin

    def header(self):
        self.set_font("CN", "B", 14)
        self.set_text_color(*NAVY)
        self.cell(0, 7, get_settings().clinic_name, new_x="LMARGIN", new_y="NEXT")
        self.set_font("CN", "", 9)
        self.set_text_color(*GREY)
        self.cell(0, 5, self.doc_title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*NAVY)
        self.set_line_width(0.5)
        self.line(self.l_margin, self.get_y() + 1, self.w - self.r_margin, self.get_y() + 1)
        self.ln(4)
        if self.banner:
            text, color = self.banner
            self.set_font("CN", "B", 10)
            self.set_text_color(*color)
            self.set_draw_color(*color)
            self.set_line_width(0.4)
            self.cell(0, 7, f"  {text}", border=1, new_x="LMARGIN", new_y="NEXT")
            self.ln(2)
        self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-14)
        self.set_font("CN", "", 8)
        self.set_text_color(*GREY)
        self.set_draw_color(200, 205, 212)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.cell(self.uw * 0.5, 6, f"{self.doc_id}", new_x="RIGHT")
        self.cell(self.uw * 0.5, 6, f"第 {self.page_no()} / {{nb}} 页  ·  生成于 {datetime.now():%Y-%m-%d %H:%M}", align="R")

    # ------------------------------------------------------------ primitives
    def h2(self, text: str):
        self.ln(2)
        self.set_font("CN", "B", 11)
        self.set_text_color(*NAVY)
        self.set_fill_color(*LIGHT)
        self.cell(0, 7, f"  {text}", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(1)

    def para(self, text: str, size: float = 10, bold: bool = False, color=(0, 0, 0), indent: float = 0):
        self.set_font("CN", "B" if bold else "", size)
        self.set_text_color(*color)
        for line in (text or "").split("\n"):
            self.set_x(self.l_margin + indent)
            self.multi_cell(self.uw - indent, size * 0.55, line if line.strip() else " ", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)

    def kv(self, pairs: list[tuple[str, str]], cols: int = 2):
        w = self.uw / cols
        self.set_font("CN", "", 9.5)
        for i in range(0, len(pairs), cols):
            chunk = pairs[i:i + cols]
            h = 6.2
            for k, v in chunk:
                x, y = self.get_x(), self.get_y()
                self.set_text_color(*GREY)
                self.cell(w * 0.32, h, k, new_x="RIGHT")
                self.set_text_color(0, 0, 0)
                self.cell(w * 0.68, h, str(v if v not in (None, "") else "未填写")[:40], new_x="RIGHT")
            self.ln(h)

    def table(self, headers: list[str], rows: list[list], widths: list[float], size: float = 9):
        """Bordered table with wrapped cells. Row height is computed from a real text-width measurement
        (so CJK/Latin mixes wrap correctly) and rows never split across pages."""
        total = float(sum(widths))
        widths = [w * self.uw / total for w in widths]
        lh = size * 0.5                                   # line height (mm) for the chosen font size
        pad = 1.2

        def wrap(text: str, w: float) -> list[str]:
            self.set_font("CN", "", size)
            out: list[str] = []
            for para in str(text if text not in (None, "") else "—").split("\n"):
                cur = ""
                for ch in para:
                    if self.get_string_width(cur + ch) > w - 2 * pad and cur:
                        out.append(cur)
                        cur = ch
                    else:
                        cur += ch
                out.append(cur)
            return out or [""]

        def draw_row(cells: list[str], bold: bool, fill: bool):
            lines = [wrap(c, w) for c, w in zip(cells, widths)]
            h = max(len(l) for l in lines) * lh + 2 * pad
            if self.get_y() + h > self.h - self.b_margin:
                self.add_page()
            x0, y0 = self.l_margin, self.get_y()
            self.set_font("CN", "B" if bold else "", size)
            for ls, w in zip(lines, widths):
                if fill:
                    self.set_fill_color(*LIGHT)
                    self.rect(x0, y0, w, h, style="F")
                self.rect(x0, y0, w, h)
                for i, ln in enumerate(ls):
                    self.set_xy(x0 + pad, y0 + pad + i * lh)
                    self.cell(w - 2 * pad, lh, ln)
                x0 += w
            self.set_xy(self.l_margin, y0 + h)

        self.set_draw_color(190, 197, 207)
        self.set_line_width(0.2)
        draw_row(headers, True, True)
        for r in rows:
            draw_row([str(c) if c not in (None, "") else "—" for c in r], False, False)

    def sign_block(self, lines: list[str]):
        if self.get_y() > self.h - 55:
            self.add_page()
        self.ln(4)
        self.set_draw_color(*NAVY)
        self.set_line_width(0.3)
        self.rect(self.l_margin, self.get_y(), self.uw, 6 + 5.5 * len(lines))
        self.set_font("CN", "B", 10)
        self.cell(0, 6, "  签署信息", new_x="LMARGIN", new_y="NEXT")
        self.set_font("CN", "", 9.5)
        for l in lines:
            self.cell(0, 5.5, "  " + l, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def output_bytes(self) -> bytes:
        buf = io.BytesIO()
        self.output(buf)
        return buf.getvalue()


# ============================================================================ medical record
def build_record_pdf(record_no: str, version: dict, snapshot: dict, *, status: str, kind: str, finalized_at: str, doctor_line: str,
                     version_no: int, signature_hash: str, content_hash: str) -> bytes:
    banner = ("已定稿病历" if kind == "final" else "更正后病历（已定稿后更正）" if kind == "amendment" else "草稿 — 未定稿，不具备病历效力",
              NAVY if kind in ("final", "amendment") else RED)
    d = Doc("门诊病历", f"{record_no} · v{version_no}", banner)
    p, enc = snapshot.get("patient", {}), snapshot.get("encounter", {})
    d.h2("患者信息")
    d.kv([("姓名", p.get("name")), ("门诊号", p.get("mrn")), ("性别", p.get("gender")), ("年龄", f"{p['age']}岁" if p.get("age") is not None else None),
          ("联系电话", p.get("phone")), ("就诊号", enc.get("no")), ("就诊日期", (enc.get("created_at") or "")[:10]), ("接诊医师", enc.get("doctor"))])
    from server.domain.records import SECTIONS
    secs = (version.get("sections") or {})
    for key, title in SECTIONS:
        txt = (secs.get(key) or {}).get("text", "").strip()
        d.h2(title)
        d.para(txt if txt else "（未填写）", color=(0, 0, 0) if txt else GREY)
    rxs = snapshot.get("prescriptions") or []
    if rxs:
        d.h2("处方明细（已签署）")
        for r in rxs:
            tag = {"western": "西药", "patent": "中成药", "herbal": "中药饮片"}.get(r["type"], r["type"])
            d.para(f"{r['no']}  {tag}  {r.get('title', '')}", bold=True, size=10)
            if r["type"] == "herbal":
                d.table(["药名", "剂量(g)", "特殊煎法"], [[i["name"], f"{i['dose_g']:g}" if i.get("dose_g") else "—", i.get("special") or ""] for i in r["items"]],
                        [3, 1.5, 2], size=9)
                d.para(f"共 {r.get('doses_count') or '?'} 剂；{r.get('decoction') or ''} {r.get('herbal_usage') or ''}".strip(), size=9, color=GREY)
            else:
                d.table(["药品", "规格", "单次", "频次", "途径", "天数"], [[i["name"], i.get("spec"), i.get("dose_text"), i.get("frequency"), i.get("route"), i.get("days")] for i in r["items"]],
                        [3.2, 1.6, 1.2, 1, 1, 0.8], size=9)
            if r.get("his", {}).get("status") == "mock_submitted":
                d.para(f"HIS：模拟提交（{r['his'].get('ref')}），未连接真实 HIS", size=8.5, color=AMBER)
    d.sign_block([f"医师：{doctor_line or '—'}", f"状态：{'定稿' if kind == 'final' else '更正' if kind == 'amendment' else '草稿'}  时间：{finalized_at or '—'}",
                  f"版本 v{version_no}  内容校验码：{content_hash[:16]}", f"签署校验码：{signature_hash[:16] or '—'}"])
    d.para("本病历由医师审核确认；AI 辅助分析仅供参考，不构成诊断依据。", size=8.5, color=GREY)
    return d.output_bytes()


# ============================================================================ prescription
def build_rx_pdf(rx: dict, patient: dict, doctor: dict, encounter_no: str, dx_names: list[str]) -> bytes:
    st = rx["status"]
    banner = {"draft": ("草稿 — 未签署，不可调剂", RED), "returned": ("药房退回 — 未生效", RED), "voided": ("已作废", RED),
              "signed": ("已签署", NAVY), "pharmacy_review": ("药房审核中", NAVY), "approved": ("药房审核通过", NAVY), "dispensed": ("已发药", NAVY)}.get(st, (st, NAVY))
    d = Doc({"western": "西药处方笺", "patent": "中成药处方笺", "herbal": "中药饮片处方笺"}.get(rx["rx_type"], "处方笺"), rx["rx_no"], banner)
    d.h2("患者与就诊")
    d.kv([("姓名", patient.get("name")), ("门诊号", patient.get("mrn")), ("性别", patient.get("gender")), ("年龄", f"{patient['age']}岁" if patient.get("age") is not None else None),
          ("就诊号", encounter_no), ("开具日期", (rx.get("signed_at") or rx.get("created_at") or "")[:10])])
    d.para("过敏史：" + ("、".join(patient.get("allergies") or []) or "未记录"), size=9.5, color=RED if patient.get("allergies") else GREY)
    d.para("诊断：" + ("；".join(dx_names) or "未填写"), size=9.5)
    d.h2(rx.get("title") or "处方")
    if rx["rx_type"] == "herbal":
        rows = [[i["name"], f"{i['dose_g']:g}" if i.get("dose_g") else "—", i.get("special") or "", i.get("role") or ""] for i in rx["items"]]
        d.table(["药名", "剂量(g)", "特殊煎法", "君臣佐使"], rows, [3, 1.5, 2, 1.5])
        d.para(f"共 {rx.get('doses_count') or '?'} 剂。煎法：{rx.get('decoction') or '—'}。服法：{rx.get('herbal_usage') or '—'}", size=9.5)
    else:
        rows = [[i["name"], i.get("spec"), i.get("dose_text"), i.get("frequency"), i.get("route"), i.get("days"), i.get("instructions")] for i in rx["items"]]
        d.table(["药品", "规格", "单次剂量", "频次", "途径", "天数", "备注"], rows, [3, 1.6, 1.3, 1, 1, 0.8, 1.6])
    if rx.get("notes"):
        d.para("医嘱：" + rx["notes"], size=9.5)
    ov = rx.get("override_reasons") or []
    if ov:
        d.h2("医师确认的用药提示")
        for o in ov:
            d.para(f"· [{o.get('severity')}] {o.get('message')}" + (f"  理由：{o['reason']}" if o.get("reason") not in ("", "ack", None) else "  （已确认）"), size=8.8, color=AMBER)
    his = rx.get("his", {})
    if his.get("simulated"):
        d.para(f"HIS：模拟提交，参考号 {his.get('ref')}（未连接真实 HIS，未向外部系统发送医嘱）", size=8.5, color=AMBER)
    d.sign_block([f"处方医师：{doctor.get('name')}  {doctor.get('title') or ''}", f"执业证号：{doctor.get('license_no') or '—'}",
                  f"签署时间：{rx.get('signed_at') or '未签署'}", f"签署校验码：{(rx.get('signature_hash') or '')[:16] or '—'}"])
    d.para("请在药师审核后调配。AI 辅助起草的内容均已由处方医师逐项审核。", size=8.5, color=GREY)
    return d.output_bytes()
