"""Follow-up: schedule generation, reminder state, patient answers → rule-based risk, alerts, messaging.

Risk assessment is RULES FIRST: hard keyword/threshold rules decide the level; the LLM (when available) can only
*add* attention signals — it can never downgrade a rule hit and never decides "high risk" alone. A high-risk hit
creates an alert and keeps the originating task open until a clinician acknowledges.
"""
from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.db.models import (Encounter, FollowupAlert, FollowupMessage, FollowupPlan, FollowupResponse, FollowupTask, Patient)
from server.domain.enums import FuPlanStatus, FuTaskStatus, RiskLevel
from server.domain.ids import utcnow
from server.errors import Conflict, NotFound, Unprocessable
from server.security import audit
from server.security.audit import Actor

HIGH_RISK = ["胸痛", "呼吸困难", "喘不上气", "晕厥", "昏倒", "意识不清", "意识模糊", "意识丧失", "大量出血", "严重出血", "呕血", "咯血", "黑便",
             "便血", "过敏性休克", "严重过敏", "喉头水肿", "抽搐", "高热不退", "严重低血糖", "言语不清", "口角歪斜", "肢体无力", "剧烈头痛",
             "夜间不能平卧", "端坐呼吸", "自杀", "不想活"]
ATTENTION = ["加重", "明显恶化", "越来越", "没有好转", "无好转", "皮疹", "起疹", "瘙痒", "恶心呕吐", "呕吐", "腹泻", "头晕加重", "心慌", "胸闷",
             "视物模糊", "乏力加重", "发热", "失眠加重", "停药", "漏服", "副作用", "不良反应"]
NEGATION = re.compile(r"(无|没有|没|未|不|否认)\s*[^，。；,;]{0,2}$")

DEFAULT_QUESTIONS = ["症状较上次就诊是否好转？（明显好转 / 好转 / 无变化 / 加重）", "是否按医嘱服药？有无漏服？", "服药后有无不适或新出现的症状？",
                     "饮食、睡眠、大小便情况如何？"]


def assess(text: str, answers: dict | None = None) -> dict:
    """Rule-based, negation-aware. Returns {level, hits:[{rule,kind,detail}], reason, recommended_action}."""
    blob = (text or "") + " " + " ".join(str(v) for v in (answers or {}).values())
    hits = []
    for kw in HIGH_RISK:
        for m in re.finditer(re.escape(kw), blob):
            pre = blob[max(0, m.start() - 4):m.start()]
            if NEGATION.search(pre):
                continue                                           # "没有胸痛" is not a hit
            hits.append({"rule": f"kw:{kw}", "kind": "high", "detail": f"提到「{kw}」"})
            break
    for kw in ATTENTION:
        for m in re.finditer(re.escape(kw), blob):
            if NEGATION.search(blob[max(0, m.start() - 4):m.start()]):
                continue
            hits.append({"rule": f"kw:{kw}", "kind": "attention", "detail": f"提到「{kw}」"})
            break
    ans = answers or {}
    worse = str(ans.get("symptom_trend", ans.get("trend", ""))).strip()
    if worse in ("加重", "明显加重", "worse"):
        hits.append({"rule": "trend:worse", "kind": "attention", "detail": "自评症状加重"})
    has_high = any(h["kind"] == "high" for h in hits)
    level = RiskLevel.HIGH.value if has_high else (RiskLevel.ATTENTION.value if hits else RiskLevel.NORMAL.value)
    if has_high:
        reason = "触发高危规则：" + "；".join(h["detail"] for h in hits if h["kind"] == "high")
        action = "请立即联系患者，必要时建议急诊就医。"
    elif hits:
        reason = "需关注：" + "；".join(h["detail"] for h in hits[:4])
        action = "建议 24 小时内电话随访或安排复诊。"
    else:
        reason, action = "未触发风险规则", ""
    return {"level": level, "hits": hits, "reason": reason, "recommended_action": action}


