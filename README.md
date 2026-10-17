# 涉农信贷风险智能评估系统 · 后端管理平台

> "挑战杯"全国大学生课外学术科技作品竞赛
> 基于多元统计模型的涉农小微企业信贷风险智能评估系统

文档核对日期：2026-10-17。启动、接口权限与部署说明按当前仓库代码及配置整理。

本仓库为项目**后端 + 完整可视化后台管理平台**，包含：

- **动态专家引擎**（生产主路径）：四层动态指标体系（基本项 + 大类→中类→小类→具体营业类型），逐指标 0-100 分加权合成 0-1000 分，叠加一票否决红线与混合经营协同因子
- **数据层评分卡**：IV 特征筛选 → WOE 编码 → VIF 共线性诊断 → Logistic 回归 → 0-1000 分评分卡；已注册数据层模型且输入特征充分时，可参与线上专家引擎的混合调整
- **后端 API 服务**：FastAPI + SQLAlchemy 2.x + MySQL 8.0
- **后台管理平台**（`admin-web/`，Vue3 + Element Plus）：登录、系统概览、数据管理、模型训练与评估、API 管理（列表/日志/测试控制台）、系统监控（服务器/数据库/健康检查）
- **竞赛前端对接**：`fore-end/`（同级目录）已从模拟模型切换为调用本后端真实 API

---

## 目录结构

```
back-end/
├── app/                    # 后端主程序
│   ├── main.py             # FastAPI 入口（启动初始化 + 自动训练 + 挂载管理端）
│   ├── core/               # 配置 / JWT安全 / 异常 / 统一响应
│   ├── db/                 # SQLAlchemy 引擎与会话、初始化
│   ├── models/             # User / AssessmentRecord / ModelVersion / SystemConfig / ApiLog
│   ├── schemas/            # Pydantic 模型（对齐前端契约）
│   ├── api/v1/             # auth / captcha / risk / dashboard / model / admin / monitor / indicator
│   ├── services/           # 业务逻辑层
│   ├── ml/                 # ★ 专家引擎 + 数据层评分卡（WOE/IV、Logistic、种子数据）
│   └── middleware/         # API 请求日志中间件
├── admin-web/              # 后台管理平台前端（Vue3 + Element Plus + ECharts）
├── scripts/                # init_db / import_indicators / seed_* / train_* / 迁移脚本
├── data/                   # 模型、融合/合成样本、清洗结果及本地备份
│   └── 数据集/             # CFPS/CHFS/CMES，本地存储、Cloudflare R2 备份，不纳入 Git
├── tests/                  # pytest：认证、动态评估、区域元数据、解释契约与管理监控
├── docs/                   # 管理看板等接口契约
├── docs_archive_20260920/   # 14 列指标体系及全国化中间版本（仅供历史追溯）
├── Dockerfile              # 多阶段构建（前端 + 后端）
├── requirements.txt        # 运行依赖（生产镜像安装此文件）
├── requirements-dev.txt    # 包含运行依赖及测试、资料处理、离线实验、质量工具
└── docker-compose.yml      # backend + MySQL + go-captcha-service
```

工作区同级还有 `fore-end/` 用户前端，以及 `docs/` 论文方案、参考资料和[资料索引](../docs/参考资料索引.md)。`back-end` 与 `fore-end` 各自为独立 Git 仓库，工作区根 `docs/` 不属于这两个仓库；本仓库 `docs/` 保存接口契约和生成配图。

---

## 快速开始（本地开发）

带 `cd back-end` 或 `cd fore-end` 的启动命令从工作区 `race3` 根目录执行；两个 Vue 前端分别使用独立终端。

### 1. 环境准备

- Python 3.13+（Docker 镜像 `python:3.13-slim`）
- Node.js 20.19.x 或 22.12+（Vite 8 的 `engines.node` 为 `^20.19.0 || >=22.12.0`，Docker 镜像 `node:22-alpine`）
- Docker Compose v2（运行 MySQL、验证码服务及可选的后端容器）

