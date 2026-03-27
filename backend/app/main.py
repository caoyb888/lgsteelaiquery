"""
FastAPI 应用入口

启动命令（开发）：
    uvicorn app.main:app --reload --port 8000

启动命令（生产）：
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.config import get_settings
from app.utils.exceptions import AIQueryBaseException

settings = get_settings()


# ---- 日志配置 ----

import sys
logger.remove()
logger.add(
    sys.stdout,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
        "{message} | {extra}"
    ),
    level=settings.app_log_level,
    serialize=settings.app_env == "production",  # 生产环境输出 JSON
)


# ---- 应用生命周期 ----

@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """启动/关闭钩子"""
    logger.info("莱钢 AI 问数后端服务启动", env=settings.app_env)
    yield
    logger.info("莱钢 AI 问数后端服务关闭")


# ---- FastAPI 应用实例 ----

app = FastAPI(
    title="莱钢集团 AI 问数",
    description="基于自然语言的企业数据查询平台 API",
    version="1.0.0",
    docs_url="/docs" if settings.app_env == "development" else None,  # 生产关闭 Swagger
    redoc_url="/redoc" if settings.app_env == "development" else None,
    lifespan=lifespan,
)


# ---- CORS 中间件（仅允许内网域名）----

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allow_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


# ---- Request ID 注入中间件 ----

@app.middleware("http")
async def add_request_id(request: Request, call_next: Any) -> Any:
    """为每个请求注入唯一 request_id，用于日志追踪"""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    with logger.contextualize(request_id=request_id):
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ---- 统一异常处理 ----

@app.exception_handler(AIQueryBaseException)
async def business_exception_handler(
    request: Request, exc: AIQueryBaseException
) -> JSONResponse:
    """将业务异常映射为标准响应格式"""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    logger.warning(
        "业务异常",
        code=exc.code,
        message=exc.message,
        path=str(request.url),
    )
    return JSONResponse(
        status_code=200,  # 业务异常统一返回 200，错误信息在 code 字段
        content={
            "code": exc.code,
            "message": exc.message,
            "data": None,
            "request_id": request_id,
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """未处理异常兜底"""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    logger.exception("未处理异常", path=str(request.url))
    return JSONResponse(
        status_code=500,
        content={
            "code": 5000,
            "message": "系统内部错误，请联系管理员",
            "data": None,
            "request_id": request_id,
        },
    )


# ---- 健康检查 ----

@app.get("/health", tags=["系统"])
async def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "lgsteel-ai-query"}


# ---- 路由注册 ----
from app.api.v1 import admin, auth, chat, datasource

app.include_router(auth.router, prefix="/api/v1/auth", tags=["认证"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["对话查询"])
app.include_router(datasource.router, prefix="/api/v1/datasource", tags=["数据源管理"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["管理后台"])
