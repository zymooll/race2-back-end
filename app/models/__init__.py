"""数据模型注册入口：导入所有模型，确保 Base.metadata 完整。"""

from app.models.ai_review import AIReviewTask
from app.models.api_log import ApiLog
from app.models.assessment import AssessmentRecord
from app.models.indicator import (
    BusinessTypeConfig,
    DataSourceMapping,
    IndicatorCategory,
    IndicatorConfig,
    IndicatorValue,
)
from app.models.model_version import ModelVersion
from app.models.sys_config import SystemConfig
from app.models.user import User

__all__ = [
    "AIReviewTask",
    "User",
    "AssessmentRecord",
    "ModelVersion",
    "SystemConfig",
    "ApiLog",
    "IndicatorCategory",
    "IndicatorConfig",
    "IndicatorValue",
    "DataSourceMapping",
    "BusinessTypeConfig",
]
