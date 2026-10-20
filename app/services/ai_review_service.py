"""持久任务、不可过期的成功快照及 SSE 订阅。数据库事务不跨上游网络等待。"""

import asyncio
import json
import math
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import BizException
from app.db.session import SessionLocal
from app.models.ai_review import AIReviewTask
from app.models.assessment import AssessmentRecord
from app.models.user import User
from app.schemas.ai_review import AIReview

PROMPT_VERSION = "review-v1"
ERROR_MESSAGES = {
    "configuration": "AI 服务配置尚未就绪，当前显示规则说明。",
    "snapshot_missing": "本条历史记录缺少必要的解释快照，无法生成 AI 说明。",
    "no_indicators": "本次没有需要生成说明的重点指标。",
    "timeout": "AI 生成超时，当前显示规则说明，可稍后重试。",
    "upstream_transient": "AI 服务暂时不可用，当前显示规则说明，可稍后重试。",
    "upstream_rejected": "AI 服务拒绝了请求，请检查接口与模型配置。",
    "invalid_output": "AI 输出未通过校验，当前显示规则说明。",
    "refusal": "AI 未生成可用说明，当前显示规则说明。",
    "incomplete": "AI 输出未完成，当前显示规则说明。",
    "generation_interrupted": "生成任务已中断，当前显示规则说明，可稍后重试。",
    "record_unavailable": "记录已删除或当前账号无权继续访问。",
}


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def configured(config=settings) -> bool:
    return bool(config.OPENAI_API_KEY.get_secret_value() and config.OPENAI_MODEL.strip())


def owned_record(db: Session, record_id: int, user_id: int, *, lock: bool = False) -> AssessmentRecord:
    query = select(AssessmentRecord).where(AssessmentRecord.id == record_id)
    if lock:
        query = query.with_for_update()
    record = db.scalar(query)
    if record is None:
        raise BizException("记录不存在", 404)
    if record.user_id != user_id:
        raise BizException("无权访问该记录", 403)
    return record


def build_context(record: AssessmentRecord) -> dict:
    """只使用历史计分快照；不读取当前指标配置，不发送名称/身份字段/自由备注。"""
    result = record.result_json or {}
    deductions = result.get("deductions") or []
    if not deductions:
        raise ValueError("no_indicators")
    transparency = result.get("transparency") or {}
    rows = {row["code"]: row for row in transparency.get("indicators", []) if row.get("code")}
    items = []
    for position, deduction in enumerate(deductions[:3]):
        code = deduction.get("code")
        row = rows.get(code)
        # 有计分结果的指标才能入选，文本身份信息与未验证的自由输入不会计分。
        if not row or row.get("score") is None or row.get("status") != "provided":
            raise ValueError("snapshot_missing")
        item = {
            key: row.get(key)
            for key in ("code", "name", "value", "unit", "weight", "scoringRule", "riskMeaning", "dataSource")
        }
        item.update(position=position, score=deduction["score"])
        items.append(item)
    original = record.input_json or {}
    return {
        "businessType": record.business_type,
        "selectedCategories": original.get("selectedCategories", []),
        "mixedBusiness": original.get("mixedBusiness", {}),
        "score": record.score,
        "level": record.level,
        "coverage": transparency.get("confidence"),
        "items": items,
    }


def enqueue(db: Session, record: AssessmentRecord, config=settings) -> AIReviewTask | None:
    """调用者在同一事务中持有评估记录锁；新评估则在 flush 后调用。"""
    if not config.AI_REVIEW_ENABLED or not configured(config) or record.user_id is None:
        return None
    try:
        context = build_context(record)
    except ValueError:
        return None
    context["_generationOptions"] = {
        "reasoningEffort": config.OPENAI_REASONING_EFFORT,
        "maxOutputTokens": config.AI_REVIEW_MAX_OUTPUT_TOKENS,
    }
    task = AIReviewTask(
        record_id=record.id,
        namespace=config.AI_REVIEW_NAMESPACE,
        context=context,
        model=config.OPENAI_MODEL,
        base_url=config.OPENAI_BASE_URL.rstrip("/"),
        prompt_version=PROMPT_VERSION,
    )
    db.add(task)
    db.flush()
    return task


