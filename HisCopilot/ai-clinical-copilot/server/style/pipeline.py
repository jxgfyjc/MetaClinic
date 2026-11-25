"""导入流水线的数据库侧：存源文件 → 解析结果脱敏入库为病例 → 去重/异常 → 状态流转。

隐私约束：脱敏前的原文只存在于（a）上传的源文件（抽取完成/取消后清除，或手动清除）和（b）解析时的内存里；
`style_cases.text` 永远是脱敏后的文本。
"""
from __future__ import annotations

import io
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.config import get_settings
from server.db.models import StyleCase, StyleImport, StyleSource
from server.domain.enums import StyleCaseStatus, StyleImportStatus
from server.domain.ids import sha256_hex, utcnow
from server.errors import Unprocessable
from server.style import cases as C
from server.style import parse as P
from server.style.redact import redact

MAX_FILES_PER_IMPORT = 40
MAX_CASES_PER_IMPORT = 3000


def source_path(key: str) -> Path:
    return get_settings().objects_dir / key


def store_source(db: Session, imp: StyleImport, data: bytes, filename: str) -> StyleSource:
    s = get_settings()
    if not data:
        raise Unprocessable("文件内容为空", code="empty_file")
    if len(data) > s.max_style_file_mb * 1024 * 1024:
        raise Unprocessable(f"文件过大（上限 {s.max_style_file_mb} MB），请拆分后上传", code="file_too_large")
    n = len(db.scalars(select(StyleSource.id).where(StyleSource.import_id == imp.id)).all())
    if n >= MAX_FILES_PER_IMPORT:
        raise Unprocessable(f"单个导入批次最多 {MAX_FILES_PER_IMPORT} 个文件", code="too_many_files")
    kind = P.sniff_kind(data, filename)                        # 内容判定；不支持的类型在这里就拒绝（含 zip 炸弹检查）
    digest = sha256_hex(data)
    dup = db.scalar(select(StyleSource).where(StyleSource.import_id == imp.id, StyleSource.sha256 == digest))
    if dup is not None:
        raise Unprocessable(f"该文件已在本批次中（{dup.original_name}），已忽略重复上传", code="duplicate_file")
    ext = {"image": ".img", "pdf": ".pdf", "docx": ".docx", "xlsx": ".xlsx", "csv": ".csv", "json": ".json", "txt": ".txt"}[kind]
    key = f"sty_{secrets.token_hex(10)}{ext}"
    s.objects_dir.mkdir(parents=True, exist_ok=True)
    (s.objects_dir / key).write_bytes(data)
    src = StyleSource(import_id=imp.id, kind=kind, original_name=(filename or "")[:200], storage_key=key, size_bytes=len(data), sha256=digest, parse_status="pending")
    db.add(src)
    db.flush()
    return src


def purge_sources(db: Session, imp: StyleImport) -> int:
    n = 0
    for src in db.scalars(select(StyleSource).where(StyleSource.import_id == imp.id)):
        if src.storage_key:
            p = source_path(src.storage_key)
            try:
                p.unlink(missing_ok=True)
                n += 1
            except OSError:
                pass
            src.storage_key = ""
    imp.sources_purged_at = utcnow()
    return n


def ocr_bytes(data: bytes) -> tuple[str, str]:
    """图片 → OCR 文本。先用 Pillow 校验并缩放到 ≤1600px JPEG（既防畸形图片又加速 OCR）。→ (text, engine)"""
    from PIL import Image, ImageOps
    from server.ai import client
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            im = ImageOps.exif_transpose(im).convert("RGB")
            if max(im.size) < 64:
                raise Unprocessable("图片尺寸过小，无法识别", code="image_too_small")
            if max(im.size) > 1600:
                im.thumbnail((1600, 1600))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88)
    except Unprocessable:
        raise
    except Exception as e:                                       # noqa: BLE001
        raise Unprocessable(f"图片损坏或格式不受支持：{type(e).__name__}", code="bad_image") from e
    text, engine, _ms = client.ocr_image(buf.getvalue(), "image/jpeg")
    return text, engine


