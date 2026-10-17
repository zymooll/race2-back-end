# 多源数据融合管道

核对日期：2026-10-17。当前多源流程替代 [旧 Phase 3 双源代理流程](../DATA_PIPELINE.md)。本文先说明当前代码行为，再保留历史实验结果。

> 目标：使用 CHFS / CMES / CFPS 调查特征训练数据层评分卡。A 为合成训练标签，B 为独立负面信号的一致性验证；B 不直接充当真实逾期训练标签。清洗文件及 `versions.json` 记录数据处理版本。

## 环境与输入

从 `back-end` 目录执行 `python -m pip install -r requirements-dev.txt`。离线模型比较需要 `lightgbm`，Linux 还需系统 OpenMP 运行库（如 `libgomp1`）；CFPS RAR 解压分支需要系统 `7z`。

原始资料位于 `data/数据集/`，解压文件的实际路径以 `mappings.py` 的 `SOURCE_FILES` 为准。压缩包需具备访问授权并预先完成必要解密；`phase0_extract_cfps.py` 不会自动取得密码或解密受保护的 ZIP。解压文件缺失时，清洗会打印跳过信息，需核对实际参与融合的数据源和波次。

## 管道流程

```mermaid
flowchart LR
    A[CHFS 2015 dta] --> C[clean_sources.py<br/>按 wave 清洗+版本化]
    B[CMES 2015 dta] --> C
    D[CFPS 2016/2018/2020/2022<br/>famecon] --> C
    C --> E[data/cleaned/{源}_{年份}_{版本}.csv<br/>+ 清洗报告 + versions.json]
    E --> F[fuse_sources.py<br/>纵向堆叠 + 缺失报告]
    F --> G[fused_samples_版本.csv]
    G --> H[train_fused_model.py<br/>A 合成标签训练 + B 信号验证]
    H --> I[data_layer 模型]
```

## 执行顺序

以下以 `v1` 为示例。`clean_sources.py` 对同一版本会覆盖 CSV 和报告，并向 `versions.json` 追加记录；保留旧结果时使用新版本号，清洗与融合必须使用相同版本。

```bash
# Phase 0: 解压具备访问权限的 CFPS 文件
python scripts/phase0_extract_cfps.py
python scripts/phase0_build_field_catalog.py     # -> data/samples/field_catalog.csv

# Phase 1: 可选的映射辅助，需事先提供 data/indicator_dump.sql
python scripts/phase1_prepare_indicator_catalog.py  # -> indicator_catalog.csv（数量取决于 SQL 快照）
python scripts/phase1_auto_map.py                   # -> mapping_candidates.csv

# Phase 2: 每源/每波清洗 + 版本化（核心映射在 scripts/fusion/mappings.py）
python scripts/fusion/clean_sources.py --sources CMES,CHFS,CFPS --version v1

# Phase 3: 多源融合 + 缺失报告
python scripts/fusion/fuse_sources.py --version v1

# Phase 5: A 标签训练 + B 信号验证，保存模型，不注册数据库
python scripts/fusion/train_fused_model.py --version v1 --subset all
```

需要注册时单独执行完整命令 `python scripts/fusion/train_fused_model.py --version v1 --subset all --register`。此命令会重新训练、保存产物并写目标数据库，将原有激活版本置为 `inactive`，注册新的 `active` 数据层模型；先核对数据库连接和产物位置。

Phase 1 从 SQL 快照生成映射候选，不会替换当前前端 3023 条、15 列指标源，也不是使用已有 `mappings.py` 清洗规则的必需步骤。

## 管理端、启动与离线训练的区别

| 入口 | 选取样本与特征 | 注册行为 |
| --- | --- | --- |
| 管理端 `POST /api/v1/model/train` | 优先 `SAMPLE_DIR/fused_samples_v1.csv`；候选特征缺失率 `≤0.95`，再做全量/源内 IV 准入与评分卡 VIF 筛选；样本缺失或有效特征不足时回退合成样本 | 生成新的激活模型版本 |
| `train_fused_model.py` | 按 `--version` 读取融合样本；同样使用缺失率 `≤0.95` 和全量/源内 IV 准入；`--subset` 支持 `cmes/chfs/all`，默认 `cmes`，其中 `chfs` 包含 CHFS 与 CFPS 家庭样本 | 仅 `--register` 时写库并激活 |
| 启动自动训练 / `scripts/train_model.py` | 15 项合成指标样本；启动还需自动训练开启且没有可加载的激活模型 | 注册激活的合成样本模型 |

管理端当前固定寻找 `fused_samples_v1.csv`，离线生成 `v2` 等新版本不会自动切换其输入。线上混合引擎从模型记录中选择最新的 `model_type=data_layer` 模型，状态可以是 `active` 或 `inactive`。Docker 模型/样本使用持久卷，本机文件需要显式恢复或复制到对应卷，见 [data/README.md](../../data/README.md)。

