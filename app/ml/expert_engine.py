"""专家规则评分引擎：基于指标体系『评分规则 + 建议权重』的动态评分。

权重模型（与用户确认）：
- 层级基础权重：基本项 > 大类 > 中类 > 小类（business_type_config 可配，默认 0.35/0.28/0.22/0.15）
- 层内分配：同层兄弟按星级归一化 w_i = star_i / Σstar_已填
- 调整因子：特色指标加成、区域匹配加成（可打破层级单调 → “小类>大类”为合法例外）
- 缺失处理：未填指标剔除 + 权重再分配（w'_i = star_i / Σstar_已填）

打分：
- 文本：不参与评分
- 一票否决：命中即拒贷（不进入计分）
- 数值：score = clamp(value / ref_max, 0, 1) × 100（ref_max 取 scoring_config 或默认）
- 枚举：按选项语义（关键词启发）映射得分，scoring_config 可覆盖
- 布尔：默认 是=100 / 否=40，scoring_config 可覆盖

输出：0-1000 评分 + 等级 + 各指标贡献 + 扣分原因 + 建议文本。
"""

from __future__ import annotations

import math
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.indicator import BusinessTypeConfig, IndicatorConfig

# ---------- 默认层级基础权重（基本项 > 大类 > 中类 > 小类）----------
DEFAULT_LEVEL_WEIGHTS = {
    "基本项": 0.35,
    "大类": 0.28,
    "中类": 0.22,
    "小类": 0.15,
    "具体营业类型": 0.10,
}

# ---------- 默认布尔/枚举启发 ----------
NEGATIVE_WORDS = ["无", "否", "未", "低", "差", "风险", "违法", "失信", "缺失", "频繁", "零散", "逾期", "拒绝", "不"]
POSITIVE_WORDS = ["稳定", "完整", "是", "高", "优", "年度", "订单", "无逾期", "良好", "省级", "国家级", "有"]


def _parse_num(value: Any) -> float | None:
    """尝试把指标值转数值。"""
    if value is None or value == "":
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _parse_options(value_range: str) -> list[str]:
    v = (value_range or "").strip()
    if not v:
        return []
    for sep in ("/", "／", "、", "，"):
        if sep in v:
            parts = [p.strip() for p in v.split(sep) if p.strip()]
            return parts
    return [v]


def _ref_max(indicator: IndicatorConfig) -> float:
    """数值指标的参考上限：scoring_config.max > 取值说明中解析 > 默认 100。"""
    cfg = indicator.scoring_config or {}
    if cfg.get("max"):
        return float(cfg["max"])
    m = re.search(r"(\d+)\s*[-~至]\s*(\d+)", indicator.value_range or "")
    if m:
        return float(m.group(2))
    return 100.0


def _score_enum(value: str, indicator: IndicatorConfig) -> float:
    """枚举档位 → 0-100。优先 scoring_config.map，否则关键词启发。"""
    cfg = indicator.scoring_config or {}
    mapping = cfg.get("map") or {}
    if value in mapping:
        return float(mapping[value])
    options = _parse_options(indicator.value_range)
    if not options:
        return 60.0
    # 关键词启发：按选项文本判定好坏
    idx = {o: i for i, o in enumerate(options)}
    if value not in idx:
        return 60.0
    pos = idx[value] / max(len(options) - 1, 1)
    # 依据选项语义方向（含正面词越多分越高，负面词越多越低）
    txt = value
    if any(w in txt for w in NEGATIVE_WORDS) and not any(w in txt for w in POSITIVE_WORDS):
        return round(60 - 30 * pos, 1)
    if any(w in txt for w in POSITIVE_WORDS):
        return round(100 - 20 * pos, 1)
    return round(60 + 40 * pos, 1)


def _score_bool(value: Any, indicator: IndicatorConfig) -> float:
    cfg = indicator.scoring_config or {}
    mapping = cfg.get("map") or {}
    v = str(value).strip()
    if v in mapping:
        return float(mapping[v])
    # 默认：是=100，否=40（若规则或名称暗示“命中即风险”，可在 scoring_config 覆盖）
    if v in ("是", "true", "True", "1", 1, True):
        return 100.0
    return 40.0


