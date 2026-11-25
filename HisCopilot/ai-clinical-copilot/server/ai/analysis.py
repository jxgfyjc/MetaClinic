"""Analysis pipeline: facts → (western ∥ tcm [+ tongue]) → synthesis, with validated, evidence-linked output.

Design points
  * Input = the numbered fact sheet (server/ai/facts.py). The run is bound to `inputs_hash`/`inputs_version`.
  * Each module is isolated: failure of one never discards the others (status `partial`) and is stored with
    a machine-readable error code + the raw model excerpt for troubleshooting.
  * Validation: every cited fact id must exist; unknown ids are DROPPED and counted in `output.validation`
    (so the UI can show "已移除 N 条无法核实的引用"). Diagnoses lacking any valid supporting reference are kept
    but flagged `evidence_weak`.
  * No DB access inside model calls. The caller (job handler) passes plain data; persistence happens in short
    transactions so a 60 s model call never holds a SQLite write lock.
  * LLM calls are serialised through one process-wide lock because the GPU server does not speed up under
    concurrency (measured: 2 parallel requests = 2× latency each).
"""
from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from server.ai import client
from server.ai.client import ModelError, chat_json, load_prompt
from server.ai.facts import Fact, facts_prompt_block, valid_ids
from server.domain.enums import ModuleStatus

LLM_LOCK = threading.Lock()
_RETRY_NOTE = "\n\n（重要提醒：上一次输出缺少 `{key}` 数组，或其中的每一项没有 `name` 字段。请严格按系统提示中的 JSON 结构重新输出：`{key}` 至少 2 项，每项必须包含 name、confidence、rationale、supporting。只输出 JSON。）"


def _refs_ok(items: Any, ok: set[str], stats: dict) -> list[dict]:
    """Normalise [{text, refs}] lists; drop unknown refs."""
    out: list[dict] = []
    for it in items or []:
        if isinstance(it, str):
            it = {"text": it, "refs": []}
        if not isinstance(it, dict) or not str(it.get("text", "")).strip():
            continue
        refs = [r for r in (it.get("refs") or []) if isinstance(r, str)]
        good = [r for r in refs if r in ok]
        stats["dropped_refs"] += len(refs) - len(good)
        out.append({"text": str(it["text"]).strip(), "refs": good})
    return out


def _str_list(x: Any, limit: int = 12) -> list[str]:
    return [str(i).strip() for i in (x or []) if str(i).strip()][:limit] if isinstance(x, list) else []


_NAME_ALIASES = ("name", "diagnosis", "diagnosis_name", "syndrome", "syndrome_name", "disease", "title")
_WEST_KEYS = ("differentials", "differential_diagnoses", "differential_diagnosis", "diagnoses", "candidates")
_TCM_KEYS = ("syndromes", "syndrome_candidates", "patterns", "pattern_candidates", "candidates")


def _pick_list(d: dict, keys: tuple[str, ...]) -> list:
    """The candidate list under the documented key — or a common variant the model used instead (it is not always literal)."""
    for k in keys:
        v = d.get(k)
        if isinstance(v, list) and v:
            return v
    return []


def _norm_candidates(raw: list, ok: set[str], stats: dict, name_key: str) -> list[dict]:
    out = []
    for c in raw or []:
        if isinstance(c, dict) and not str(c.get("name", "")).strip():
            alias = next((c[a] for a in _NAME_ALIASES if isinstance(c.get(a), str) and c[a].strip()), "")
            c = {**c, "name": alias}
        if not isinstance(c, dict) or not str(c.get("name", "")).strip():
            continue
        sup = _refs_ok(c.get("supporting"), ok, stats)
        conf = str(c.get("confidence", "low")).lower()
        out.append({
            "key": "", "name": str(c["name"]).strip()[:200], "confidence": conf if conf in ("low", "medium", "high") else "low",
            "rationale": str(c.get("rationale", "")).strip()[:600], "supporting": sup,
            "contradicting": _refs_ok(c.get("contradicting"), ok, stats),
            "missing_information": _str_list(c.get("missing_information")),
            "questions_to_ask": _str_list(c.get("questions_to_ask")),
            "evidence_weak": not any(s["refs"] for s in sup),
        })
    for i, c in enumerate(out):
        c["key"] = f"{name_key}{i + 1}"
    return out[:5]


# ------------------------------------------------------------------ modules
def run_western(facts: list[Fact], evidence: list[dict]) -> dict:
    system, ver = load_prompt("western_v2")
    ok, stats = valid_ids(facts), {"dropped_refs": 0}
    user = f"FACTS:\n{facts_prompt_block(facts)}\n\nEVIDENCE:\n{_evidence_block(evidence)}"
    with LLM_LOCK:
        d, res = chat_json(system, user, role="main", max_tokens=1500)
        cands = _norm_candidates(_pick_list(d, _WEST_KEYS), ok, stats, "w")
        if not cands:                    # observed with the real 27B: a well-formed JSON object WITHOUT a usable differentials list — ask once more
            d, res = chat_json(system, user + _RETRY_NOTE.format(key="differentials"), role="main", max_tokens=1700)
            cands = _norm_candidates(_pick_list(d, _WEST_KEYS), ok, stats, "w")
    if not cands:
        raise ModelError("no_candidates", "模型未给出任何可用的诊断候选", raw=json.dumps(d, ensure_ascii=False))
    return {"summary": str(d.get("summary", "")).strip()[:500], "problem_list": _str_list(d.get("problem_list")),
            "candidates": cands, "red_flags": _refs_ok(d.get("red_flags"), ok, stats),
            "suggested_examinations": _str_list(d.get("suggested_examinations")), "uncertainties": _str_list(d.get("uncertainties")),
            "missing_information": _str_list(d.get("missing_information")),
            "validation": stats, "_model": res.model, "_prompt": ver, "_latency_ms": res.latency_ms}


