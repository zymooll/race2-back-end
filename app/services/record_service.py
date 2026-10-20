"""评估记录管理服务"""

from sqlalchemy.orm import Session

from app.core.exceptions import BizException
from app.models.ai_review import AIReviewTask
from app.models.assessment import AssessmentRecord
from app.models.indicator import IndicatorConfig, IndicatorValue
from app.schemas.common import PageData
from app.schemas.record import AssessmentRecordOut, IndicatorValueOut
from app.services import ai_review_service


def _indicator_values(db: Session, record_id: int) -> list[IndicatorValueOut]:
    """加载一次评估的动态指标明细（code→名称/层级/单位）。"""
    rows = (
        db.query(IndicatorValue, IndicatorConfig)
        .outerjoin(IndicatorConfig, IndicatorConfig.code == IndicatorValue.indicator_code)
        .filter(IndicatorValue.assessment_id == record_id)
        .order_by(IndicatorValue.id)
        .all()
    )
    return [
        IndicatorValueOut(
            code=v.indicator_code,
            name=c.name if c else v.indicator_code,
            level=c.level if c else "",
            unit=c.unit if c else "",
            value=v.value,
            quality=v.quality,
        )
        for v, c in rows
    ]


def list_records(
    db: Session,
    page: int = 1,
    size: int = 20,
    keyword: str | None = None,
    level: str | None = None,
    business_type: str | None = None,
    user_id: int | None = None,
) -> PageData[AssessmentRecordOut]:
    """评估记录分页；user_id 非 None 时仅返回该用户自己的记录。"""
    query = db.query(AssessmentRecord)
    if user_id is not None:
        query = query.filter(AssessmentRecord.user_id == user_id)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(AssessmentRecord.enterprise_name.like(like))
    if level:
        query = query.filter(AssessmentRecord.level == level)
    if business_type:
        query = query.filter(AssessmentRecord.business_type == business_type)

    total = query.count()
    items = query.order_by(AssessmentRecord.id.desc()).offset((page - 1) * size).limit(size).all()
    return PageData(
        total=total,
        page=page,
        size=size,
        items=[AssessmentRecordOut.from_model(r) for r in items],
    )


def _check_owner(r: AssessmentRecord, user_id: int | None) -> None:
    """归属校验：非空 user_id 时仅允许操作本人记录。"""
    if user_id is not None and r.user_id != user_id:
        raise BizException("无权访问该记录", 403)


def get_record(db: Session, record_id: int, user_id: int | None = None) -> AssessmentRecordOut:
    r = db.query(AssessmentRecord).filter(AssessmentRecord.id == record_id).first()
    if not r:
        raise BizException("记录不存在", 404)
    _check_owner(r, user_id)
    values = _indicator_values(db, record_id)
    detail = AssessmentRecordOut.from_model(r, indicator_values=values or None)
    if r.result_json:
        detail.result = {**detail.result, "recordId": r.id, "aiReview": ai_review_service.snapshot(db, r)}
    else:
        detail.result = None
    return detail


def delete_record(db: Session, record_id: int, user_id: int | None = None) -> None:
    r = db.query(AssessmentRecord).filter(AssessmentRecord.id == record_id).with_for_update().first()
    if not r:
        raise BizException("记录不存在", 404)
    _check_owner(r, user_id)
    db.query(AIReviewTask).filter(AIReviewTask.record_id == record_id).delete(synchronize_session=False)
    db.delete(r)
    db.commit()