### 2. 本地 Docker 一键启动（推荐）

后端、MySQL 与行为验证码服务由当前 Compose 文件一起启动：

```bash
cd back-end
docker compose up -d --build
```

- race3 本地端口：后端 `18001` → 容器 `8000`；MySQL `127.0.0.1:3307` → 容器 `3306`；验证码 `127.0.0.1:19091` → 容器 `8080`。
- 容器：`race3-backend`、`race3-mysql`、`race3-gocaptcha`；MySQL、模型、样本分别持久化在 `race3_mysql_data / race3_model_data / race3_sample_data`，与旧项目隔离。
- 停止：`docker compose down`
- 使用本机 `.venv` 连接上述 Docker MySQL 时，`DATABASE_URL` 使用 `127.0.0.1:3307`；容器内后端使用 `mysql:3306`。
- Compose 会读取同目录 `.env` 和当前终端环境变量。为 Docker 配置 `.env` 时，数据库地址应使用 `mysql:3306`；`.env.example` 中的 `localhost:3306` 是本机连接占位值，不能直接用于后端容器。终端里的同名变量也会覆盖 Compose 的默认值。
- 新库启动只建表并创建默认账号和配置，**不会自动导入动态指标**。首次使用评估功能前，按下文导入当前指标源。迁移已有环境时，应同时恢复业务数据库、模型产物与样本卷；保留模型时可设置 `AUTO_TRAIN_ON_STARTUP=false`，完成恢复后再启动后端。

### 3. 本机 Python 后端（与后端容器二选一）

创建环境并安装依赖。PowerShell：

```powershell
cd back-end
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
# 首次创建配置；保留已有 .env
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

macOS/Linux 对应激活命令为 `source .venv/bin/activate`，复制配置使用 `cp .env.example .env`。开发依赖已包含运行依赖；仅运行服务可安装 `requirements.txt`。

先配置 `.env` 中的数据库和验证码地址，再执行初始化。复用 Compose 配套服务时，本机 Python 使用：

```dotenv
DATABASE_URL=mysql+pymysql://root:race123456@127.0.0.1:3307/race?charset=utf8mb4
CAPTCHA_SERVICE_URL=http://127.0.0.1:19091
```

可用 `docker compose up -d mysql go-captcha-service` 启动配套服务。执行此命令会读取上述 `.env`，但不会启动后端容器。若已启动 backend 容器，先执行 `docker compose stop backend`，释放本机 Python 所需的 `18001` 端口。若改回完整 Docker 部署，先将 `DATABASE_URL` 和 `CAPTCHA_SERVICE_URL` 改为容器内的 `mysql:3306` 和 `http://go-captcha-service:8080`。本机安装的 MySQL 可按实际端口配置，数据库需预先存在。

```bash
# 已激活虚拟环境，当前目录为 back-end
python scripts/init_db.py

# 先只读检查当前工作区指标源
python scripts/import_indicators.py --xlsx "../fore-end/docs/农业及相关产业动态指标搜集体系.xlsx" --check-only

# 仅首次导入新库时执行；已有指标配置时先阅读下文迁移说明
python scripts/import_indicators.py --xlsx "../fore-end/docs/农业及相关产业动态指标搜集体系.xlsx"

# 启动时补齐业务类型配置；默认在无可加载的激活模型时训练合成样本评分卡
uvicorn app.main:app --host 0.0.0.0 --port 18001 --reload
```

已有数据库升级到区域标签版本时，先备份，再在对应环境执行 `python scripts/migrate_202610_region_tags.py`。当前源文件为 3023 条、15 列；迁移只补充结构，不替换指标。导入器会删除并重建类别和指标配置，导入前需保留人工维护的评分参数。脚本默认先寻找旧目录名 `race2-fore-end`，再兼容 `fore-end`，本工作区使用上述显式 `--xlsx` 路径。