def _lower_better(indicator: IndicatorConfig) -> bool:
    cfg = indicator.scoring_config or {}
    rule = (indicator.scoring_rule or "") + " " + (indicator.name or "")
    # 越低越好判定：显式配置 > 规则/名称语义（负债率/风险/负担/频次等越高越差）
    lower_better = cfg.get("lower_better", False)
    if not lower_better and "越高越好" not in rule:
        lower_better = any(kw in rule for kw in ("越低", "风险越高", "越高越差", "越高风险", "越高越不利"))
    return bool(lower_better)


def _score_value(value: float, indicator: IndicatorConfig) -> float:
    """数值指标 → 0-100。支持『越高越好』与『越低越好』。"""
    lower_better = _lower_better(indicator)
    ref = _ref_max(indicator)
    if ref <= 0:
        ref = 100.0
    ratio = min(max(value / ref, 0.0), 1.0)
    return round((1 - ratio) * 100 if lower_better else ratio * 100, 1)


def describe_scoring(indicator: IndicatorConfig) -> str:
    """返回当前计分规则，避免把配置文档中的规划描述当成已执行算法。"""
    if indicator.is_veto:
        return "命中否决项即不予授信；否决核验不参与加权计分。"
    if indicator.indicator_type == "文本":
        return "备注与身份核对信息，不参与加权计分。"
    mapping = (indicator.scoring_config or {}).get("map") or {}
    if mapping:
        return "当前选项计分：" + "；".join(f"{option} = {score:g} 分" for option, score in mapping.items())
    if indicator.indicator_type == "数值":
        ref = _ref_max(indicator)
        ref = ref if ref > 0 else 100.0
        direction = (
            "越低越好，按 (1 - 取值 / 参考上限) × 100"
            if _lower_better(indicator)
            else "越高越好，按取值 / 参考上限 × 100"
        )
        return f"当前参考上限 {ref:g} {indicator.unit or ''}；{direction} 计分，得分限制在 0 到 100。"
    if indicator.indicator_type == "布尔":
        return "当前默认计分：是 = 100 分，否 = 40 分。"
    return "当前按选项顺序与正负面关键词映射为 0 到 100 分。"


def score_indicator(value: Any, indicator: IndicatorConfig) -> float | None:
    """单指标打分；文本类或无法打分返回 None。"""
    t = indicator.indicator_type
    if t == "文本":
        return None
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    if t == "数值":
        num = _parse_num(value)
        if num is None:
            return None
        # 数据正确性：数值指标（面积/金额/年限/次数等）不允许负数，负数视为无效数据
        if num < 0:
            return None
        return _score_value(num, indicator)
    if t == "枚举":
        options = _parse_options(indicator.value_range)
        mapping = (indicator.scoring_config or {}).get("map") or {}
        if options and str(value).strip() not in options and str(value).strip() not in mapping:
            return None
        return _score_enum(str(value).strip(), indicator)
    if t == "布尔":
        v = str(value).strip()
        mapping = (indicator.scoring_config or {}).get("map") or {}
        if (
            v not in ("是", "否", "true", "false", "True", "False", "1", "0", "有", "无", "命中", "存在")
            and v not in mapping
        ):
            return None
        return _score_bool(value, indicator)
    return None


def _check_veto(indicators: dict[str, Any], configs: dict[str, IndicatorConfig]) -> str | None:
    """一票否决：命中即拒贷，返回触发指标名。"""
    for code, cfg in configs.items():
        if not cfg.is_veto:
            continue
        value = indicators.get(code)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        v = str(value).strip()
        if v in ("是", "true", "True", "1", "有", "命中", "存在"):
            return cfg.name
    return None


def _level_weights(db: Session, business_type: str) -> dict[str, float]:
    """层级基础权重：优先按具体营业类型（国标 4+4 位叶子）配置，其次回退其所属大类配置，最后默认。

    支持 business_type_config 中按具体营业类型（8 位 code）独立配置权重；
    未配置叶子的场景回退大类配置，保证向后兼容。
    """
    base = dict(DEFAULT_LEVEL_WEIGHTS)
    codes: list[str] = []
    if business_type:
        codes.append(business_type)
        if "_" in business_type:
            codes.append(business_type.split("_")[0][:2])  # 叶子 → 所属大类
    for code in codes:
        cfg = (
            db.query(BusinessTypeConfig)
            .filter(BusinessTypeConfig.business_type_code == code, BusinessTypeConfig.active.is_(True))
            .first()
        )
        if cfg and cfg.level_weights:
            return {**base, **cfg.level_weights}
    return base


