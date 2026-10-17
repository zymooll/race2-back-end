# data 目录说明

核对日期：2026-10-17。本目录保存模型产物、研究数据、清洗与训练输出，以及本地 SQL 备份。业务数据库运行于 MySQL，不使用这里的 SQLite 文件。

## 本地目录与版本管理

| 路径 | 用途 | Git 管理 |
| --- | --- | --- |
| `数据集/` | CFPS / CHFS / CMES 原始调查资料与压缩文件 | 忽略 |
| `raw/` | 解压后的 Stata 等中间数据，供清洗或旧代理管道使用 | 忽略 |
| `cleaned/` | 多源分波次清洗 CSV、报告及 `versions.json` | 忽略输出，保留 `.gitkeep` |
| `samples/` | `fused_samples_<版本>.csv`、合成/代理样本和分析报告 | 忽略输出，保留 `.gitkeep` |
| `models/` | `scorecard_*.pkl`、模型元数据及注册 SQL | 忽略输出，保留 `.gitkeep` |
| `*.sql`、`*.db` | 本地数据库备份或历史文件 | 忽略 |
| `nationalize_*.csv` | 旧版 14 列指标全国化扫描、变更与核验快照 | 随仓库保留 |
| `enum_map_candidates.csv` | 枚举打分候选配置，应用前需人工审核 | 随仓库保留 |
| `requirements-venv-snapshot.txt` | 历史环境依赖快照 | 随仓库保留；安装使用仓库根 requirements 文件 |

当前管道见 [scripts/fusion/README.md](../scripts/fusion/README.md)；[scripts/DATA_PIPELINE.md](../scripts/DATA_PIPELINE.md) 为旧版双源代理流程。清洗脚本重复使用同一版本会覆盖同名文件并追加版本记录，保留旧结果时应使用新的版本号。

Docker 中模型与样本位于 `race3_model_data`、`race3_sample_data` 持久卷，分别挂载至 `/app/data/models`、`/app/data/samples`；这些目录没有绑定本机同名目录，本机新生成的文件不会自动出现在容器中。MySQL 数据保存在 `race3_mysql_data` 卷。恢复时应保持数据库模型版本记录与模型文件路径对应。

论文参考报告、问卷和代码本保存在工作区根 `docs/参考资料/`，见[参考资料索引](../../docs/参考资料索引.md)；它们与本目录的原始调查样本分开管理。

## 研究数据备份约定（Cloudflare R2）

`data/数据集/` 不进入 Git。项目已有的 R2 备份位置约定如下；恢复前通过团队授权渠道配置访问凭证，并核对所需数据版本：

- **Bucket**: `race-datasets`
- **前缀**: `datasets/`（结构同本地 `data/数据集/` 目录）
- **S3 端点**: `https://d08d42f9b1e53cc1648cdc9c1eab5a0e.r2.cloudflarestorage.com`

### 恢复下载命令

```bash
# 方式一：rclone
rclone copy r2:race-datasets/datasets ./data/数据集/ -P

# 方式二：AWS CLI，使用团队配置的授权凭证和 R2 端点
aws s3 sync s3://race-datasets/datasets ./data/数据集/ --endpoint-url https://d08d42f9b1e53cc1648cdc9c1eab5a0e.r2.cloudflarestorage.com
```

`rclone` 需要事先配置名为 `r2` 的 remote；`rclone` 与 AWS CLI 均为可选恢复工具，不是后端运行依赖。恢复大文件后继续使用当前 `.gitignore` 规则。