Docker 方式首次导入时，也可在本机安装开发依赖后，通过 `127.0.0.1:3307` 连接容器 MySQL 执行导入；生产镜像仅含运行依赖，不含 `openpyxl` 或同级前端的 Excel。导入后重启后端补齐业务类型配置，执行 Compose 前先恢复容器连接配置。

`requirements.txt` 用于后端服务与 Docker 生产镜像。需要运行测试、指标 Excel 导入、论文配图或 LightGBM 对比实验时，安装 `python -m pip install -r requirements-dev.txt`，其中已包含运行依赖。测试客户端使用 `httpx2`；Linux 下运行 LightGBM 另需系统 OpenMP 运行库（如 `libgomp1`）。CFPS 的 RAR 解压脚本还需要系统 `7z` 命令。

FastAPI、scikit-learn 与 imbalanced-learn 限制在已验证的次版本范围内；requirements 不是完整环境锁文件。复现已有模型时应保持训练与加载环境一致，发布前在目标 Python/系统中验证，并另存完整依赖版本清单。

启动后访问：

| 地址 | 说明 |
|---|---|
| http://localhost:18001/admin/ | 后台管理平台（Docker 自动构建；本机 Python 需先构建 admin-web，再重启后端） |
| http://localhost:18001/docs | Swagger 接口文档（开发环境） |
| http://localhost:18001/ | 服务信息 |

### 4. 管理前端（开发模式）

```bash
cd back-end/admin-web
npm install
npm run dev          # http://localhost:5176（已代理 /api → localhost:18001）
```

生产构建（FastAPI 自动挂载到 `/admin`）：

```bash
cd back-end/admin-web
npm run build        # 产物在 admin-web/dist，重启后端即可访问 /admin
```

### 5. 用户前端（fore-end）

```bash
cd fore-end
npm install
npm run dev          # http://localhost:5175（已代理 /api → localhost:18001）
```

> 🔐 **邀约制登录**：本系统不开放注册，账号由后台管理平台「数据管理 → 用户管理」统一开通。默认管理员 `admin/admin123` 可创建分析师账号，供用户前端登录。管理端接口要求管理员角色。登录态保存在浏览器 `localStorage`，刷新页面自动恢复；用户前端完成验证码后，账号或密码错误只提示错误并关闭验证界面，重新点击登录时再开始验证。

---

## 数据库配置（MySQL）

项目使用 **MySQL 8.0**：

数据库连接地址取决于后端运行位置：

| 后端运行位置 | MySQL 主机与端口 | 验证码服务地址 |
| --- | --- | --- |
| 当前 Compose 的 backend 容器 | `mysql:3306` | `http://go-captcha-service:8080` |
| 本机 Python，连接当前 Compose 服务 | `127.0.0.1:3307` | `http://127.0.0.1:19091` |
| 本机 Python，连接自行安装的服务 | 按实际配置，例如 `127.0.0.1:3306` | 按实际验证码服务配置 |

完整连接串格式为 `mysql+pymysql://用户名:密码@主机:端口/race?charset=utf8mb4`。Compose 首次初始化会创建 `race` 数据库；自行安装 MySQL 时先执行 `CREATE DATABASE race DEFAULT CHARACTER SET utf8mb4;`，再运行 `python scripts/init_db.py`。已有数据卷使用创建时的数据库凭证，修改 Compose 中的密码变量不会自动修改库内密码。

---

## 动态专家引擎 + 数据层评分卡技术方案

对齐商业计划书 3.3 节技术路线：

