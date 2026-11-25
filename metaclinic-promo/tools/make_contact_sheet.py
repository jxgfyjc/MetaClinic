import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SHOTS_DIR = Path("/cephfs/yujc/metaclinic-promo/phase1/screenshots")
OUT_DIR = Path("/cephfs/yujc/metaclinic-promo/phase1")
ARTIFACT_DIR = Path("/root/.gemini/antigravity/brain/c5bf124a-28e4-48d5-9ce1-93bde492a2c0")
FONT_PATH = "/cephfs/yujc/HisCopilot/ai-clinical-copilot/assets/fonts/cn-medium.otf"

CAPTIONS = {
    "01_login_metaclinic": ("MetaClinic 登录与本地私有化部署声明 (/login)", "[本次优化]"),
    "02_style_import_input": ("医师经验资料输入 (/style/[pid]#imports)", "[原有功能]"),
    "03_style_import_parsed_cases": ("example.md 解析脱敏与病例核对", "[本次修复]"),
    "04_style_extract_done": ("本机模型抽取完成（操作区置顶）", "[本次优化]"),
    "05_style_entries_pending": ("待审核经验条目列表 (/style/[pid]#review)", "[本次新增]"),
    "06_style_bulk_selected": ("多选待审核条目与选中数量悬浮栏", "[本次新增]"),
    "07_style_bulk_confirm": ("批量通过二次确认弹窗", "[本次新增]"),
    "08_style_bulk_result": ("批量审核逐条处理结果反馈", "[本次新增]"),
    "09_style_entries_persisted": ("批量审核后刷新持久化状态", "[本次新增]"),
    "10_style_profile_overview": ("经验画像概览与版本发布 (/style/[pid])", "[原有功能]"),
    "11_style_profile_library": ("经验画像详情（共享使用视角）", "[原有功能]"),
    "12_workstation_home": ("MetaClinic 医师工作台主界面 (/)", "[本次优化]"),
    "13_encounter_intake": ("紧凑患者接诊与病史事实 (/encounters/[eid])", "[本次优化]"),
    "14_encounter_ai_analysis": ("AI 中西医结合辅助分析（Qwen3.8 / ShizhenGPT）", "[本次优化]"),
    "15_encounter_diagnosis_review": ("诊断审核、批量拒余项与最终诊断确认", "[本次优化]"),
    "16_encounter_style_selected": ("诊疗中选择已授权经验画像", "[本次优化]"),
    "17_encounter_plan_comparison": ("分栏治疗方案与四栏经验对照", "[本次优化]"),
    "18_encounter_plan_confirmed": ("批量接受理疗项与综合方案确认", "[本次优化]"),
    "19_encounter_rx_populated": ("由方案一键生成的西药与饮片处方", "[本次优化]"),
    "20_encounter_record": ("临床病历结构化草稿与归档 (/encounters/[eid])", "[原有功能]"),
}

TAG_COLORS = {
    "[原有功能]": (37, 99, 235),
    "[本次修复]": (217, 119, 6),
    "[本次新增]": (5, 150, 105),
    "[本次优化]": (124, 58, 237),
}


def build_sheets():
    files = sorted(SHOTS_DIR.glob("*.png"))
    if not files:
        raise RuntimeError(f"No screenshots found in {SHOTS_DIR}")

    font_title = ImageFont.truetype(FONT_PATH, 28)
    font_cap = ImageFont.truetype(FONT_PATH, 20)
    font_tag = ImageFont.truetype(FONT_PATH, 16)

    per_sheet = 6
    cols = 2
    thumb_w, thumb_h = 800, 500
    pad = 28
    header_h = 64
    cap_h = 46

    n_sheets = math.ceil(len(files) / per_sheet)
    for si in range(n_sheets):
        chunk = files[si * per_sheet : (si + 1) * per_sheet]
        rows = math.ceil(len(chunk) / cols)
        w = pad + cols * (thumb_w + pad)
        h = header_h + pad + rows * (thumb_h + cap_h + pad)
        canvas = Image.new("RGB", (w, h), (245, 247, 250))
        draw = ImageDraw.Draw(canvas)

        draw.rectangle([0, 0, w, header_h], fill=(15, 23, 42))
        draw.text((pad, 18), f"MetaClinic 第一阶段真实界面联系表 ({si + 1}/{n_sheets})", fill=(248, 250, 252), font=font_title)

        for idx, fp in enumerate(chunk):
            r, c = divmod(idx, cols)
            x = pad + c * (thumb_w + pad)
            y = header_h + pad + r * (thumb_h + cap_h + pad)

            draw.rectangle([x - 2, y - 2, x + thumb_w + 2, y + thumb_h + cap_h + 2], fill=(255, 255, 255), outline=(203, 213, 225), width=2)
            im = Image.open(fp).convert("RGB").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            canvas.paste(im, (x, y))

            stem = fp.stem
            title, tag = CAPTIONS.get(stem, (stem, "[原有功能]"))
            tag_color = TAG_COLORS.get(tag, (71, 85, 105))
            ty = y + thumb_h + 10
            draw.rounded_rectangle([x + 10, ty, x + 106, ty + 28], radius=6, fill=tag_color)
            draw.text((x + 18, ty + 5), tag, fill=(255, 255, 255), font=font_tag)
            draw.text((x + 118, ty + 3), f"{stem[:2]}. {title}", fill=(30, 41, 59), font=font_cap)

        out_path = OUT_DIR / f"contact_sheet_{si + 1}.png"
        canvas.save(out_path, optimize=True)
        art_path = ARTIFACT_DIR / f"contact_sheet_{si + 1}.png"
        canvas.save(art_path, optimize=True)
        print("Saved:", out_path, "and", art_path)


if __name__ == "__main__":
    build_sheets()
