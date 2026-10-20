"""FastAPI 应用入口

启动时自动：初始化数据库（建表 + 默认管理员 + 默认配置）→ 若无模型则自动训练评分卡。
生产模式下若存在 admin-web/dist 构建产物，自动挂载到 /admin。
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.response import ApiResponse
from app.db.init_db import full_init
from app.db.session import SessionLocal
from app.middleware.request_log import RequestLogMiddleware
from app.ml.training import load_active_model, run_training
from app.services.ai_review_worker import running_review_workers


class ReviewGZipMiddleware(GZipMiddleware):
    """显式绕过复核 SSE，不依赖部署的 Starlette 版本是否默认排除事件流。"""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").endswith("/review/stream"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---------- 启动初始化 ----------
    try:
        full_init()
    except Exception as e:  # noqa: BLE001
        print(f"[init] 数据库初始化失败：{e}")

    # 若无已训练模型，自动训练
    if settings.AUTO_TRAIN_ON_STARTUP:
        db: Session = SessionLocal()
        try:
            model = load_active_model(db)
            if model is None:
                print("[init] 未检测到已训练模型，开始自动训练...")
                result = run_training(db=db, trained_by="system")
                print(
                    f"[init] 自动训练完成 v{result['version']} "
                    f"AUC={result['auc']} KS={result['ks']} 召回率={result['recall']}"
                )
            else:
                print(f"[init] 已加载模型 {model.version}")
        finally:
            db.close()

    async with running_review_workers():
        yield
    print("[shutdown] 服务关闭")


def create_app() -> FastAPI:
    is_prod = settings.ENV == "production"
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="基于多元统计模型的涉农小微企业信贷风险智能评估系统 - 后端管理平台",
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
    )

    # ---------- CORS ----------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # ---------- gzip 压缩（指标树等大响应 1.7MB → ~300KB，大幅降低 CDN/隧道传输耗时）----------
    app.add_middleware(ReviewGZipMiddleware, minimum_size=1000)
    app.add_middleware(RequestLogMiddleware)

    # ---------- 安全响应头（防点击劫持 / MIME 嗅探 / XSS 基础防护）----------
    @app.middleware("http")
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")
        return response

    # ---------- 异常处理 ----------
    register_exception_handlers(app)

    # ---------- 路由 ----------
    app.include_router(api_router, prefix=settings.API_PREFIX)

    # ---------- 管理端静态资源（生产构建后自动挂载）----------
    admin_dist = Path(__file__).resolve().parent.parent / "admin-web" / "dist"
    if admin_dist.exists():
        app.mount("/admin", StaticFiles(directory=str(admin_dist), html=True), name="admin")

    @app.get("/", tags=["系统"])
    def root():
        return ApiResponse(
            data={
                "name": settings.APP_NAME,
                "version": settings.APP_VERSION,
                "docs": "/docs",
                "admin": "/admin" if admin_dist.exists() else None,
                "apiPrefix": settings.API_PREFIX,
            }
        )

    return app


app = create_app()