```
线上评估（生产主路径）：
  动态指标体系（基本项 + 大类→中类→小类→具体营业类型，源文件 3023 条指标按经营加载）
    → 逐指标评分（数值分箱 / 枚举档位 / 布尔映射）
    → 层级基础权重 + 星级归一 + 特色/区域加成
    → 加权合成 0-1000 分，业务阈值分级：≥700 低风险 / 500-700 中等 / <500 高风险
    → 一票否决红线（失信被执行人等）命中时给出高风险/拒贷建议
    → 存在可用数据层模型、至少 2 项已知特征且触发风险分位条件时混合调整
    → 混合经营按占比加权 + 协同因子（种养结合/产销一体）

数据层评分卡（管理端可训练）：
  融合调查样本（优先）或 15 项合成指标样本（兜底）
    → 训练/测试划分 → 仅训练集 SMOTE → WOE 分箱 → IV / VIF 筛选
    → Logistic 回归（class_weight）→ 0-1000 分评分卡
    → 模型监控：PSI 群体稳定性 / 客群迁移预警
```

模型验证：AUC / KS / 混淆矩阵 / 召回率 / 5 折交叉验证 / PSI，训练指标在管理平台可视化展示（ROC 曲线、KS 曲线、IV 条形图、混淆矩阵热力图）。

小样本与极端场景应对（对齐商业计划书 3.3.1）：
- **SMOTE 过采样**：对违约样本合成扩增（仅训练集，避免数据泄漏），缓解违约率 3%-5% 的样本不平衡
- **一票否决红线**：失信被执行人、重大税收违法、连三累六等红线指标，命中时给出高风险/拒贷建议并提示人工复核
- **模型监控**：PSI 群体稳定性监控（训练分布 vs 实际客群），样本不足或显著偏移时触发再校准预警
- **三组对比实验**：替代数据 vs 传统信用数据、原始变量/WOE/分组PCA 特征方案、涉农专属 vs 通用模型

管理端 `POST /model/train` 优先读取 `SAMPLE_DIR/fused_samples_v1.csv`，使用 CHFS / CMES / CFPS 融合调查特征训练数据层评分卡；融合样本缺失或有效特征不足时，回退 `app/ml/seed.py` 的 15 项合成样本。融合特征是真实调查值，A 训练标签仍为合成标签，不能将对应 AUC 当成真实逾期预测准确率。当前离线管道与历史实验见 [scripts/fusion/README.md](scripts/fusion/README.md)。

启动自动训练与 `python scripts/train_model.py` 直接走合成样本路径；自动训练仅在 `AUTO_TRAIN_ON_STARTUP=true` 且无可加载的激活模型时执行。Docker 构建不包含本机模型、样本或调查数据，需恢复到持久卷，管理端训练才能使用融合数据。线上混合调整按最新注册且 `metrics_json.model_type=data_layer` 的模型选择，不要求该模型状态为 `active`。

真实结果回填提供 `pending / normal / overdue / rejected` 四种状态。概率校准仅使用正常还款和逾期样本，样本至少 50 条且两类各至少 5 条时尝试 Platt 校准；缓存最长 5 分钟，条件不足或拟合失败时使用默认概率映射。回填本身不会改写已保存的评估快照。

---

## 后台管理平台功能

| 模块 | 功能 |
|---|---|
| 系统概览 | 统计卡片（用户/记录/模型/API调用）、服务健康、评估趋势、评分分布、行业分布 |
| 数据管理 | 评估记录（搜索/筛选/详情/真实结果回填/删除）、回测与数据层触发统计、用户管理（CRUD/重置密码/角色）、指标配置、系统配置（阈值/利率） |
| 模型管理 | 训练评分卡（融合样本优先；合成样本路径可选样本量）、指标看板（AUC/KS/混淆矩阵/ROC/KS曲线/IV图/5折CV）、合成样本路径的三组对比实验（替代数据vs传统/WOE vs 分组PCA/涉农专属vs通用）、模型版本历史 |
| 模型监控 | PSI 群体稳定性、实际客群评分分布、客群迁移预警、模型再校准触发提示 |
| API 管理 | 接口列表（OpenAPI 聚合）、接口日志（耗时/状态/请求响应）、接口测试控制台 |
| 系统监控 | 服务器状态（CPU/内存/磁盘实时曲线）、数据库状态（表/容量）、健康检查探针 |