def ingest(db: Session, imp: StyleImport, src: StyleSource | None, parsed: P.ParsedSource | None, *, text_blocks: list[str] | None = None,
           origin: str = "text", kind_override: str | None = None, label: str = "") -> int:
    """解析结果 → 脱敏 → StyleCase。返回新增病例数。`text_blocks` 用于粘贴/OCR 文本。"""
    mapping = (imp.config_json or {}).get("mapping") or None
    label = label or (src.original_name if src else "粘贴文本")
    if parsed is not None and parsed.rows:
        raws, info = C.cases_from_rows(parsed.rows, parsed.columns, mapping, label)
        if src is not None:
            src.parsed_json = {"columns": parsed.columns, "mapping": info["mapping"], "unmapped": info["unmapped"], "rows": info["rows"], "merged_rows": info["merged_rows"],
                               "notes": parsed.notes}
        blocks_raws = C.cases_from_blocks(parsed.blocks, label, origin) if parsed.blocks else []
        raws = raws + blocks_raws
    else:
        blocks = text_blocks if text_blocks is not None else (parsed.blocks if parsed else [])
        raws = C.cases_from_blocks(blocks, label, origin)
        if src is not None and parsed is not None:
            src.parsed_json = {"blocks": len(parsed.blocks), "pages": parsed.pages, "notes": parsed.notes}
    have = len(db.scalars(select(StyleCase.id).where(StyleCase.import_id == imp.id)).all())
    if have + len(raws) > MAX_CASES_PER_IMPORT:
        raise Unprocessable(f"单个批次最多 {MAX_CASES_PER_IMPORT} 个病例，请拆分导入", code="too_many_cases")
    made = 0
    for rc in raws:
        r = redact(rc.text, rc.names)
        text = r.text.strip()
        if not text:
            continue
        herbs = C.parse_herbs(text) if rc.kind == "case" else []
        rc.text, rc.herbs = text, herbs
        anomalies = C.anomalies_for(rc) if rc.kind == "case" else rc.anomalies
        anomalies = [a for a in anomalies if a["code"] not in ("merged_rows",)] + [a for a in rc.anomalies if a["code"] == "merged_rows"]
        if origin == "ocr":
            anomalies.append({"code": "ocr_unverified", "severity": "warn", "message": "文本来自 OCR，可能有错字/漏字，请对照原图核对（尤其药名与克数）"})
        if r.risk != "low":
            anomalies.append({"code": "redaction_risk", "severity": "warn" if r.risk == "medium" else "error",
                              "message": "脱敏后仍有疑似个人信息：" + "；".join(r.residual[:3])})
        kind = kind_override or rc.kind
        c = StyleCase(profile_id=imp.profile_id, import_id=imp.id, source_id=src.id if src else None, seq=have + made + 1, kind=kind, origin=rc.origin,
                      status=StyleCaseStatus.PENDING_REVIEW.value, section=rc.section[:80], source_label=rc.label[:160], text=text, content_hash=C.content_hash(text),
                      redaction_json={"counts": r.counts, "risk": r.risk, "residual": r.residual}, anomaly_json=anomalies, herbs_json=herbs,
                      visit_date=rc.date, link_key=sha256_hex(rc.link_key_raw)[:24] if rc.link_key_raw else "")
        db.add(c)
        made += 1
    db.flush()
    return made


