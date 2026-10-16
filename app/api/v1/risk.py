"""风险评估接口"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_user_optional, require_admin
from app.core.exceptions import BizException
from app.core.response import ApiResponse, ok
from app.db.session import get_db
from app.models.assessment import AssessmentRecord
from app.models.user import User
from app.schemas.common import PageData
from app.schemas.record import AssessmentRecordOut
from app.schemas.risk import DynamicRiskInput, RiskResult
from app.services import record_service, risk_service

router = APIRouter(prefix="/risk", tags=["风险评估"])

# 真实回测结果枚举：pending 待回填 / normal 正常还款 / overdue 逾期 / rejected 未放款
OUTCOME_VALUES = ("pending", "normal", "overdue", "rejected")


class OutcomeUpdate(BaseModel):
    outcome: Literal["pending", "normal", "overdue", "rejected"]
    note: str | None = None


@router.post("/assess-dynamic", response_model=ApiResponse[RiskResult], summary="动态指标体系评估（专家引擎）")
def assess_dynamic(
    req: DynamicRiskInput,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    payload = req.model_dump()
    result = risk_service.assess_dynamic_and_store(
        db,
        payload,
        assessor_name=user.username if user else None,
        user_id=user.id if user else None,
    )
    return ok(result)


@router.get("/records", response_model=ApiResponse[PageData[AssessmentRecordOut]], summary="评估记录分页")
def records(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    keyword: str | None = None,
    level: str | None = None,
    businessType: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = record_service.list_records(db, page, size, keyword, level, businessType, user_id=user.id)
    return ok(data)


@router.get(
    "/records/backtest/stats", response_model=ApiResponse, summary="真实回测统计（放款结果→现实版召回率/精确率）"
)
def backtest_stats(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """基于人工回填的真实放款结果，统计现实版指标。

    - 现实版精确率 = 判高风险客群中实际逾期比例
    - 现实版召回率 = 实际逾期客群中被判高风险比例
    - 仅统计已回填（outcome != pending）的记录
    """
    rows = db.query(AssessmentRecord).all()
    filled = [r for r in rows if (r.outcome or "pending") != "pending"]

    by_outcome = {v: 0 for v in OUTCOME_VALUES}
    by_level: dict[str, int] = {}
    cross: dict[str, dict[str, int]] = {}
    for r in filled:
        oc = r.outcome or "pending"
        by_outcome[oc] = by_outcome.get(oc, 0) + 1
        by_level[r.level] = by_level.get(r.level, 0) + 1
        cell = cross.setdefault(r.level, {})
        cell[oc] = cell.get(oc, 0) + 1

    high = by_level.get("高风险", 0)
    overdue = by_outcome.get("overdue", 0)
    high_overdue = cross.get("高风险", {}).get("overdue", 0)

    return ok(
        {
            "total": len(rows),
            "filled": len(filled),
            "byOutcome": by_outcome,
            "byLevel": by_level,
            "cross": cross,
            # 现实版指标（回填样本）
            "precisionHighRisk": round(high_overdue / high, 4) if high else None,
            "recallOverdue": round(high_overdue / overdue, 4) if overdue else None,
            "highRiskTotal": high,
            "overdueTotal": overdue,
        }
    )


@router.get("/records/data-layer-stats", response_model=ApiResponse, summary="数据层混合触发监控")
def data_layer_stats(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """统计线上评估中数据层混合的触发情况（基于 result_json.overrides 中的 data_layer 标记）。"""
    rows = db.query(AssessmentRecord).all()
    triggered = 0
    scores: list[float] = []
    for r in rows:
        rj = r.result_json or {}
        ov = rj.get("overrides") or []
        hit = None
        for o in ov:
            if isinstance(o, str) and "data_layer:" in o:
                hit = o
                break
        if hit:
            triggered += 1
            import re

            m = re.search(r"data_layer:score=([\d.]+)", hit)
            if m:
                scores.append(float(m.group(1)))
    return ok(
        {
            "total": len(rows),
            "triggered": triggered,
            "triggerRate": round(triggered / len(rows), 4) if rows else None,
            "dataScoreAvg": round(sum(scores) / len(scores), 1) if scores else None,
            "dataScoreMin": min(scores) if scores else None,
            "dataScoreMax": max(scores) if scores else None,
        }
    )


@router.get("/records/{record_id}", response_model=ApiResponse[AssessmentRecordOut], summary="记录详情")
def record_detail(
    record_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ok(record_service.get_record(db, record_id, user_id=user.id))


@router.delete("/records/{record_id}", response_model=ApiResponse, summary="删除记录")
def delete_record(
    record_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    record_service.delete_record(db, record_id, user_id=user.id)
    return ok(message="删除成功")


@router.put(
    "/records/{record_id}/outcome",
    response_model=ApiResponse[AssessmentRecordOut],
    summary="回填评估真实结果（放款/逾期，用于回测）",
)
def update_outcome(
    record_id: int,
    body: OutcomeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rec = db.get(AssessmentRecord, record_id)
    if not rec:
        raise BizException("评估记录不存在", 404)
    if user.role != "admin" and rec.user_id != user.id:
        raise BizException("无权回填该记录", 403)
    rec.outcome = body.outcome
    rec.outcome_note = (body.note or "").strip()[:255] or None
    rec.outcome_at = datetime.now()
    db.commit()
    db.refresh(rec)
    return ok(AssessmentRecordOut.from_model(rec))