当前两条融合训练路径均调用 `app/ml/training.py` 的 `_source_iv_keep`：取全量与各源内 IV 的最大值，任一达到 `0.02` 即通过准入；传给评分卡时设置 `skip_iv_filter=True`，避免再次按全量 IV 剔除源专属特征。代码中的旧注释或历史实验 `<0.9` 门槛不代表当前实现。

## 核心模块（scripts/fusion/）

| 文件 | 职责 |
|---|---|
| `mappings.py` | 核心映射字典（含 wave 和 clean_rule DSL）、数据源可信度、按 wave 读取字段与候选入模特征；数量以当前字典为准 |
| `clean_engine.py` | 清洗规则表达式引擎（AST 白名单：clip/sum/div/map/coalesce/fill/mask，防注入） |
| `clean_sources.py` | 每源/每波独立清洗 → data/cleaned/{源}_{年份}_{版本}.csv + 报告 + versions.json；同版本重跑会覆盖文件、追加记录 |
| `fuse_sources.py` | 纵向堆叠多源多波样本 + 特征缺失率报告（>50% 建议剔除） |
| `train_fused_model.py` | A 标签 + Scorecard 训练 + B 信号验证 + 可选注册 |
| `compare_models.py` | Logistic 评分卡与 LightGBM 的离线比较；`--subset` 支持 `all/cmes/hh` |
| `export_model_meta.py` | 从模型产物生成元数据 JSON 和注册 SQL；不自动执行 SQL |

## 历史 v1 数据源与波次快照

下表及后续历史指标沿用既有实验记录；当前运行的数据量、模型 ID 和指标应从本地清洗报告、融合报告与目标数据库读取。

| 数据源 | 文件 | 样本量(v1) | 映射 |
|---|---|---|---|
| CMES 2015 | cmes2015_191228.dta | 5,497 | 21 条 |
| CHFS 2015 | chfs2015_master_hh_pub_v1_20260707.dta | 37,289 | 8 条 |
| CFPS 2016 | cfps2016famecon_201807.dta | 14,019 | 5 条 |
| CFPS 2018 | cfps2018famecon_202512.dta | 14,215 | 9 条 |
| CFPS 2020 | cfps2020famecon_202306.dta | 11,620 | 9 条 |
| CFPS 2022 | cfps2022famecon_202410.dta | 10,726 | 9 条 |
| **融合** | fused_samples_v1.csv | **93,366** | 25 指标列 |

## 清洗规则 DSL（clean_rule 示例）

- `clip(2015-{a1006},0,60)` 经营年限（年份相减+截断）
- `div(sum({bi3006},{bi3011}),10000)` 收入加总转万元
- `map({e1014},{1:1,2:0})` 枚举→0/1
- `coalesce({fs101},{fs201})` 多字段取首非空
- `mask({agri_asset},sum({agri_asset},{agri_inc})>0)` 条件掩码

## 训练标签 A 与验证信号 B

- **A 主标签**：营收/年限/从业/土地（+）与贷款/负债率/民间借款（−）加权，sigmoid 校准违约率 5%
- **B 验证**：未与入模特征重叠的负面信号与预测概率的排序一致性（Spearman）；包括借款被拒等调查字段，不等同于真实逾期标注

## 历史 v1 结果（全样本 93,366 行）

- 入模特征（缺失率<0.9 自动筛选）12 项：BASIC_008/009/019、01_05、_chfs_agri/_chfs_income、_cfps_agri/_cfps_hus_input/_cfps_private_debt/_cfps_income/_cfps_assets/_cfps_total_asset
- 5 折 CV **AUC 0.7950±0.0025**、KS 0.53、PSI 0.0007；A 标签违约率 5.05%
- **B 验证（防泄漏）Spearman=0.105（p=1.6e-56, n=22,745）**：
  - 真实 `predict_proba` 违约概率（非特征均值占位）
  - 独立真实负面信号 `_cfps_rejected`（CFPS ft8 借款被拒经历，1=被拒/5=未拒，**不入模**，2018plus 三波）
  - 排除与入模特征重叠的信号变量（`_cfps_private_debt` 曾是特征+信号→自相关虚高 0.524，已剔除）
  - 方向正确：被拒过的家庭模型违约概率更高；与早期 CMES 子集（0.103）交叉印证
- 关键特征缺失率已改善：BASIC_019 贷款 5.5%、BASIC_008 营收 15.5%、_cfps_private_debt 45.8%
- 模型产物：data/models/scorecard_v*.pkl（旧实验曾记录模型 id=6；目标环境当前 ID 以数据库为准，metrics_json 含 model_type/scoreQuantiles）

## 已知限制与后续迭代