def mark_duplicates(db: Session, imp: StyleImport) -> dict:
    """批次内 + 与本画像已有病例比对。精确重复自动排除；近似重复只标记，由医师决定。"""
    existing = db.scalars(select(StyleCase).where(StyleCase.profile_id == imp.profile_id, StyleCase.import_id != imp.id,
                                                  StyleCase.status != StyleCaseStatus.EXCLUDED.value).order_by(StyleCase.created_at)).all()
    mine = db.scalars(select(StyleCase).where(StyleCase.import_id == imp.id).order_by(StyleCase.seq)).all()
    items = [(c.id, C.norm_for_hash(c.text), c.herbs_json or [], c.visit_date or "") for c in existing + mine if c.kind == "case"]
    hits = C.find_duplicates(items)
    by_id = {c.id: c for c in mine}
    n_exact = n_near = n_revisit = 0
    for cid, (orig, score, kind) in hits.items():
        c = by_id.get(cid)
        if c is None:
            continue
        if kind == "revisit":                                   # 同方不同日：复诊，不是重复录入——不标 dup_of，不阻止确认，只做提示
            c.anomaly_json = list(c.anomaly_json or []) + [{"code": "possible_revisit", "severity": "info",
                                                            "message": "与另一份病例内容相同但就诊日期不同，可能是同方复诊，已作为独立病例计入（如实为重复录入请手动排除）"}]
            n_revisit += 1
            continue
        c.dup_of_id, c.dup_score = orig, score
        if kind == "exact":
            c.status = StyleCaseStatus.EXCLUDED.value
            c.anomaly_json = list(c.anomaly_json or []) + [{"code": "duplicate_exact", "severity": "info", "message": "与已有病例内容完全相同，已自动排除（可手动恢复）"}]
            n_exact += 1
        else:
            c.anomaly_json = list(c.anomaly_json or []) + [{"code": "duplicate_near", "severity": "warn", "message": f"与另一份病例高度相似（{round(score * 100)}%），可能是重复录入，请核对后决定是否排除"}]
            n_near += 1
    return {"duplicates_exact": n_exact, "duplicates_near": n_near, "possible_revisits": n_revisit}


def refresh_stats(db: Session, imp: StyleImport) -> dict:
    cases = db.scalars(select(StyleCase).where(StyleCase.import_id == imp.id)).all()
    srcs = db.scalars(select(StyleSource).where(StyleSource.import_id == imp.id)).all()
    st = {"files": len([s for s in srcs if s.storage_key or s.kind != "paste"]), "sources_failed": len([s for s in srcs if s.parse_status in ("failed", "unsupported")]),
          "cases": len([c for c in cases if c.kind == "case"]), "notes": len([c for c in cases if c.kind == "note"]),
          "pending": len([c for c in cases if c.status == StyleCaseStatus.PENDING_REVIEW.value]),
          "confirmed": len([c for c in cases if c.status == StyleCaseStatus.CONFIRMED.value]),
          "excluded": len([c for c in cases if c.status == StyleCaseStatus.EXCLUDED.value]),
          "with_prescription": len([c for c in cases if c.herbs_json]), "no_prescription": len([c for c in cases if c.kind == "case" and not c.herbs_json]),
          "anomalous": len([c for c in cases if any(a.get("severity") in ("warn", "error") for a in (c.anomaly_json or []))]),
          "redaction_high": len([c for c in cases if (c.redaction_json or {}).get("risk") == "high"]),
          "redaction_medium": len([c for c in cases if (c.redaction_json or {}).get("risk") == "medium"]),
          "duplicates": len([c for c in cases if c.dup_of_id])}
    imp.stats_json = {**(imp.stats_json or {}), **st}
    return st


def finish_parsing(db: Session, imp: StyleImport) -> None:
    mark = mark_duplicates(db, imp)
    st = refresh_stats(db, imp)
    imp.stats_json = {**imp.stats_json, **mark}
    srcs = db.scalars(select(StyleSource).where(StyleSource.import_id == imp.id)).all()
    if st["cases"] + st["notes"] == 0:
        imp.status = StyleImportStatus.FAILED.value
        imp.error_code = "no_cases"
        imp.error_detail = "没有解析出任何病例：" + "；".join(f"{s.original_name}：{s.parse_error}" for s in srcs if s.parse_error)[:600]
    else:
        imp.status, imp.error_code, imp.error_detail = StyleImportStatus.NEEDS_REVIEW.value, "", ""