---

## API 一览（前缀 `/api/v1`）

| 模块 | 接口 | 说明 | 鉴权 |
|---|---|---|---|
| 认证 | `POST /auth/login` / `GET /auth/me` | 登录 / 当前用户 | 公开 / 需登录 |
| 风险评估 | `POST /risk/assess-dynamic` | 动态指标体系评估（专家引擎，支持混合经营） | 公开 |
| 评估记录 | `GET /risk/records` `GET/DELETE /risk/records/{record_id}` | 本人记录管理 | 需登录 |
| 数据看板 | `GET /dashboard/stats` `GET /dashboard/industry-distribution` `GET /dashboard/score-distribution` `GET /dashboard/trend` | 统计 | 公开 |
| 行为验证码 | `GET /captcha` `POST /captcha/check` | 获取 / 验证行为验证码 | 公开 |
| 真实结果回填 | `PUT /risk/records/{record_id}/outcome` | 回填正常还款 / 逾期 / 未放款 / 待回填 | 本人记录；管理员可回填所有记录 |
| 真实回测 | `GET /risk/records/backtest/stats` | 回填结果与风险等级交叉统计 | 管理员 |
| 数据层监控 | `GET /risk/records/data-layer-stats` | 混合触发率与数据层评分统计 | 管理员 |
| 指标体系 | `GET /indicators/tree` | 指标树（基本项 + 大类→中类→小类→具体营业类型） | 公开 |
| 管理看板 | `GET /admin/dashboard` | 评估解释、账号行为、真实引擎调用与 API 请求统计 | 管理员 |
| 模型管理 | `GET /model/info` `POST /model/train` `GET /model/versions` `GET /model/metrics` `GET/PUT /model/thresholds` `GET /model/monitor` | 信息/训练/版本/监控/阈值 | 管理员 |
| 管理平台 | `/admin/stats` `/admin/users` `/admin/api-logs` `/admin/api-spec` `/admin/configs` `/admin/indicators` | 账号、日志、系统与指标配置管理 | 管理员 |
| 系统监控 | `GET /monitor/server` `/monitor/database` `/monitor/health` | 服务器/数据库/健康 | 管理员 |

登录需先获取并完成验证码，再将 `captchaKey` 与用户名、密码一起提交；测试环境可用 `CAPTCHA_ENABLED=false` 跳过。需登录接口使用 `Authorization: Bearer <token>`。评估记录分页、详情和删除均按当前账号归属限制，包括管理员；回填接口单独允许管理员维护其他账号及匿名记录。公开评估接口有有效 token 时记录账号归属，否则保存为匿名记录。

统一响应格式：`{ "code": 200, "message": "success", "data": ... }`。业务错误及应用异常处理器通常仍返回 HTTP 200，前端还需检查业务 `code`；错误码包括 `401 / 403 / 404 / 422 / 500`，不能只根据 HTTP 状态判断成功。

区域标签、评估解释及管理看板契约见 [docs/admin-dashboard-contract.md](docs/admin-dashboard-contract.md)。即时评估与历史记录保留 `riskAlerts`、`transparency` 和 `engineUsage` 快照；覆盖率衡量数据充分程度，旧记录未跟踪的引擎调用次数不作推测。

新增资料与运维工具索引见 [scripts/README.md](scripts/README.md)；14 列旧版的扫描、改造和核验记录仅供历史追溯，不能替代当前区域配置流程。

---

## 测试

```bash
cd back-end
# 已激活虚拟环境
python -m pip install -r requirements-dev.txt
docker compose up -d mysql
python -m pytest tests -q
```

覆盖：认证与验证码、动态评估、区域元数据、风险提示与数据解释、模型训练、监控和管理接口。测试使用独立的 `race_test` 数据库（`127.0.0.1:3307`），每次运行会重建该测试库；业务库 `race` 不参与测试初始化。