def snapshot(db: Session, record: AssessmentRecord, config=settings) -> dict:
    task = db.scalar(
        select(AIReviewTask).where(AIReviewTask.record_id == record.id).execution_options(populate_existing=True)
    )
    if task is not None:
        status = "pending" if task.status == "running" else task.status
        # 关闭功能只停止新生成，已保存结果仍然可读。
        error = task.error_code
        if status != "ready" and (not config.AI_REVIEW_ENABLED or not configured(config)):
            status = "disabled"
            if config.AI_REVIEW_ENABLED and not configured(config):
                error = "configuration"
        retry_after = max(0, math.ceil((task.retry_at - utcnow()).total_seconds())) if task.retry_at else 0
        return AIReview(
            recordId=record.id,
            status=status,
            attemptId=task.attempt_id,
            version=task.version,
            items=task.items if status == "ready" else [],
            preview=task.preview if status == "pending" else [],
            model=task.model,
            promptVersion=task.prompt_version,
            generatedAt=task.generated_at.isoformat() + "Z" if task.generated_at else None,
            errorCode=error,
            message=ERROR_MESSAGES.get(error),
            retryAfter=retry_after,
            retryable=task.retryable and status == "failed",
        ).model_dump()
    error = None
    if not config.AI_REVIEW_ENABLED or record.user_id is None:
        status = "disabled"
    elif not configured(config):
        status, error = "disabled", "configuration"
    else:
        try:
            build_context(record)
            status = "not_requested"
        except ValueError as exc:
            status, error = "not_applicable", str(exc)
    return AIReview(recordId=record.id, status=status, errorCode=error, message=ERROR_MESSAGES.get(error)).model_dump()


def request_review(db: Session, record_id: int, user_id: int, config=settings) -> dict:
    record = owned_record(db, record_id, user_id, lock=True)
    task = db.scalar(select(AIReviewTask).where(AIReviewTask.record_id == record_id).with_for_update())
    if task is None:
        enqueue(db, record, config)
    elif task.status == "failed" and config.AI_REVIEW_ENABLED and configured(config):
        # 配置修正后也允许人工重试参数/校验失败；成功结果不接受重新生成。
        if task.retry_at and task.retry_at > utcnow():
            raise BizException("请等待重试冷却时间结束", 429)
        task.status = "pending"
        task.attempts = 0
        task.preview = []
        task.error_code = None
        task.retry_at = None
        task.retryable = False
        task.lease_token = None
        task.model = config.OPENAI_MODEL
        task.base_url = config.OPENAI_BASE_URL.rstrip("/")
        task.namespace = config.AI_REVIEW_NAMESPACE
        task.context = {
            **task.context,
            "_generationOptions": {
                "reasoningEffort": config.OPENAI_REASONING_EFFORT,
                "maxOutputTokens": config.AI_REVIEW_MAX_OUTPUT_TOKENS,
            },
        }
        task.prompt_version = PROMPT_VERSION
        task.version += 1
    db.commit()
    return snapshot(db, record, config)


def read_owned(record_id: int, user_id: int, session_factory=SessionLocal, config=settings) -> dict:
    with session_factory() as db:
        user = db.get(User, user_id)
        if user is None or user.status != 1:
            raise BizException("账号已失效", 401)
        return snapshot(db, owned_record(db, record_id, user_id), config)