# --------------------------------------------------------------------------- plan / tasks
def propose_plan(db: Session, actor: Actor, enc: Encounter, *, schedule: list[dict] | None = None, revisit_advice: str = "",
                 doctor_id: str | None = None) -> FollowupPlan:
    """Create a PROPOSED plan (no tasks yet; nothing reaches the patient until a doctor activates it)."""
    existing = db.scalar(select(FollowupPlan).where(FollowupPlan.encounter_id == enc.id,
                                                    FollowupPlan.status.in_([FuPlanStatus.PROPOSED.value, FuPlanStatus.ACTIVE.value])))
    if existing:
        return existing
    sched = schedule or [{"day": 3, "kind": "symptom_check", "questions": DEFAULT_QUESTIONS},
                         {"day": 7, "kind": "symptom_check", "questions": DEFAULT_QUESTIONS},
                         {"day": 14, "kind": "revisit_reminder", "questions": ["是否需要预约复诊？"]}]
    plan = FollowupPlan(encounter_id=enc.id, patient_id=enc.patient_id, doctor_id=doctor_id or enc.doctor_id, schedule_json=sched,
                        revisit_advice=revisit_advice, created_by=actor.id)
    db.add(plan)
    db.flush()
    audit.record(db, actor, "followup.propose", entity_type="followup_plan", entity_id=plan.id, encounter_id=enc.id, patient_id=enc.patient_id,
                 summary="生成随访计划草案（待医师确认）")
    return plan


def activate_plan(db: Session, actor: Actor, plan: FollowupPlan, *, schedule: list[dict] | None = None, start=None) -> FollowupPlan:
    if plan.status == FuPlanStatus.ACTIVE.value:
        return plan                                                        # idempotent
    if plan.status != FuPlanStatus.PROPOSED.value:
        raise Conflict("该随访计划已结束", code="plan_closed")
    if schedule is not None:
        if not schedule:
            raise Unprocessable("随访计划至少需要一个随访节点", code="empty_schedule")
        plan.schedule_json = schedule
    start = start or utcnow()
    for i, node in enumerate(plan.schedule_json, start=1):
        day = int(node.get("day", 0))
        if not (0 <= day <= 365):
            raise Unprocessable(f"第 {i} 个随访节点的天数不合理", code="bad_schedule")
        db.add(FollowupTask(plan_id=plan.id, encounter_id=plan.encounter_id, patient_id=plan.patient_id, doctor_id=plan.doctor_id, seq=i,
                            kind=node.get("kind", "symptom_check"), due_at=start + timedelta(days=day),
                            questions_json=node.get("questions") or DEFAULT_QUESTIONS))
    plan.status, plan.activated_at = FuPlanStatus.ACTIVE.value, utcnow()
    audit.record(db, actor, "followup.activate", entity_type="followup_plan", entity_id=plan.id, encounter_id=plan.encounter_id,
                 patient_id=plan.patient_id, summary=f"启用随访计划（{len(plan.schedule_json)} 个节点）")
    return plan


def due_state(t: FollowupTask) -> str:
    now = utcnow()
    if t.status != FuTaskStatus.SCHEDULED.value:
        return t.status
    if t.due_at <= now:
        return "overdue" if (now - t.due_at).days >= 1 else "due"
    return "upcoming"


