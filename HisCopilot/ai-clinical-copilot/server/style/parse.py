"""文件解析：把上传文件统一为 `ParsedSource`（文本块 或 表格行），不碰脱敏、不碰数据库。

支持：txt/md、csv/tsv、json/jsonl、docx、xlsx、pdf（仅含文字层；扫描版 PDF 一期不支持，会明确报 `pdf_scanned`）、图片（交给 OCR 任务）。
安全：类型由**文件内容**判定（魔数），不信任扩展名；大小/行数/页数设上限；docx/xlsx 是 zip，限制解压后体积防 zip bomb；XML 一律不解析外部实体
（openpyxl/python-docx 内部使用 lxml/defusedxml 默认安全配置，此处额外限制 zip 成员总大小）。
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass, field

from server.errors import Unprocessable

MAX_ROWS = 20000
MAX_CHARS = 4_000_000
MAX_PDF_PAGES = 400
MAX_UNZIPPED = 120 * 1024 * 1024
MAX_ZIP_ENTRIES = 3000


@dataclass
class ParsedSource:
    kind: str                                              # txt|csv|json|docx|xlsx|pdf|image
    blocks: list[str] = field(default_factory=list)        # 文本块（txt/docx/pdf 每段）
    rows: list[dict] = field(default_factory=list)         # 表格行（csv/xlsx/json）：{列名: 值}
    columns: list[str] = field(default_factory=list)
    pages: int = 0
    notes: list[str] = field(default_factory=list)
    needs_ocr: bool = False

    @property
    def chars(self) -> int:
        return sum(len(b) for b in self.blocks) + sum(len(str(v)) for r in self.rows for v in r.values())


# --------------------------------------------------------------------------- sniffing
def sniff_kind(data: bytes, filename: str = "") -> str:
    """Decide from content; the extension only disambiguates text formats (which have no magic bytes)."""
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return "image"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                _check_zip(z)
                names = set(z.namelist())
        except zipfile.BadZipFile:
            raise Unprocessable("文件已损坏（不是有效的压缩文档）", code="bad_file") from None
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsx"
        raise Unprocessable("不支持的压缩文档类型（仅支持 .docx / .xlsx）", code="unsupported_file")
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise Unprocessable("不支持旧版 .doc/.xls 格式，请另存为 .docx / .xlsx 后上传", code="legacy_office")
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if b"\x00" in data[:4096]:
        raise Unprocessable("无法识别的二进制文件类型", code="unsupported_file")
    if ext in ("csv", "tsv"):
        return "csv"
    if ext in ("json", "jsonl", "ndjson"):
        return "json"
    return "txt"


def _check_zip(z: zipfile.ZipFile) -> None:
    infos = z.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise Unprocessable("压缩文档内文件过多，已拒绝", code="zip_bomb")
    if sum(i.file_size for i in infos) > MAX_UNZIPPED:
        raise Unprocessable("文档解压后体积异常，已拒绝", code="zip_bomb")


def decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            t = data.decode(enc)
            if enc == "utf-16" and not (data[:2] in (b"\xff\xfe", b"\xfe\xff")):
                continue
            return t
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- parsers
def parse(data: bytes, filename: str = "") -> ParsedSource:
    kind = sniff_kind(data, filename)
    if kind == "image":
        return ParsedSource(kind="image", needs_ocr=True)
    fn = {"txt": _parse_txt, "csv": _parse_csv, "json": _parse_json, "docx": _parse_docx, "xlsx": _parse_xlsx, "pdf": _parse_pdf}[kind]
    ps = fn(data)
    if ps.chars > MAX_CHARS:
        raise Unprocessable("文件内容过大，请拆分后分批导入", code="too_large")
    if not ps.blocks and not ps.rows:
        raise Unprocessable("没有解析到任何文字内容", code="empty_content")
    return ps


def _split_paragraphs(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [b.strip() for b in text.split("\n\n") if b.strip()] if "\n\n" in text else [text.strip()] if text.strip() else []


def _parse_txt(data: bytes) -> ParsedSource:
    t = decode_text(data)
    return ParsedSource(kind="txt", blocks=_split_paragraphs(t))


def _parse_csv(data: bytes) -> ParsedSource:
    t = decode_text(data)
    sample = t[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel_tab if sample.count("\t") > sample.count(",") else csv.excel
    rd = csv.reader(io.StringIO(t), dialect)
    rows = [r for r in rd if any(c.strip() for c in r)]
    if not rows:
        return ParsedSource(kind="csv")
    cols = _clean_header(rows[0])
    out = []
    for r in rows[1:MAX_ROWS + 1]:
        out.append({cols[i]: (r[i].strip() if i < len(r) else "") for i in range(len(cols))})
    ps = ParsedSource(kind="csv", rows=out, columns=cols)
    if len(rows) - 1 > MAX_ROWS:
        ps.notes.append(f"超过 {MAX_ROWS} 行，仅读取前 {MAX_ROWS} 行")
    return ps


def _clean_header(h: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for i, c in enumerate(h):
        c = (c or "").strip().replace("\n", " ") or f"列{i + 1}"
        if c in seen:
            seen[c] += 1
            c = f"{c}#{seen[c]}"
        else:
            seen[c] = 1
        out.append(c)
    return out


def _parse_json(data: bytes) -> ParsedSource:
    t = decode_text(data).strip()
    try:
        v = json.loads(t)
        items = v if isinstance(v, list) else (v.get("cases") or v.get("records") or v.get("data") or [v]) if isinstance(v, dict) else []
    except ValueError:
        items = []
        for ln in t.splitlines():                         # jsonl
            ln = ln.strip()
            if ln:
                try:
                    items.append(json.loads(ln))
                except ValueError:
                    raise Unprocessable("JSON 格式不正确（也不是 JSON Lines）", code="bad_json") from None
    rows, cols = [], []
    for it in items[:MAX_ROWS]:
        if isinstance(it, dict):
            row = {str(k): (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)) for k, v in it.items()}
            rows.append(row)
            for k in row:
                if k not in cols:
                    cols.append(k)
        elif isinstance(it, str):
            rows.append({"文本": it})
            if "文本" not in cols:
                cols.append("文本")
    return ParsedSource(kind="json", rows=rows, columns=cols)


def _parse_docx(data: bytes) -> ParsedSource:
    import docx                                            # python-docx
    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as e:                                 # noqa: BLE001
        raise Unprocessable(f"无法读取 Word 文档：{type(e).__name__}", code="bad_file") from e
    blocks: list[str] = []
    rows: list[dict] = []
    cols: list[str] = []
    for p in d.paragraphs:
        if p.text.strip():
            blocks.append(p.text.strip())
    for tb in d.tables:                                    # 表格：首行当表头；只有 ≥2 行 ≥2 列才当病例表
        grid = [[c.text.strip() for c in r.cells] for r in tb.rows]
        if len(grid) >= 2 and len(grid[0]) >= 2:
            h = _clean_header(grid[0])
            if not cols:
                cols = h
            for r in grid[1:MAX_ROWS + 1]:
                if any(r):
                    rows.append({h[i]: (r[i] if i < len(r) else "") for i in range(len(h))})
        else:
            for r in grid:
                t = " ".join(x for x in r if x)
                if t:
                    blocks.append(t)
    ps = ParsedSource(kind="docx", blocks=_merge_short_lines(blocks), rows=rows, columns=cols)
    return ps


def _merge_short_lines(lines: list[str]) -> list[str]:
    """Word 常按行分段：把"病例标题行"之间的内容合成一个块，交给 cases.split 再拆；这里只做保守合并——整篇一个块，由病例切分器决定边界。"""
    return ["\n".join(lines)] if lines else []


def _parse_xlsx(data: bytes) -> ParsedSource:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:                                 # noqa: BLE001
        raise Unprocessable(f"无法读取 Excel 文件：{type(e).__name__}", code="bad_file") from e
    rows: list[dict] = []
    cols: list[str] = []
    notes: list[str] = []
    for ws in wb.worksheets:
        it = ws.iter_rows(values_only=True)
        head = None
        for r in it:
            if r and any(c not in (None, "") for c in r):
                head = [("" if c is None else str(c)) for c in r]
                break
        if head is None:
            continue
        h = _clean_header(head)
        if not cols:
            cols = h
        elif h != cols:
            notes.append(f"工作表「{ws.title}」表头与第一个工作表不同，其列按各自表头读取")
        n = 0
        for r in it:
            if not r or not any(c not in (None, "") for c in r):
                continue
            rows.append({h[i]: ("" if i >= len(r) or r[i] is None else _cell(r[i])) for i in range(len(h))})
            n += 1
            if len(rows) >= MAX_ROWS:
                notes.append(f"超过 {MAX_ROWS} 行，已截断")
                break
        if len(rows) >= MAX_ROWS:
            break
    wb.close()
    allcols = list(dict.fromkeys(cols + [k for r in rows for k in r]))
    return ParsedSource(kind="xlsx", rows=rows, columns=allcols, notes=notes)


def _cell(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return str(v).strip()


def _parse_pdf(data: bytes) -> ParsedSource:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        rd = PdfReader(io.BytesIO(data))
        if rd.is_encrypted:
            try:
                rd.decrypt("")
            except Exception:                              # noqa: BLE001
                raise Unprocessable("PDF 已加密，请先解除密码后上传", code="pdf_encrypted") from None
        n = len(rd.pages)
        if n > MAX_PDF_PAGES:
            raise Unprocessable(f"PDF 页数过多（{n} 页，上限 {MAX_PDF_PAGES}），请拆分", code="too_large")
        texts = []
        for pg in rd.pages:
            try:
                texts.append((pg.extract_text() or "").strip())
            except Exception:                              # noqa: BLE001
                texts.append("")
    except PdfReadError as e:
        raise Unprocessable(f"无法读取 PDF：{e}", code="bad_file") from e
    body = "\n\n".join(t for t in texts if t)
    if len(body.replace("\n", "").strip()) < 20 * max(1, n) * 0.2 and n:
        raise Unprocessable("这是扫描版/图片版 PDF（没有可提取的文字层）。一期暂不支持，请把页面导出为图片后上传（将走 OCR），或提供文字版",
                            code="pdf_scanned")
    return ParsedSource(kind="pdf", blocks=[body], pages=n)