def claim_next(session_factory=SessionLocal, config=settings) -> dict | None:
    if not config.AI_REVIEW_ENABLED or not configured(config):
        return None
    now = utcnow()
    with session_factory() as db, db.begin():
        task = db.scalar(
            select(AIReviewTask)
            .where(
                AIReviewTask.namespace == config.AI_REVIEW_NAMESPACE,
                or_(
                    and_(
                        AIReviewTask.status == "pending",
                        or_(AIReviewTask.retry_at.is_(None), AIReviewTask.retry_at <= now),
                    ),
                    and_(AIReviewTask.status == "running", AIReviewTask.lease_until < now),
                ),
            )
            .order_by(AIReviewTask.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if task is None:
            return None
        if task.attempts >= 2:
            task.status = "failed"
            task.error_code = "generation_interrupted"
            task.retryable = True
            task.retry_at = now + timedelta(seconds=config.AI_REVIEW_RETRY_COOLDOWN)
            task.preview = []
            task.version += 1
            return None
        task.status = "running"
        task.attempts += 1
        task.attempt_id += 1
        task.version += 1
        task.preview = []
        task.lease_token = uuid4().hex
        task.lease_until = now + timedelta(seconds=config.AI_REVIEW_LEASE_SECONDS)
        task.error_code = None
        task.updated_at = now
        return {
            "record_id": task.record_id,
            "token": task.lease_token,
            "attempt_id": task.attempt_id,
            "context": task.context,
            "model": task.model,
            "base_url": task.base_url,
            "prompt_version": task.prompt_version,
        }


def update_running(
    claim: dict,
    *,
    preview: list | None = None,
    final: list | None = None,
    usage: dict | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
    retryable: bool = False,
    session_factory=SessionLocal,
    config=settings,
) -> bool:
    now = utcnow()
    with session_factory() as db, db.begin():
        # 与人工操作采用相同锁序；外部 API 完成时重新确认评估仍存在。
        record = db.scalar(select(AssessmentRecord).where(AssessmentRecord.id == claim["record_id"]).with_for_update())
        if record is None:
            return False
        task = db.scalar(
            select(AIReviewTask)
            .where(
                AIReviewTask.record_id == record.id,
                AIReviewTask.lease_token == claim["token"],
                AIReviewTask.status == "running",
                AIReviewTask.lease_until > now,
            )
            .with_for_update()
        )
        if task is None:
            return False
        if preview is not None and preview != task.preview:
            task.preview = preview
            task.version += 1
        task.lease_until = now + timedelta(seconds=config.AI_REVIEW_LEASE_SECONDS)
        task.updated_at = now
        if final is not None:
            task.items, task.preview = final, []
            task.status = "ready"
            task.generated_at = now
            task.usage = usage
            task.duration_ms = duration_ms
            task.version += 1
            task.lease_token = None
            task.retry_at = None
        elif error is not None:
            task.status = "pending" if retryable and task.attempts < 2 else "failed"
            delay = 5 if task.status == "pending" else config.AI_REVIEW_RETRY_COOLDOWN
            task.retry_at = now + timedelta(seconds=delay)
            task.retryable = True  # 人工可在冷却后重试；仅暂时性异常自动重试。
            task.error_code = error
            task.preview = []
            task.version += 1
            task.lease_token = None
        return True


async def review_events(record_id: int, user_id: int, session_factory=SessionLocal, config=settings):
    """每次读取使用短会话，避免长连接持有数据库连接或事务。"""
    previous = None
    heartbeat = asyncio.get_running_loop().time()
    while True:
        try:
            state = await asyncio.to_thread(read_owned, record_id, user_id, session_factory, config)
        except BizException:
            state = AIReview(
                recordId=record_id,
                status="failed",
                errorCode="record_unavailable",
                message=ERROR_MESSAGES["record_unavailable"],
            ).model_dump()
        if previous is None:
            event = "snapshot"
        elif state["status"] == "ready":
            event = "completed"
        elif state["status"] == "failed":
            event = "failed"
        elif state["attemptId"] != previous["attemptId"] or (previous["preview"] and not state["preview"]):
            event = "reset"
        else:
            event = "preview"
        if previous != state:
            yield f"event: {event}\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"
        if state["status"] != "pending":
            if previous is None and state["status"] in ("ready", "failed"):
                terminal = "completed" if state["status"] == "ready" else "failed"
                yield f"event: {terminal}\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"
            return
        previous = state
        now = asyncio.get_running_loop().time()
        if now - heartbeat >= 10:
            yield ": heartbeat\n\n"
            heartbeat = now
        await asyncio.sleep(0.25)