def run_tcm(facts: list[Fact], evidence: list[dict], tongue: dict | None) -> dict:
    system, ver = load_prompt("tcm_v2")
    ok, stats = valid_ids(facts), {"dropped_refs": 0}
    user = f"FACTS:\n{facts_prompt_block(facts)}\n\nTONGUE:\n{_tongue_block(tongue)}\n\nEVIDENCE:\n{_evidence_block(evidence)}"
    with LLM_LOCK:
        d, res = chat_json(system, user, role="tcm", max_tokens=1300)
        cands = _norm_candidates(_pick_list(d, _TCM_KEYS), ok, stats, "t")
        if not cands:
            d, res = chat_json(system, user + _RETRY_NOTE.format(key="syndromes"), role="tcm", max_tokens=1500)
            cands = _norm_candidates(_pick_list(d, _TCM_KEYS), ok, stats, "t")
    if not cands:
        raise ModelError("no_candidates", "模型未给出任何可用的证候候选", raw=json.dumps(d, ensure_ascii=False))
    four = d.get("four_diagnoses") or {}
    return {"summary": str(d.get("summary", "")).strip()[:500],
            "four_diagnoses": {k: _refs_ok(four.get(k), ok, stats) for k in ("wang", "wen", "wen_ask", "qie")},
            "missing_four_diagnoses": _str_list(d.get("missing_four_diagnoses")), "candidates": cands,
            "treatment_principles": _str_list(d.get("treatment_principles")),
            "formula_directions": [x for x in (d.get("formula_directions") or []) if isinstance(x, dict) and x.get("name")][:4],
            "uncertainties": _str_list(d.get("uncertainties")), "missing_information": _str_list(d.get("missing_information")),
            "validation": stats, "_model": res.model, "_prompt": ver, "_latency_ms": res.latency_ms}


def run_tongue(image_path: Path) -> dict:
    system, ver = load_prompt("tongue_v2")
    if not image_path.exists():
        raise ModelError("image_missing", "舌象图片文件不存在（可能被清理）", retryable=False)
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    with LLM_LOCK:
        d, res = chat_json(system, "请描述这张舌象图片。", role="main", max_tokens=900, image_b64=b64)
    q = d.get("image_quality") or {}
    d["_usable"] = bool(q.get("usable", True))
    d["_model"], d["_prompt"], d["_latency_ms"] = res.model, ver, res.latency_ms
    return d


def run_synthesis(facts: list[Fact], western: dict | None, tcm: dict | None) -> dict:
    system, ver = load_prompt("synthesis_v2")
    ok, stats = valid_ids(facts), {"dropped_refs": 0}
    brief = {"western": [{"name": c["name"], "confidence": c["confidence"], "rationale": c["rationale"]} for c in (western or {}).get("candidates", [])],
             "western_red_flags": [r["text"] for r in (western or {}).get("red_flags", [])],
             "tcm": [{"name": c["name"], "confidence": c["confidence"], "rationale": c["rationale"]} for c in (tcm or {}).get("candidates", [])],
             "missing": (western or {}).get("missing_information", []) + (tcm or {}).get("missing_four_diagnoses", [])}
    import json
    user = f"FACTS:\n{facts_prompt_block(facts)}\n\nANALYSES:\n{json.dumps(brief, ensure_ascii=False)}"
    with LLM_LOCK:
        d, res = chat_json(system, user, role="main", max_tokens=900)
    return {"headline": str(d.get("headline", "")).strip()[:300], "key_decisions": _str_list(d.get("key_decisions")),
            "confirm_first": _str_list(d.get("confirm_first")), "risk_alerts": _refs_ok(d.get("risk_alerts"), ok, stats),
            "conflicts": _str_list(d.get("conflicts")), "coordination_notes": _str_list(d.get("coordination_notes")),
            "suggested_diagnosis_set": [x for x in (d.get("suggested_diagnosis_set") or []) if isinstance(x, dict) and x.get("name")][:6],
            "validation": stats, "_model": res.model, "_prompt": ver, "_latency_ms": res.latency_ms}


# ------------------------------------------------------------------ helpers
def _evidence_block(evidence: list[dict]) -> str:
    if not evidence:
        return "（知识库未提供证据；请不要引用任何文献来源）"
    return "\n".join(f"E{i + 1} | {e.get('title', '')} | {e.get('excerpt', '')[:300]}" for i, e in enumerate(evidence[:6]))


def _tongue_block(t: dict | None) -> str:
    if not t:
        return "（未提供舌象）"
    if not t.get("_usable", True):
        return "（舌象图片质量不合格，不可用：" + "；".join((t.get("image_quality") or {}).get("issues", [])) + "）"
    parts = [f"观察：{'；'.join(t.get('observations', []))}", f"未见：{'；'.join(t.get('not_observed', []))}",
             f"不确定：{'；'.join(t.get('uncertain', []))}（以上来自图像模型，需医师核对）"]
    return "\n".join(parts)
