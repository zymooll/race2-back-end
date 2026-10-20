"""风险评估请求/响应 —— 严格对齐前端 fore-end/src/api/types.ts 契约"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.ai_review import AIReview

# 经营类型大类编码：01~10 或 MIXED（混合经营）
_BUSINESS_TYPE_RE = re.compile(r"^(0[1-9]|10|MIXED)$")
# 指标/类型编码：字母数字下划线，最长 32
_CODE_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")


class DynamicRiskInput(BaseModel):
    """动态指标体系评估请求（Phase 1：专家引擎，指标按编码动态传入）。"""

    enterpriseName: str = ""
    businessType: str = ""  # 经营类型大类编码 01~10；混合经营用 MIXED
    middleType: str = ""  # 中类编码（可选，兼容旧渐进式）
    smallType: str = ""  # 小类编码（可选，兼容旧渐进式）
    specificType: str = ""  # 具体营业类型编码（可选，兼容旧渐进式）
    selectedCategories: list[str] = []  # el-tree 勾选的具体营业类型叶子编码列表
    mixedBusiness: dict[str, float] = {}  # 混合经营比例 {具体营业类型(8位)或大类编码: 0~1}
    indicators: dict[str, str] = {}  # 动态指标值 {指标编码: 值}

    # ---------- 服务端安全校验（防止超长输入 / 非法编码 / 非法比例）----------
    @field_validator("enterpriseName")
    @classmethod
    def _check_enterprise_name(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) > 100:
            raise ValueError("企业名称不能超过 100 个字符")
        return v

    @field_validator("businessType")
    @classmethod
    def _check_business_type(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not _BUSINESS_TYPE_RE.match(v):
            raise ValueError("经营类型编码不合法（应为 01~10 或 MIXED）")
        return v

    @field_validator("selectedCategories")
    @classmethod
    def _check_categories(cls, v: list[str]) -> list[str]:
        if len(v) > 50:
            raise ValueError("勾选的经营类型不能超过 50 项")
        for c in v:
            if not _CODE_RE.match(c or ""):
                raise ValueError(f"经营类型编码不合法：{c}")
        return v

    @field_validator("mixedBusiness")
    @classmethod
    def _check_mixed(cls, v: dict[str, float]) -> dict[str, float]:
        if len(v) > 50:
            raise ValueError("混合经营组成不能超过 50 类")
        for code, ratio in v.items():
            # key 支持具体营业类型（8 位叶子）或大类编码
            if not _CODE_RE.match(code):
                raise ValueError(f"混合经营类型编码不合法：{code}")
            if ratio < 0 or ratio > 1:
                raise ValueError(f"混合经营比例 {code} 需在 0~1 之间")
        return v

    @field_validator("indicators")
    @classmethod
    def _check_indicators(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 200:
            raise ValueError("指标数量不能超过 200 项")
        out: dict[str, str] = {}
        for code, val in v.items():
            if not _CODE_RE.match(code):
                raise ValueError(f"指标编码不合法：{code}")
            val = (val or "").strip()
            if len(val) > 50:
                raise ValueError(f"指标 {code} 的值过长（最多 50 字符）")
            out[code] = val
        return out


class FactorContribution(BaseModel):
    """各指标贡献"""

    factor: str
    category: str
    weight: float
    score: float  # 单项得分 0-100
    code: str = ""  # 稳定指标编码，供详情定位


class Deduction(BaseModel):
    """扣分原因（前三项负面指标）"""

    factor: str
    score: float
    reason: str
    code: str = ""


class RiskAlert(BaseModel):
    code: str = ""  # 空编码表示整体评估风险
    factor: str
    severity: Literal["veto", "high"]
    reason: str
    score: float | None = None


class IndicatorExplanation(BaseModel):
    code: str
    name: str
    level: str
    unit: str
    value: str | None = None
    dataSource: str
    riskMeaning: str
    scoringRule: str
    score: float | None = None
    weight: float | None = None
    status: Literal["provided", "missing", "invalid", "unscored"]


class DataTransparency(BaseModel):
    expectedCount: int
    providedCount: int
    scoredCount: int
    missingCodes: list[str]
    confidence: float  # 可评估指标有效覆盖率；不是预测准确率
    confidenceLabel: str
    confidenceExplanation: str
    indicators: list[IndicatorExplanation]


class EngineUsage(BaseModel):
    expertCalls: int
    dataLayerApplications: int  # 已成功参与结果融合的次数


class RiskResult(BaseModel):
    """风险评估结果（评分卡 0-1000 分）"""

    score: int  # 综合信用评分 (0-1000)
    probability: float  # 违约概率 (0-1)
    level: str  # 低风险 / 中等风险 / 高风险
    suggestedAmount: float  # 建议授信额度（万元）
    suggestedRate: float  # 建议利率（%）
    contributions: list[FactorContribution]
    deductions: list[Deduction]
    advice: str
    overrides: list[str] = []  # 触发的兜底规则（极端场景人工复核提示）
    veto: str | None = None  # 一票否决命中指标名（专家引擎）
    completeness: float = 0.0  # 数据完整度 0~1（相对期望指标集）
    riskAlerts: list[RiskAlert] = Field(default_factory=list)
    transparency: DataTransparency | None = None  # 老记录可能没有解释快照
    engineUsage: EngineUsage | None = None  # 老记录不推断执行次数
    recordId: int | None = None
    aiReview: AIReview | None = None