测试自行配置数据库连接、临时模型/样本目录并关闭验证码，不依赖业务 `.env` 中的连接地址。最近一次依赖验证（2026-10-16）：Windows / Python 3.14.7 开发环境 59 项测试通过，保留 3 条 Logistic 收敛警告；Linux / Python 3.13.16 生产依赖独立安装及评分卡烟雾检查通过。

---

## 部署

### 1. 用户前端（fore-end）→ Cloudflare Workers 静态资源

1. 在 `fore-end` 执行 `npm install`、`npm run build`，生成 `dist/`。
2. 仓库 `wrangler.jsonc` 定义静态资源 Worker `race-fore-end`，资源目录为 `dist`；按 Cloudflare 的项目绑定配置部署构建产物。前端使用 Hash 路由。
3. `src/api/index.ts` 默认将自定义域名指向 `https://api.intellicoretech.cn/api/v1`，将 `*.workers.dev` 预览指向 `https://api.intellicoretech.cn/dev/api/v1`。需要覆盖时，在构建前设置 `VITE_API_BASE_URL`。

前端部署与 API 网关配置详见同级前台 [README](../fore-end/README.md)；网关源码存档在 `fore-end/cloudflare/intellicore-worker/`。

### 2. 后端 + 管理端 → 服务器 Docker

```bash
cd back-end
docker compose up -d --build
```

- `mysql` 服务：MySQL 8.0，数据卷持久化
- `backend` 服务：多阶段构建（前端构建 + 后端），自动挂载管理端到 `/admin/`；启动初始化和自动训练条件见上文
- `go-captcha-service` 服务：登录行为验证码，后端通过 Docker 内网访问
- 当前仓库 Compose 发布端口为 `18001`，访问 `http://<服务器IP>:18001/admin/`；容器内部端口为 `8000`。服务器如使用覆盖配置，按实际发布端口设置反向代理或隧道

服务器部署步骤：

1. 修改 `.env`：容器连接使用 `mysql:3306`（或容器可达的外部 MySQL），`JWT_SECRET_KEY` 换随机串、`CORS_ORIGINS` 填入前端域名；生产设置 `ENV=production` 关闭 `/docs` 与 `/redoc`
2. `docker compose up -d --build`
3. （可选）配置 Nginx/Caddy 反代 HTTPS，并将域名解析到服务器

Compose 只向容器注入 `backend.environment` 中声明的变量，其他 `.env` 字段不会自动传入。例如更改首次创建的管理员账号/密码，需显式添加 `DEFAULT_ADMIN_USERNAME`、`DEFAULT_ADMIN_PASSWORD` 到该服务的环境配置；已有账号密码通过管理端重置，修改初始化变量不会覆盖已有账号。

**仓库约定的部署域名**：

| 服务 | 地址 |
|---|---|
| 竞赛前端（fore-end） | https://intellicoretech.cn （Cloudflare Workers 静态资源，Hash 路由） |
| 后端 API | https://api.intellicoretech.cn （Cloudflare Worker 网关 → 隧道 → 部署环境的后端端口） |
| 管理平台 | https://backend.intellicoretech.cn/admin |

### 3. CI/CD 自动部署（GitHub Actions）

推送到 `main` 或 `dev` 且命中路径过滤时触发服务器部署，也可通过 `workflow_dispatch` 手动选择分支：

```mermaid
graph LR
  A[本地 git push] --> B[GitHub Actions: Deploy to Server]
  B --> C[SSH 受限密钥连服务器]
  C --> D[deploy-backend.sh main 或 dev]
  D --> E[服务器脚本执行对应分支部署]
```