def _adjustment(indicator: IndicatorConfig, business_type: str, level_weights: dict) -> float:
    """调整因子：特色指标加成 + 区域匹配。可打破层级单调。"""
    adj = 1.0
    if indicator.is_feature:
        # 特色指标加成（暂固定 1.1，后续可按经营类型配置）
        adj *= 1.1
    # 区域标签只说明适用范围，不在缺少经营地区输入时推断区域加分。
    return adj


def expert_assess(
    db: Session,
    business_type: str,
    indicators: dict[str, Any],
    mixed_business: dict | None = None,
    selected_categories: list[str] | None = None,
) -> dict[str, Any]:
    """专家引擎评估。返回与现有 RiskResult 兼容的结构。"""
    codes = list(indicators.keys())
    if not codes:
        return {
            "score": 0,
            "probability": 1.0,
            "level": "高风险",
            "suggestedAmount": 0.0,
            "suggestedRate": 0.0,
            "contributions": [],
            "deductions": [],
            "advice": "未填写任何指标，无法评估",
            "overrides": [],
            "veto": None,
            "completeness": 0.0,
        }
    configs = {c.code: c for c in db.query(IndicatorConfig).filter(IndicatorConfig.code.in_(codes)).all()}

    # 一票否决
    veto_hit = _check_veto(indicators, configs)
    if veto_hit:
        return {
            "score": 200,
            "probability": 0.9,
            "level": "高风险",
            "suggestedAmount": 0.0,
            "suggestedRate": 0.0,
            "contributions": [],
            "deductions": [],
            "advice": f"【一票否决】命中『{veto_hit}』，不予授信。",
            "overrides": [f"veto:{veto_hit}"],
            "veto": veto_hit,
            "completeness": _completeness(db, business_type, indicators, configs, selected_categories),
        }

    # 逐指标打分（跳过文本/缺失）
    scored: list[tuple[IndicatorConfig, float]] = []
    for code in codes:
        cfg = configs.get(code)
        if not cfg or cfg.is_veto:
            continue
        s = score_indicator(indicators[code], cfg)
        if s is not None:
            scored.append((cfg, s))

    if not scored:
        return {
            "score": 300,
            "probability": 0.8,
            "level": "高风险",
            "suggestedAmount": 0.0,
            "suggestedRate": 0.0,
            "contributions": [],
            "deductions": [],
            "advice": "缺少可计分指标，暂按高风险处理",
            "overrides": [],
            "veto": None,
            "completeness": _completeness(db, business_type, indicators, configs, selected_categories),
        }

    # 权重计算
    lw = _level_weights(db, business_type)
    level_star_sum: dict[str, float] = {}
    for cfg, _s in scored:
        level_star_sum[cfg.level] = level_star_sum.get(cfg.level, 0.0) + (cfg.weight_star or 3.0)

    weighted_items: list[tuple[float, float, str, str]] = []  # (w_eff, score, code, name)
    total_w = 0.0
    total_s = 0.0
    for cfg, s in scored:
        base = lw.get(cfg.level, 0.2)
        star_norm = (cfg.weight_star or 3.0) / max(level_star_sum.get(cfg.level, 1.0), 1e-9)
        adj = _adjustment(cfg, business_type, lw)
        w = base * star_norm * adj
        weighted_items.append((w, s, cfg.code, cfg.name))
        total_w += w
        total_s += w * s

    if total_w <= 0:
        total_w = 1e-9
    total_100 = total_s / total_w  # 0-100
    score = max(0, min(1000, round(total_100 * 10)))

    # ---- 数据层混合（混合引擎：专家层 0.75 + 数据层 0.25）----
    overrides: list[str] = []
    try:
        from app.ml.data_layer import data_layer_score

        blended, dl_info = data_layer_score(db, indicators, score)
        if blended is not None:
            score = blended
            overrides.append(f"data_layer:score={dl_info['dataScore']}({dl_info['features']})")
    except Exception:  # noqa: BLE001
        # 数据层异常不阻断主流程
        pass

    # 等级
    level = "低风险" if score >= 700 else ("中等风险" if score >= 500 else "高风险")
    # 违约概率：优先用真实回填数据校准（Platt），数据不足回退默认逻辑映射
    probability = round(1 / (1 + math.exp((score - 550) / 80)), 4)  # 默认逻辑映射（回退）
    try:
        from app.ml.calibration import calibrated_probability

        _p = calibrated_probability(db, float(score))
        if _p is not None:
            probability = round(_p, 4)
    except Exception:  # noqa: BLE001
        pass

    # 贡献与扣分
    contributions = [
        {
            "code": code,
            "factor": name,
            "category": configs.get(code).level if configs.get(code) else "未知",
            "weight": round(w / total_w, 4),
            "score": round(s, 1),
        }
        for (w, s, code, name) in weighted_items
    ]
    deductions = sorted(
        [
            {"code": code, "factor": name, "score": s, "reason": _deduct_reason(s)}
            for (_w, s, code, name) in weighted_items
        ],
        key=lambda x: x["score"],
    )[:3]

    # 额度/利率（沿用现有逻辑）
    amount, rate = _amount_and_rate(score, indicators, configs)
    advice = _build_advice(level, amount, rate)

    return {
        "score": score,
        "probability": probability,
        "level": level,
        "suggestedAmount": amount,
        "suggestedRate": rate,
        "contributions": contributions,
        "deductions": deductions,
        "advice": advice,
        "overrides": overrides,
        "veto": None,
        "completeness": _completeness(db, business_type, indicators, configs, selected_categories),
    }


