"""训练编排：生成/加载样本 → 训练评分卡 → 评估 → 保存产物 → 注册模型版本。

供启动自动训练、管理平台“模型训练”接口、命令行脚本共用。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from app.core.config import settings
from app.ml import model_artifact
from app.ml.scorecard import Scorecard
from app.ml.seed import generate_samples


def _sample_path() -> Path:
    Path(settings.SAMPLE_DIR).mkdir(parents=True, exist_ok=True)
    return Path(settings.SAMPLE_DIR) / "synthetic_samples.csv"


def load_or_generate_samples(n_samples: int | None = None) -> pd.DataFrame:
    """优先读取已生成的样本 CSV，否则重新生成"""
    path = _sample_path()
    n = n_samples or settings.SEED_SAMPLES
    if path.exists() and n_samples is None:
        df = pd.read_csv(path)
        if len(df) >= 500:
            return df
    df = generate_samples(n=n, default_rate=settings.SEED_DEFAULT_RATE)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return df


def _fused_samples_path() -> Path | None:
    """真实调查数据融合样本（CHFS + CMES + CFPS → fused_samples_v1.csv）"""
    p = Path(settings.SAMPLE_DIR) / "fused_samples_v1.csv"
    return p if p.exists() else None


def _fused_feature_cols(df: pd.DataFrame, max_missing: float = 0.95) -> list[str]:
    """融合样本入模特征：排除内部列（source/*_wave/_*）与标签，仅保留数值指标列。

    与 scripts/fusion/mappings.MODEL_FEATURES 对齐（按列是否存在过滤，缺失率<max_missing）。
    阈值 0.95（而非 0.9）：多数特征是"源专属"（CMES 经营年限/从业/贷款、CHFS 负债率、
    CFPS 民间借贷），融合后整体缺失率虚高是因为其它源对该字段贡献 0，而非数据质量差；
    过严阈值（0.9）会把有业务价值的 CMES 特征误杀。IV/VIF 会进一步剔除弱特征。
    """
    MODEL_FEATURES = [
        "BASIC_003", "BASIC_004", "BASIC_005", "BASIC_008", "BASIC_009", "BASIC_019",
        "01_05", "0111_01", "0111_05", "0111_08", "0112_04", "1041_02",
        "_cmes_had_loan", "_cmes_profit", "_cmes_credit", "_cmes_purchase_credit",
        "_chfs_agri", "_chfs_income",
        "_cfps_agri", "_cfps_livestock", "_cfps_hus_input", "_cfps_private_debt",
        "_cfps_income", "_cfps_assets",
        "_total_asset", "_hh_size",
    ]
    feats = []
    for c in MODEL_FEATURES:
        if c not in df.columns:
            continue
        if df[c].isna().mean() <= max_missing:
            feats.append(c)
    return feats


def _source_iv_keep(df: pd.DataFrame, feats: list[str], target: str = "default", min_iv: float = 0.02) -> list[str]:
    """IV 准入：全量 IV 与各数据源内 IV 取最大值，任一 ≥min_iv 即保留。

    数据源：CMES（小微企业）、CHFS（家庭金融）、CFPS（家庭追踪）。特征多带"源专属"属性：
    - 全量 IV：反映特征在整体融合样本上的区分力（缺失会被其它源稀释）
    - 源内 IV：反映特征在其数据充足的源内的真实区分力（如 CMES 经营年限 IV≈0.51）
    取两者最大值判定，避免两类误杀：
    - 只看全量 IV：CMES 专属特征因源样本占比小而 IV≈0 被误杀
    - 只看单一"数据最全源"源内 IV：BASIC_019 贷款在 CHFS/CFPS 源内 IV≈0、
      但全量 IV=0.19、CMES 源内 IV=2.76，会被误杀
    """
    from app.ml.binning import WOEBinner

    keep: list[str] = []
    for f in feats:
        if f not in df.columns:
            continue

        def _iv(sub: pd.DataFrame) -> float | None:
            if sub[f].notna().sum() < 500 or sub[target].sum() < 30:
                return None
            binner = WOEBinner(f, is_categorical=False, max_bins=5, min_bin_pct=0.05)
            try:
                binner.fit(sub[f], sub[target])
                return float(binner.iv_)
            except Exception:
                return None

        # 全量 IV
        full_iv = _iv(df)
        if full_iv is not None and full_iv >= min_iv:
            keep.append(f)
            continue
        # 各源内 IV（任一个达到 min_iv 即保留）
        for src in ("CMES", "CHFS", "CFPS"):
            sub = df[df["source"] == src]
            if f not in sub.columns:
                continue
            src_iv = _iv(sub)
            if src_iv is not None and src_iv >= min_iv:
                keep.append(f)
                break
    return keep


def _display_iv_table(
    df: pd.DataFrame, feats: list[str], target: str = "default", base_iv: list[dict] | None = None
) -> list[dict]:
    """IV 展示值：取「全量 IV 与各源内 IV 的最大值」，避免源专属特征在全量图上显示为 0。

    base_iv: Scorecard 计算的全量 ivTable（含 nBins），用于保留分箱数等信息。
    返回按原顺序的 ivTable，iv 字段替换为展示值。
    """
    from app.ml.binning import WOEBinner

    # 收集每个特征的全量/各源 IV 最大值
    best_iv: dict[str, float] = {}
    for f in feats:
        if f not in df.columns:
            continue

        def _iv(sub: pd.DataFrame) -> float | None:
            if sub[f].notna().sum() < 500 or sub[target].sum() < 30:
                return None
            binner = WOEBinner(f, is_categorical=False, max_bins=5, min_bin_pct=0.05)
            try:
                binner.fit(sub[f], sub[target])
                return float(binner.iv_)
            except Exception:
                return None

        values = [v for v in [_iv(df)] + [_iv(df[df["source"] == s]) for s in ("CMES", "CHFS", "CFPS")] if v is not None]
        best_iv[f] = max(values) if values else 0.0

    if base_iv:
        return [dict(x, iv=round(best_iv.get(x.get("factor", ""), x.get("iv", 0.0)), 6)) for x in base_iv]
    return [{"factor": f, "iv": round(best_iv.get(f, 0.0), 6), "nBins": 2} for f in feats]


def run_fused_training(
    db=None,
    trained_by: str | None = None,
    version: str | None = None,
    use_smote: bool | None = None,
    default_rate: float = 0.05,
) -> dict | None:
    """真实调查数据融合训练（主路径）：读 fused_samples_v1.csv → 合成 A 标签 → 训练数据层评分卡。

    融合数据缺失时返回 None（由调用方回退合成样本）。
    """
    path = _fused_samples_path()
    if path is None:
        return None

    import numpy as np

    df = pd.read_csv(path)
    feats = _fused_feature_cols(df)
    if len(feats) < 2:
        return None

    # A 主标签：可解释风险因子加权 → sigmoid 校准违约率（与 scripts/fusion/train_fused_model 一致）
    def _std(x: pd.Series) -> pd.Series:
        s = x.astype(float)
        return (s - s.mean()) / (s.std() + 1e-9)

    rng = np.random.default_rng(42)
    z = _std(df["BASIC_008"].fillna(df["BASIC_008"].median())) * 0.40
    if "BASIC_003" in df and df["BASIC_003"].notna().mean() > 0.1:
        z = z + _std(df["BASIC_003"].fillna(df["BASIC_003"].median())) * 0.28
    if "BASIC_005" in df and df["BASIC_005"].notna().mean() > 0.1:
        z = z + _std(df["BASIC_005"].fillna(df["BASIC_005"].median())) * 0.16
    if "01_05" in df and df["01_05"].notna().mean() > 0.1:
        z = z + _std(df["01_05"].fillna(df["01_05"].median())) * 0.20
    if "BASIC_019" in df and df["BASIC_019"].notna().mean() > 0.1:
        z = z - _std(df["BASIC_019"].fillna(0)) * 0.45
    if "BASIC_009" in df and df["BASIC_009"].notna().mean() > 0.1:
        z = z - _std(df["BASIC_009"].fillna(df["BASIC_009"].median())) * 0.45
    neg = pd.Series(0.0, index=df.index)
    for c in ("_cmes_credit", "_cmes_purchase_credit", "_cfps_private_debt"):
        if c in df and df[c].notna().mean() > 0.1:
            neg = neg + df[c].fillna(0)
    if neg.abs().sum() > 0:
        z = z - _std(neg) * 0.30
    z = z + rng.standard_normal(len(df)) * 0.04

    z_std = (z - z.mean()) / max(z.std(), 1e-9)
    alpha = 2.8
    lo, hi = -10.0, 10.0
    for _ in range(60):
        mid = (lo + hi) / 2
        p = 1.0 / (1.0 + np.exp(alpha * z_std + mid))
        if p.mean() > default_rate:
            lo = mid
        else:
            hi = mid
    beta = (lo + hi) / 2
    df["default"] = (rng.random(len(df)) < (1.0 / (1.0 + np.exp(alpha * z_std + beta)))).astype(int)

    # 源内 IV 准入：候选特征在其数据最全的数据源内部计算 IV（≥0.02 保留）。
    # 原因：全量融合训练时，CMES 特征（经营年限/从业/贷款/民间借款）因 CMES 仅占 5.9%、
    # 且合成标签由 CHFS/CFPS 大样本驱动，全量 IV≈0 被 Scorecard 剔除；但源内 IV 很高
    # （如 BASIC_003 经营年限 IV=0.51），是有业务价值的特征，不应被"淹没"。
    feats = _source_iv_keep(df, feats, target="default")

    version = version or f"v{datetime.now():%Y%m%d%H%M%S%f}"
    scorecard = Scorecard(
        version=version,
        use_smote=settings.SMOTE_ENABLED if use_smote is None else use_smote,
        feature_cols=feats,
        categorical_cols=[],
        eval_threshold_mode="default_rate",
        skip_iv_filter=True,  # 特征已由源内 IV 准入，跳过全量 IV 二次剔除（避免 CMES 特征被误杀）
    )
    scorecard.fit(df[feats + ["default"]], target_col="default")

    artifact_path = model_artifact.save_scorecard(scorecard)
    metrics = dict(scorecard.metrics)

    # IV 图展示优化：全量 IV 会把 CMES 源内特征（经营年限/从业/贷款/民间借款）显示成 0，
    # 因为它们在 CHFS/CFPS 大样本上无数据被稀释。改为展示「全量 IV 与各源内 IV 的最大值」，
    # 让每个特征反映其实际可用于建模的预测力（与 _source_iv_keep 准入口径一致）。
    metrics["ivTable"] = _display_iv_table(df, feats, target="default", base_iv=metrics.get("ivTable"))

    metrics["model_type"] = "data_layer"
    metrics["trainSource"] = "fused_real_survey"
    result = {
        "version": version,
        "artifactPath": artifact_path,
        "nSamples": scorecard.n_samples,
        "nFeatures": len(scorecard.feature_names),
        "auc": metrics.get("auc"),
        "ks": metrics.get("ks"),
        "recall": metrics.get("recall"),
        "precision": metrics.get("precision"),
        "f1": metrics.get("f1"),
        "accuracy": metrics.get("accuracy"),
        "defaultRate": metrics.get("defaultRate"),
        "psi": metrics.get("psi"),
        "cvScores": metrics.get("cvScores", []),
        "smoteApplied": metrics.get("smoteApplied", False),
        "featureNames": scorecard.feature_names,
        "trainedBy": trained_by,
        "trainSource": "fused_real_survey",
        "metrics": metrics,
    }

    if db is not None:
        from app.models.model_version import ModelVersion

        db.query(ModelVersion).filter(ModelVersion.status == "active").update({"status": "inactive"})
        mv = ModelVersion(
            version=version,
            status="active",
            n_samples=scorecard.n_samples,
            n_features=len(scorecard.feature_names),
            auc=metrics.get("auc"),
            ks=metrics.get("ks"),
            recall=metrics.get("recall"),
            precision=metrics.get("precision"),
            f1=metrics.get("f1"),
            metrics_json=metrics,
            artifact_path=artifact_path,
            trained_by=trained_by,
        )
        db.add(mv)
        db.commit()

    return result


def run_training(
    n_samples: int | None = None,
    db=None,
    trained_by: str | None = None,
    version: str | None = None,
    use_smote: bool | None = None,
) -> dict:
    """执行一次完整训练，返回结果摘要"""
    df = load_or_generate_samples(n_samples)

    version = version or f"v{datetime.now():%Y%m%d%H%M%S%f}"
    scorecard = Scorecard(
        version=version,
        use_smote=settings.SMOTE_ENABLED if use_smote is None else use_smote,
    )
    scorecard.fit(df, target_col="default")

    # 保存模型文件
    artifact_path = model_artifact.save_scorecard(scorecard)

    # 三组对比实验（对齐计划书 3.3.3）
    try:
        from app.ml.experiments import run_experiments

        experiments = run_experiments(df, target="default")
        scorecard.metrics["experiments"] = experiments
    except Exception:
        scorecard.metrics["experiments"] = None

    # 汇总指标
    metrics = dict(scorecard.metrics)
    result = {
        "version": version,
        "artifactPath": artifact_path,
        "nSamples": scorecard.n_samples,
        "nFeatures": len(scorecard.feature_names),
        "auc": metrics.get("auc"),
        "ks": metrics.get("ks"),
        "recall": metrics.get("recall"),
        "precision": metrics.get("precision"),
        "f1": metrics.get("f1"),
        "accuracy": metrics.get("accuracy"),
        "defaultRate": metrics.get("defaultRate"),
        "psi": metrics.get("psi"),
        "cvScores": metrics.get("cvScores", []),
        "smoteApplied": metrics.get("smoteApplied", False),
        "featureNames": scorecard.feature_names,
        "trainedBy": trained_by,
        "metrics": metrics,
    }

    # 注册模型版本（若提供 db）
    if db is not None:
        from app.models.model_version import ModelVersion

        # 旧版本置为非激活
        db.query(ModelVersion).filter(ModelVersion.status == "active").update({"status": "inactive"})
        mv = ModelVersion(
            version=version,
            status="active",
            n_samples=scorecard.n_samples,
            n_features=len(scorecard.feature_names),
            auc=metrics.get("auc"),
            ks=metrics.get("ks"),
            recall=metrics.get("recall"),
            precision=metrics.get("precision"),
            f1=metrics.get("f1"),
            metrics_json=metrics,
            artifact_path=artifact_path,
            trained_by=trained_by,
        )
        db.add(mv)
        db.commit()

    return result


def load_active_model(db=None):
    """加载当前激活模型（优先 DB 中的 active 版本，其次最新模型文件）"""
    if db is not None:
        from app.models.model_version import ModelVersion

        mv = db.query(ModelVersion).filter(ModelVersion.status == "active").order_by(ModelVersion.id.desc()).first()
        if mv and mv.artifact_path:
            model = model_artifact.load_scorecard(mv.artifact_path)
            if model is not None:
                return model
    path = model_artifact.latest_artifact()
    if path:
        return model_artifact.load_scorecard(path)
    return None