- 触发范围：`app/**`、`scripts/**`、`admin-web/**`、`Dockerfile`、`docker-compose.yml`、`requirements*.txt`、workflow 文件
- workflow 调用 `deploy-backend.sh ${{ github.ref_name }}`；仓库注释约定服务器脚本为 `/root/deploy-backend.sh`，通过受限部署密钥接收 `main/dev` 分支参数。服务器脚本及密钥限制需在服务器单独配置
- GitHub Secrets：`SERVER_HOST` / `SERVER_USER` / `DEPLOY_SSH_KEY`（Settings → Secrets and variables → Actions）
- 根 `README.md` 不在触发范围内，但 `scripts/README.md` 等匹配 `scripts/**` 的文档仍会触发；不能按扩展名判断是否部署
- 当前 Compose 将模型/样本保存在持久卷中；自动训练还取决于是否存在可加载的激活模型，恢复时需保证数据库版本记录与模型产物对应

### 4. 本地自动部署（git post-commit hook）

仓库提供可选的本地提交后部署钩子：启用后，每次提交若涉及匹配路径，会尝试重建 backend 容器。

**启用前提**：脚本固定读取 `docker-compose.yml` 和 `docker-compose.local.yml`；当前检出没有后一个文件，且它被 Git 忽略。需先提供本机覆盖文件或调整钩子的 Compose 参数，再启用此功能。普通手动启动使用前文的单文件 Compose 命令。

- 钩子脚本：`scripts/git-hooks/post-commit`（随仓库分发）
- 启用（一次性配置，指向仓库内 hooks 目录）：

  ```bash
  cd back-end
  git config core.hooksPath scripts/git-hooks
  ```

- 停用：

  ```bash
  git config --unset core.hooksPath
  ```

- 匹配 `app/`、`scripts/`、`admin-web/`、`Dockerfile`、`docker-compose*`、`requirements*` 和部署 workflow；其中 `scripts/` 下文档同样匹配，且本地 `docker-compose*` 范围比云端单个 `docker-compose.yml` 更宽
- 执行内容：`docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build backend`
- 说明：hook 只在**本机**生效（`core.hooksPath` 为本地 git 配置），提交不会把"已启用状态"同步给其他开发者，克隆仓库后需各自执行上面的启用命令

---

## 代码风格与质量检查

全项目统一代码风格，提交前请执行：

**后端（Python，ruff 0.16+）**

```bash
cd back-end
# 已激活虚拟环境并安装 requirements-dev.txt
python -m ruff check app scripts tests
python -m ruff format --check app scripts tests
python -m pytest tests -q
```

需要自动格式化时执行 `python -m ruff format app scripts tests`。Ruff 缓存位于 `back-end/.ruff_cache/`；检查命令在后端目录执行。

**前端（admin-web / fore-end，ESLint + Prettier）**

```bash
cd admin-web        # 或 fore-end
npm run lint        # eslint . --max-warnings=0
npm run format      # prettier --write
npm run build       # vue-tsc + vite 构建验证
```

约定：行宽 120、Python 缩进 4 空格 / 前端 2 空格、UTF-8 + LF、文件末尾换行（见 `.editorconfig`）；两端统一「农业绿」主题（`#2c6e49`）。

---

## 默认账号

| 角色 | 账号 | 密码 |
|---|---|---|
| 管理员 | `admin` | `admin123` |
| 分析师 | `analyst1` | `analyst123`（示例，由管理员在管理端创建） |

> 生产部署前配置随机 `JWT_SECRET_KEY`，并在管理端修改默认管理员密码。首次创建账号可使用 `DEFAULT_ADMIN_*`；本机 Python 从 `.env` 读取，Docker 需按部署一节显式传入。

---

## 版权与致谢

- 评分卡方法参考：`Toad-Dev-Group/toad`、`carlo58/optbinning`、`ShichenXie/scorecardpy`
- 后端结构参考：`tiangolo/full-stack-fastapi-template`
- 管理端结构参考：`PureAdmin`、`SoybeanAdmin` 等开源后台模板思路
