"""V1.2 §5: Case mining pipeline — OCR → structure → similarity → pattern mining.

Batch async: first 10-20 cases validate structure quality, then scale to ~100.
Uses real LLM (clinical_main) for structuring + pattern mining.
Uses real embedding (Qwen3-Embedding-8B, port 7873) for similarity search.
Does NOT train models — only mines patterns for doctor confirmation.
"""
from __future__ import annotations

import json
import time
from typing import Any

from packages.shared.providers import LLMProvider, AIUnavailable, EmbeddingProvider
from packages.shared.v12_repos import (
    CaseImportRepo, ExperiencePatternRepo,
)
from services.ai_orchestrator.orchestrator import _safe_json


class CaseMiningPipeline:
    """Async pipeline: structure cases → find similarities → mine patterns."""

    def __init__(self, doctor_id: str, case_import_id: str):
        self.doctor_id = doctor_id
        self.case_import_id = case_import_id
        self.structured: list[dict] = []

    async def run(self, cases: list[dict]) -> None:
        """Process cases in batches. Update progress after each case."""
        total = len(cases)
        await CaseImportRepo.update_progress(
            self.case_import_id, status="processing",
            structured_json={"structured": [], "in_progress": True})
        # Phase 1: Structure each case via LLM
        for i, case in enumerate(cases):
            try:
                structured = await self._structure_case(case)
                self.structured.append(structured)
            except Exception as e:
                self.structured.append({
                    "index": i, "error": str(e),
                    "raw": str(case)[:500]})
            await CaseImportRepo.update_progress(
                self.case_import_id,
                processed_count=i + 1,
                structured_json={"structured": self.structured})
        # Phase 2: Find similar cases via embedding
        try:
            clusters = await self._cluster_cases()
        except Exception as e:
            clusters = []
            print(f"[case_mining] clustering failed: {e}", flush=True)
        # Phase 3: Mine patterns from clusters via LLM
        patterns = await self._mine_patterns(clusters)
        await CaseImportRepo.update_progress(
            self.case_import_id, status="done",
            structured_json={"structured": self.structured,
                             "clusters": clusters},
            patterns_json={"patterns": patterns})

    async def _structure_case(self, case: dict) -> dict:
        """Use LLM to extract structured data from a case text.

        If the case has an 'image_path' (uploaded image), OCR it first via
        PaddleOCR-VL, then feed the OCR text + any user-provided text to the LLM.
        """
        case_text = case.get("text", "")
        ocr_text = ""

        # V1.2 §5: if an image was uploaded, OCR it into text first
        image_path = case.get("image_path", "")
        if image_path:
            try:
                ocr_text = await self._ocr_case_image(image_path)
                if ocr_text:
                    case_text = f"{case_text}\n[OCR识别内容]\n{ocr_text}".strip()
            except Exception as e:
                ocr_text = ""
                print(f"[case_mining] OCR failed for {image_path}: {e}", flush=True)

        if not case_text.strip():
            # If we only had an image and OCR failed, try using the raw text
            case_text = case.get("text", "") or json.dumps(case, ensure_ascii=False)
        if not case_text.strip():
            return {"error": "empty case (no text and OCR failed/empty)"}

        prompt = """请从以下病例中提取结构化信息，返回 JSON：
{
  "disease": "主病名",
  "symptoms": ["症状1", "症状2"],
  "syndrome": "中医证型（如有）",
  "diagnosis": "诊断",
  "herbs": ["药味1", "药味2"],
  "doses": {"药味1": "10g", "药味2": "15g"},
  "method": "治法",
  "treatment_goal": "治疗目标",
  "outcome": "疗效"
}
只返回 JSON，不要输出其他内容。"""
        try:
            llm = LLMProvider("clinical_main")
            resp = await llm.chat([
                {"role": "system", "content": prompt},
                {"role": "user", "content": case_text[:3000]},
            ], json_mode=True, timeout=120)
            d = _safe_json(resp.text)
            d["raw_text"] = case_text[:500]
            if ocr_text:
                d["ocr_text"] = ocr_text[:500]
            return d
        except AIUnavailable:
            # Fallback: basic keyword extraction
            return {"disease": "", "symptoms": [], "raw_text": case_text[:500],
                    "error": "LLM unavailable"}

    async def _ocr_case_image(self, image_path: str) -> str:
        """OCR a case image using OCRProvider (PaddleOCR-VL or clinical_main VLM fallback).

        Uses the existing OCRProvider which reads the model registry (models.yaml)
        — the OCR endpoint is configured there, NOT hardcoded. This avoids
        hitting a non-existent port 7875 when the registry routes OCR to
        clinical_main (port 7870) as a fallback.
        """
        import aiofiles
        from pathlib import Path
        from packages.shared.providers import OCRProvider, AIUnavailable

        p = Path(image_path)
        if not p.exists():
            raise FileNotFoundError(f"image not found: {image_path}")

        async with aiofiles.open(str(p), "rb") as f:
            raw = await f.read()

        # Detect mime from extension
        ext = p.suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(ext, "image/png")

        try:
            ocr = OCRProvider("ocr")
            return await ocr.extract(
                raw, mime=mime,
                prompt="请识别图片中的全部文字内容，按原始格式输出，保留表格和数值。")
        except AIUnavailable as e:
            print(f"[case_mining] OCR provider unavailable: {e}", flush=True)
            return ""

    async def _cluster_cases(self) -> list[dict]:
        """Cluster similar cases using embedding similarity."""
        if not self.structured:
            return []
        # Build text representations for embedding
        texts = []
        for s in self.structured:
            text = " ".join(filter(None, [
                s.get("disease", ""), s.get("syndrome", ""),
                s.get("diagnosis", ""),
                " ".join(s.get("symptoms", [])),
                " ".join(s.get("herbs", [])),
                s.get("method", ""),
            ]))
            texts.append(text or s.get("raw_text", ""))
        try:
            emb = EmbeddingProvider("embedding")
            vecs = await emb.embed(texts)
        except (AIUnavailable, Exception) as e:
            # Fallback: simple keyword-based grouping
            return self._keyword_cluster(self.structured)
        # Simple nearest-neighbor clustering (threshold-based)
        clusters: list[list[int]] = []
        assigned: set[int] = set()
        for i, vec in enumerate(vecs):
            if i in assigned:
                continue
            cluster = [i]
            assigned.add(i)
            for j in range(i + 1, len(vecs)):
                if j in assigned:
                    continue
                sim = self._cosine_sim(vec, vecs[j])
                if sim > 0.75:
                    cluster.append(j)
                    assigned.add(j)
            if len(cluster) > 1:
                clusters.append(cluster)
        return [{"indices": c, "size": len(c),
                 "cases": [self.structured[i] for i in c]}
                for c in clusters]

    def _keyword_cluster(self, cases: list[dict]) -> list[dict]:
        """Fallback: group by disease field."""
        groups: dict[str, list[int]] = {}
        for i, c in enumerate(cases):
            disease = c.get("disease", "未分类")
            groups.setdefault(disease, []).append(i)
        return [{"indices": idxs, "size": len(idxs),
                 "cases": [cases[i] for i in idxs]}
                for idxs in groups.values() if len(idxs) > 1]

    @staticmethod
    def _cosine_sim(a, b) -> float:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    async def _mine_patterns(self, clusters: list[dict]) -> list[dict]:
        """Use LLM to mine clinical thinking patterns from case clusters."""
        if not clusters:
            return []
        patterns = []
        for cluster in clusters:
            cases = cluster.get("cases", [])
            if len(cases) < 2:
                continue
            # Build summary of cluster
            summary = json.dumps([{
                "disease": c.get("disease", ""),
                "syndrome": c.get("syndrome", ""),
                "symptoms": c.get("symptoms", []),
                "herbs": c.get("herbs", []),
                "doses": c.get("doses", {}),
                "method": c.get("method", ""),
            } for c in cases[:20]], ensure_ascii=False)
            prompt = f"""分析以下{len(cases)}份相似病例，提炼临床思路规律：
1. 辨证路径：反复出现哪些辨证思路？
2. 方药组合：哪些药对/方剂组合反复出现？
3. 加减规律：在不同兼证下如何加减？
4. 适用条件、例外和不确定性。

返回 JSON：
{{"pattern_text": "规律描述", "category": "syndrome/herb/method",
  "conditions": "适用条件", "exceptions": "例外",
  "confidence": 0.0-1.0}}

只返回 JSON。病例数据：{summary}"""
            try:
                llm = LLMProvider("clinical_main")
                resp = await llm.chat([
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "请分析"},
                ], json_mode=True, timeout=180)
                d = _safe_json(resp.text)
                d["sample_size"] = len(cases)
                d["source_cases"] = [c.get("disease", f"case{i}")
                                     for i, c in enumerate(cases)]
            except (AIUnavailable, Exception) as e:
                d = {"pattern_text": f"(LLM unavailable: {e})",
                     "sample_size": len(cases), "confidence": 0.0}
            # Persist pattern
            rec = await ExperiencePatternRepo.create(
                doctor_id=self.doctor_id,
                case_import_id=self.case_import_id,
                category=d.get("category", "syndrome"),
                pattern_text=d.get("pattern_text", ""),
                source_cases=d.get("source_cases", []),
                sample_size=d.get("sample_size", len(cases)),
                conditions=d.get("conditions", ""),
                exceptions=d.get("exceptions", ""),
                confidence=d.get("confidence", 0.0))
            patterns.append(rec)
        return patterns