# --------------------------------------------------------------------------- patient response
def submit_response(db: Session, task: FollowupTask, *, answers: dict, free_text: str, submit_key: str) -> tuple[FollowupResponse, bool, FollowupAlert | None]:
    """Idempotent per (task, submit_key). Returns (response, created, alert)."""
    if task.status in (FuTaskStatus.CANCELLED.value,):
        raise Conflict("该随访已取消", code="task_cancelled")
    if submit_key:
        ex = db.scalar(select(FollowupResponse).where(FollowupResponse.task_id == task.id, FollowupResponse.submit_key == submit_key))
        if ex:
            return ex, False, None
    free_text = (free_text or "").strip()
    if not free_text and not answers:
        raise Unprocessable("请至少填写一项回答", code="empty_response")
    if len(free_text) > 3000:
        raise Unprocessable("内容过长", code="too_long")
    risk = assess(free_text, answers)
    resp = FollowupResponse(task_id=task.id, patient_id=task.patient_id, encounter_id=task.encounter_id, answers_json=answers, free_text=free_text,
                            risk_level=risk["level"], rule_hits_json=risk["hits"], submit_key=submit_key or "",
                            ai_summary_json={"reason": risk["reason"], "recommended_action": risk["recommended_action"], "rules_version": "xy-fu-1"})
    db.add(resp)
    task.status, task.answered_at = FuTaskStatus.ANSWERED.value, utcnow()
    db.flush()
    alert = None
    if risk["level"] in (RiskLevel.HIGH.value, RiskLevel.ATTENTION.value):
        alert = FollowupAlert(response_id=resp.id, patient_id=task.patient_id, encounter_id=task.encounter_id, doctor_id=task.doctor_id,
                              level=risk["level"], rules_json=risk["hits"])
        db.add(alert)
        db.flush()
    audit.record(db, Actor.patient(task.patient_id), "followup.respond", entity_type="followup_response", entity_id=resp.id,
                 encounter_id=task.encounter_id, patient_id=task.patient_id, summary=f"患者提交随访回答（{risk['level']}）",
                 detail={"hits": [h["rule"] for h in risk["hits"]]})
    return resp, True, alert


def close_task(db: Session, actor: Actor, task: FollowupTask) -> FollowupTask:
    if task.status == FuTaskStatus.CLOSED.value:
        return task
    open_alerts = db.scalar(select(func.count(FollowupAlert.id)).join(FollowupResponse, FollowupResponse.id == FollowupAlert.response_id)
                            .where(FollowupResponse.task_id == task.id, FollowupAlert.status == "open", FollowupAlert.level == "high_risk")) or 0
    if open_alerts:
        raise Conflict("该随访存在未处理的高危预警，请先处理预警", code="alert_open")
    task.status, task.closed_by, task.closed_at = FuTaskStatus.CLOSED.value, actor.id, utcnow()
    audit.record(db, actor, "followup.close", entity_type="followup_task", entity_id=task.id, encounter_id=task.encounter_id,
                 patient_id=task.patient_id, summary="关闭随访任务")
    return task


def handle_alert(db: Session, actor: Actor, alert: FollowupAlert, *, status: str, note: str) -> FollowupAlert:
    if status not in ("acknowledged", "resolved"):
        raise Unprocessable("status 必须是 acknowledged 或 resolved", code="bad_status")
    if alert.level == "high_risk" and status == "resolved" and len(note.strip()) < 4:
        raise Unprocessable("处理高危预警必须记录处置说明", code="note_required")
    alert.status, alert.handled_by, alert.handled_at, alert.handle_note = status, actor.id, utcnow(), note.strip()
    audit.record(db, actor, f"followup.alert.{status}", entity_type="followup_alert", entity_id=alert.id, encounter_id=alert.encounter_id,
                 patient_id=alert.patient_id, summary=f"随访预警{('已确认' if status == 'acknowledged' else '已处理')}（{alert.level}）")
    return alert


# --------------------------------------------------------------------------- messaging
def send_message(db: Session, *, patient_id: str, sender_kind: str, sender_id: str, sender_name: str, body: str, encounter_id: str | None = None,
                 task_id: str | None = None, client_key: str = "") -> tuple[FollowupMessage, bool]:
    body = (body or "").strip()
    if not body:
        raise Unprocessable("消息不能为空", code="empty_message")
    if len(body) > 2000:
        raise Unprocessable("消息过长（上限 2000 字）", code="too_long")
    if client_key:
        ex = db.scalar(select(FollowupMessage).where(FollowupMessage.patient_id == patient_id, FollowupMessage.client_key == client_key))
        if ex:
            return ex, False
    m = FollowupMessage(patient_id=patient_id, sender_kind=sender_kind, sender_id=sender_id, sender_name=sender_name, body=body,
                        encounter_id=encounter_id, task_id=task_id, client_key=client_key)
    db.add(m)
    db.flush()
    return m, True