1. **AUC 偏乐观**：合成标签来自同批指标（自我验证），B 验证（0.105）才是与真实信号的参考强度
2. **跨源缺失仍高**：早期离线全量训练的 `<0.9` 门槛剔除了部分 CMES 专属特征；当前管理端和离线训练均采用 `≤0.95` 加全量/源内 IV 准入，不能直接套用旧实验的入选列表
3. **B 验证信号有限**：当前从 CMES 信贷/民间借款及 CFPS 民间债务/借款被拒字段中选择未入模且非空比例 `>0.1` 的信号，具体可用字段随子集和入选特征变化；历史全量实验主要使用 CFPS ft8，不能当成真实逾期标签
4. **映射字典待扩展**：历史字段清单曾为 9,282 条；当前映射及清单数量以代码和本次输出为准（mapping_candidates.csv 是候选）
5. **清洗规则 DSL** 需更多算子（if/聚合窗口），可按需扩展 clean_engine
6. 合成样本仍用于启动自动训练、`train_model.py`、测试及管理端融合样本不足时的兜底路径
7. **推送 main/dev 且脚本路径变化会触发部署 workflow**（paths 含 scripts/**，也包含本目录 README），部署流程见后端 README

## 实验与优化（2026-08-09）

### 模型选型对比（compare_models.py）

| 模型 | 5 折 CV AUC | KS | B 验证 Spearman（真实信号） |
|---|---|---|---|
| Logistic 评分卡 | 0.7974±0.0025 | 0.5367 | **0.106**（p=9e-58，显著） |
| LightGBM | 0.8317±0.0050 | 0.6021 | **0.004**（p=0.53，不显著） |

- 历史实验中，LightGBM 的合成标签 AUC 较高，但在该次所选 B 信号上的相关性不显著；评分卡在该 B 信号上呈显著相关。这是特定数据与代理信号下的结果，不能据此认定真实逾期预测的泛化能力。

### 分客群建模对比（compare_models.py --subset）

| 客群 | 评分卡 CV AUC | LightGBM CV AUC |
|---|---|---|
| 企业（CMES 5,497） | **0.9168±0.0168** | 0.9036±0.0133 |
| 家庭（CHFS+CFPS ~85k） | 0.9038±0.0043 | **0.9369±0.0030** |
| 全量（93,366） | 0.7974±0.0025 | 0.8317±0.0050 |

- 分客群评分卡 AUC 显著高于全量（企业特征 BASIC_004/005/1041_02 在分客群时可用）→ **分客群建模有价值**（合成标签下）

### B 验证信号扩展（2016 波 ft8）

- CFPS 2016 波新增 ft8（借款被拒）映射 → B 验证样本 **22,745 → 31,881**，Spearman 0.086（p=6e-54 仍显著）
- CHFS 2015 无逾期/违约字段，B 信号仍以 CFPS 为主

### 历史数据层混合参数敏感性（sensitivity_analysis.py，3 特征场景）

- 该次场景打分范围 275-711；0.15/0.85 参数下：**下修触发 11.4%（均幅 -23 分）、上修触发 20.5%（均幅≈-0.9 分，可忽略）**
- 0.25/0.75 过激（下修 37%）；下修权重 0.5 合理
- **结论**：下修是数据层混合的主要价值；上修路径（0.9/0.1）实际近乎无效，后续可考虑调权重或移除

## 当前线上辅助能力

### 专家引擎概率校准（app/ml/calibration.py）

- 用真实回填数据（outcome）Platt 校准 score→逾期概率，替代固定 sigmoid((score-550)/80)
- 用户可回填本人记录，管理员可回填所有记录；`normal/overdue` 两类样本合计 ≥50 条且各 ≥5 条时尝试启用（TTL 5 分钟缓存），数据不足或拟合失败回退默认映射；`pending/rejected` 不参与校准
- 随回测数据积累自动生效

### 数据层触发监控（GET /api/v1/risk/records/data-layer-stats）

- 仅管理员可访问，统计线上评估中数据层混合触发率、dataScore 分布（基于 result_json.overrides）

### 枚举打分候选 map 工具（gen_enum_map_candidates.py）

- 历史快照中枚举指标 1,502 个，其中 1,118 个（74%）无 scoring_config.map（走关键词启发）
- 历史运行曾为其中 **1,117 个生成候选 map**（复用 build_enum_map 启发，含中性跳过 11 个）→ data/enum_map_candidates.csv，**需人工审核后应用**；当前数量取决于目标数据库

### 管道半自动执行

```bash
# 数据更新后使用新的版本号逐步处理；将 vN 和 vXXX 替换为实际版本
python scripts/fusion/clean_sources.py --sources CMES,CHFS,CFPS --version vN
python scripts/fusion/fuse_sources.py --version vN
python scripts/fusion/train_fused_model.py --version vN --subset all
python scripts/fusion/export_model_meta.py --pkl data/models/scorecard_vXXX.pkl
# 在目标环境恢复对应 pkl，核对 SQL 的版本和路径后再执行注册
```
完整 CI（数据更新→自动训练→自动注册）建议后续接入 GitHub Actions 独立 workflow，避免与主部署耦合。
