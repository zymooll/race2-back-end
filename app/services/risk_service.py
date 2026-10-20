"""风险评估服务：加载模型与阈值 → 评估 → 落库"""

from sqlalchemy.orm import Session

from app.ml.expert_engine import expert_assess
from app.models.assessment import AssessmentRecord
from app.models.indicator import IndicatorValue
from app.services import ai_review_service
from app.services.transparency_service import enrich_result


def _combine_mixed(sub_results: dict[str, tuple[float, dict]], db: Session | None = None) -> dict:
    """混合经营：按子类型比例加权合并专家评估结果 + 叠加协同因子。

    协同因子（business_type_config.MIXED.synergy_factors，如 "01+02": {factor:1.06}）：
    当所选业务组合命中已知协同组合时，评分加成 factor（种养结合/产销一体等）。
    """
    total_ratio = sum(r for r, _ in sub_results.values()) or 1.0
    # 否决红线不能被其他经营类型的高分、额度或协同加分稀释。
    veto_result = next((res for _, res in sub_results.values() if res.get("veto")), None)
    if veto_result is not None:
        return dict(veto_result)
    score = round(sum(ratio * res["score"] for ratio, res in sub_results.values()) / total_ratio)
    probability = sum(ratio * res["probability"] for ratio, res in sub_results.values()) / total_ratio

    # ---- 协同因子叠加 ----
    overrides: list[str] = list(dict.fromkeys(o for _, res in sub_results.values() for o in res.get("overrides", [])))
    if db is not None:
        from app.models.indicator import BusinessTypeConfig

        cfg = (
            db.query(BusinessTypeConfig)
            .filter(BusinessTypeConfig.business_type_code == "MIXED", BusinessTypeConfig.active.is_(True))
            .first()
        )
        factors = (cfg.synergy_factors or {}) if cfg else {}
        # 协同因子按「大类组合」匹配（sub_results 的 key 为具体营业类型 8 位叶子，取前 2 位大类）
        big_sets: set[str] = set()
        big_codes = sorted({c[:2] for c in sub_results.keys()})
        for i in range(len(big_codes)):
            for j in range(i + 1, len(big_codes)):
                big_sets.add(f"{big_codes[i]}+{big_codes[j]}")
        synergy_hits = []
        for key in sorted(big_sets):
            if key in factors:
                synergy_hits.append((key, float(factors[key]["factor"]), factors[key].get("name", key)))
        if synergy_hits:
            # 取最大协同加成（v1：不叠加多个因子，避免过度乐观）
            key, factor, name = max(synergy_hits, key=lambda x: x[1])
            score = max(0, min(1000, round(score * factor)))
            overrides.append(f"synergy:{key}:{factor:.2f}({name})")

    level = "低风险" if score >= 700 else ("中等风险" if score >= 500 else "高风险")

    contributions = []
    for ratio, res in sub_results.values():
        for c in res["contributions"]:
            c2 = dict(c)
            c2["weight"] = round(c2["weight"] * ratio / total_ratio, 4)
            contributions.append(c2)
    deductions = sorted(
        [d for _, res in sub_results.values() for d in res["deductions"]],
        key=lambda x: x["score"],
    )[:3]

    # 额度/利率按加权分
    amount = round(sum(ratio * res["suggestedAmount"] for ratio, res in sub_results.values()) / total_ratio, 2)
    rate = round(sum(ratio * res["suggestedRate"] for ratio, res in sub_results.values()) / total_ratio, 2)
    advice = _build_mixed_advice(level, amount, rate)

    return {
        "score": score,
        "probability": round(probability, 4),
        "level": level,
        "suggestedAmount": amount,
        "suggestedRate": rate,
        "contributions": contributions,
        "deductions": deductions,
        "advice": advice,
        "overrides": overrides,
        "veto": None,
        "completeness": next(iter(sub_results.values()))[1]["completeness"],
    }


def _build_mixed_advice(level: str, amount: float, rate: float) -> str:
    if level == "低风险":
        return f"【混合经营】综合评估良好，建议授信约 {amount} 万元，执行优惠利率 {rate}%。"
    if level == "中等风险":
        return f"【混合经营】建议审慎授信，额度约 {amount} 万元，利率 {rate}%，可补充担保。"
    return f"【混合经营】建议暂缓放贷或要求足额抵押物，参考额度 {amount} 万元，利率 {rate}%。"


def assess_dynamic_and_store(
    db: Session,
    payload: dict,
    assessor_name: str | None = None,
    user_id: int | None = None,
) -> dict:
    """动态指标体系评估（专家引擎）：支持单经营类型与混合经营加权。"""
    indicators = payload.get("indicators", {}) or {}
    business_type = payload.get("businessType", "")
    mixed = payload.get("mixedBusiness", {}) or {}
    selected_categories = payload.get("selectedCategories", []) or []
    expert_calls = 0
    data_layer_applications = 0
    sub_results: dict[str, tuple[float, dict]] = {}

    if business_type == "MIXED" and mixed:
        for code, ratio in mixed.items():
            if ratio > 0:
                expert_calls += 1
                sub_results[code] = (
                    float(ratio),
                    expert_assess(db, code, indicators, selected_categories=selected_categories),
                )
                data_layer_applications += sum(
                    o.startswith("data_layer:") for o in sub_results[code][1].get("overrides", [])
                )
        if sub_results:
            result = _combine_mixed(sub_results, db)
        else:
            expert_calls = 1
            result = expert_assess(db, "", indicators, selected_categories=selected_categories)
    else:
        expert_calls = 1
        result = expert_assess(db, business_type, indicators, selected_categories=selected_categories)

    if not sub_results:
        data_layer_applications = sum(o.startswith("data_layer:") for o in result.get("overrides", []))
    result["engineUsage"] = {"expertCalls": expert_calls, "dataLayerApplications": data_layer_applications}
    enrich_result(db, payload, result)

    # 落库：动态输入/结果快照 + 指标明细
    rec = AssessmentRecord(
        enterprise_name=payload.get("enterpriseName", ""),
        business_type=business_type,
        user_id=user_id,
        score=result["score"],
        probability=result["probability"],
        level=result["level"],
        suggested_amount=result["suggestedAmount"],
        suggested_rate=result["suggestedRate"],
        input_json=payload,
        result_json=result,
        assessor_name=assessor_name,
    )
    db.add(rec)
    db.flush()  # 拿到 rec.id

    for code, value in indicators.items():
        db.add(IndicatorValue(assessment_id=rec.id, indicator_code=code, value=str(value)))

    ai_review_service.enqueue(db, rec)
    db.commit()
    return {**result, "recordId": rec.id, "aiReview": ai_review_service.snapshot(db, rec)}