def _completeness(
    db: Session,
    business_type: str,
    indicators: dict,
    configs: dict,
    selected_categories: list[str] | None = None,
) -> float:
    """数据完整度：相对期望指标集（基本项 + 大类 + 勾选叶子路径上的各级指标）计算已填比例。"""
    from sqlalchemy import or_

    conds: list = [IndicatorConfig.level == "基本项"]
    if business_type:
        # 传具体营业类型（8 位叶子）时，大类期望集取其所属大类
        big = business_type.split("_")[0][:2] if "_" in business_type else business_type
        conds.append((IndicatorConfig.level == "大类") & (IndicatorConfig.category_code == big))
    # 勾选的具体营业类型叶子 → 其路径（大类/中类/小类/具体营业类型）上的指标纳入期望
    sel = selected_categories or []
    if sel:
        small_codes = [c.split("_")[0] for c in sel if "_" in c]
        mid_codes = sorted({s[:3] for s in small_codes})
        big_codes = sorted({s[:2] for s in small_codes})
        conds.append(IndicatorConfig.category_code.in_(list(sel) + small_codes + mid_codes + big_codes))
    expected = db.query(IndicatorConfig.code).filter(or_(*conds)).all()
    expected_codes = [c[0] for c in expected]
    if not expected_codes:
        return 0.0
    filled = sum(1 for code in expected_codes if code in indicators and indicators[code] not in (None, ""))
    return round(filled / len(expected_codes), 2)


def _deduct_reason(score: float) -> str:
    if score < 40:
        return "该项指标表现较差"
    if score < 60:
        return "该项指标有待改善"
    return "建议结合原始数据与采集时点复核该项评分。"


def _amount_and_rate(score: int, indicators: dict, configs: dict) -> tuple[float, float]:
    """授信额度与利率：参考年营收类指标估算基准额度，按等级折扣。"""
    base = 5.0
    revenue_kw = ["营业收入", "年营收", "营收", "销售收入", "经营收入", "年产值"]
    for code, v in indicators.items():
        cfg = configs.get(code)
        if not cfg or cfg.indicator_type != "数值":
            continue
        name = cfg.name or ""
        if any(k in name for k in revenue_kw) and "占比" not in name:
            num = _parse_num(v)
            if num:
                base = max(num * 0.8, 5.0)
                break
    if score >= 700:
        factor = 1.0
    elif score >= 500:
        factor = 0.7
    else:
        factor = 0.4
    amount = round(base * factor, 2)
    rate = round(3.5 + (1 - score / 1000) * 6.0, 2)
    return amount, rate


def _build_advice(level: str, amount: float, rate: float) -> str:
    if level == "低风险":
        return f"建议优先审批，授信约 {amount} 万元，执行优惠利率 {rate}%。"
    if level == "中等风险":
        return f"建议审慎授信，额度约 {amount} 万元，利率 {rate}%，可补充担保。"
    return f"建议暂缓放贷或要求足额抵押物，参考额度 {amount} 万元，利率 {rate}%。"
