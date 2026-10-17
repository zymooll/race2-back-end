# 脚本与资料索引

核对日期：2026-10-17。以下命令均从 `back-end` 目录执行。

在后端仓库根目录执行脚本。依赖使用 `requirements-dev.txt`；调查数据和 SQL 备份属于本地资料，不纳入 Git。

安装：`python -m pip install -r requirements-dev.txt`。该文件已包含后端运行依赖，以及 Excel 解析、配图、LightGBM 对比和测试工具。Linux 上运行 LightGBM 需要系统 OpenMP 运行库（如 `libgomp1`）；`phase0_extract_cfps.py` 的 RAR 解压分支需要系统安装 `7z`，这两项不属于 pip 包。

## 当前指标维护

- `migrate_202610_region_tags.py`：既有数据库的幂等区域标签结构迁移；先备份，再在目标环境运行。
- `import_indicators.py --xlsx "../fore-end/docs/农业及相关产业动态指标搜集体系.xlsx" --check-only`：只解析当前 3023 条、15 列源文件，核对数量和编码，不写数据库。去掉 `--check-only` 会替换现有类别和指标配置，需预先保存人工维护的评分参数。显式指定路径可避免脚本优先选中旧名称 `race2-fore-end` 目录中的文件。
- `stats_indicator_config.py` / `stats_indicator_source.py`：只读统计配置覆盖率和数据来源，默认连接本地 race3 MySQL。
- `smoke_expert_engine.py`：本地专家引擎场景打印，参考分数来自旧版快照，仅作人工对照；正式回归使用 `python -m pytest tests -q`。

## 历史全国化复现

`nationalize_scan.py`、`nationalize_apply.py` 和 `verify_nationalize.py` 记录早期 3020 条、14 列指标的全国化过程。扫描及转换默认读取 `docs_archive_20260920/` 的原始版本，不操作当前前台 15 列源文件。

`nationalize_apply.py` 会在归档目录生成 `_national` 文件并更新 `data/nationalize_changes.csv`。`verify_nationalize.py`、`compare_scoring_config.py` 和 `compare_legacy_dump.py` 需要未入库的本地 SQL 备份，并只读对照数据库；其中「全部为全国」的核验口径仅适用于旧版。历史报告与当前版本的区别见归档目录 README。

## 数据与论文辅助

- [fusion/README.md](fusion/README.md)：当前 CFPS / CHFS / CMES 多源清洗、融合与评分卡训练流程，以及单独标注的历史实验结果。[DATA_PIPELINE.md](DATA_PIPELINE.md) 为早期双源代理管道的历史说明。
- `train_model.py [样本量]`：合成样本评分卡训练并注册激活版本；管理端训练优先融合样本的逻辑在 `app/services/model_service.py`，两者不是同一路径。
- `level4_review.md`：旧版第 4 级指标人工核验快照；其中 2223 条、401 类是历史数量，当前指标以 `--check-only` 统计为准。
- `inspect_datasets.py`：只读盘点本仓库 `data/数据集/` 中的 Stata 压缩数据。
- `read_docx.py`：使用 Python 标准库提取 DOCX 文本，可按标题、关键词或段落范围查看。
- `gen_chapter3_diagrams.py`：生成第三章架构图与操作流程图，输出到 `docs/第三章配图/`；需安装开发依赖中的 matplotlib。

`data/requirements-venv-snapshot.txt` 是历史本地环境快照，供复现参考；日常安装以 `requirements.txt` 和 `requirements-dev.txt` 为准。

论文方案、论文参考报告/问卷/代码本已归入工作区根 `docs/`，见[参考资料索引](../../docs/参考资料索引.md)。检查缓存、虚拟环境和脚本输出留在后端目录内，分别使用 `.ruff_cache/`、`.venv/`、`data/` 或脚本指定的 `docs/第三章配图/`。
