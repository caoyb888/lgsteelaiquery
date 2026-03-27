"""
管理后台 API

GET  /api/v1/admin/stats/accuracy       — SQL 准确率统计（基于用户反馈）
GET  /api/v1/admin/stats/usage          — 使用量统计（查询次数/Token/活跃用户）
GET  /api/v1/admin/audit/logs           — 审计日志查询（分页）
GET  /api/v1/admin/users                — 用户列表
POST /api/v1/admin/users                — 新增用户
PATCH /api/v1/admin/users/{user_id}/role — 修改用户角色
GET  /api/v1/admin/datasource/stale     — 超期未更新数据源列表
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.db.models.audit import AuditLog
from app.db.models.datasource import Datasource
from app.db.models.user import User
from app.dependencies import CurrentUserIdDep, MetaSessionDep
from app.schemas.common import ApiResponse, PaginatedResponse
from app.security.auth import hash_password
from app.security.rbac import RBACChecker
from app.utils.exceptions import AuthorizationError

_rbac = RBACChecker()

router = APIRouter()


def _require_admin(session, user_id: uuid.UUID):
    """快速角色守卫——实际调用时需 await，这里仅做类型提示占位。"""
    pass


async def _assert_admin(session, user_id: uuid.UUID) -> None:
    result = await session.execute(
        select(User.role).where(User.id == user_id, User.is_active.is_(True))
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已被禁用")
    try:
        _rbac.check_can_manage_users(role)
    except AuthorizationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可执行此操作") from exc


# ─── 统计 ────────────────────────────────────────────────────────────────────


@router.get("/stats/accuracy")
async def get_accuracy_stats(
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
    days: int = 30,
) -> ApiResponse[dict]:
    """
    SQL 准确率统计（基于用户点赞 / 点踩反馈）。

    返回：总查询数、有反馈数、点赞数、点踩数、满意度（点赞/有反馈）
    """
    await _assert_admin(session, current_user_id)

    since = datetime.now(tz=timezone.utc) - timedelta(days=days)
    result = await session.execute(
        select(
            func.count(AuditLog.id).label("total"),
            func.count(AuditLog.feedback).label("with_feedback"),
            func.sum((AuditLog.feedback == 1).cast(int)).label("thumbs_up"),
            func.sum((AuditLog.feedback == -1).cast(int)).label("thumbs_down"),
            func.count(AuditLog.id).filter(AuditLog.status == "success").label("success_count"),
            func.count(AuditLog.id).filter(AuditLog.status == "failed").label("failed_count"),
            func.count(AuditLog.id).filter(AuditLog.status == "blocked").label("blocked_count"),
        ).where(AuditLog.created_at >= since)
    )
    row = result.one()
    total = row.total or 0
    with_feedback = row.with_feedback or 0
    thumbs_up = int(row.thumbs_up or 0)
    satisfaction = round(thumbs_up / with_feedback * 100, 1) if with_feedback > 0 else None
    sql_success_rate = round(row.success_count / total * 100, 1) if total > 0 else None

    return ApiResponse.ok(
        data={
            "period_days": days,
            "total_queries": total,
            "success_count": row.success_count or 0,
            "failed_count": row.failed_count or 0,
            "blocked_count": row.blocked_count or 0,
            "sql_success_rate_pct": sql_success_rate,
            "with_feedback": with_feedback,
            "thumbs_up": thumbs_up,
            "thumbs_down": int(row.thumbs_down or 0),
            "satisfaction_pct": satisfaction,
        }
    )


@router.get("/stats/usage")
async def get_usage_stats(
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
    days: int = 7,
) -> ApiResponse[dict]:
    """使用量统计：查询次数、Token 消耗、活跃用户数"""
    await _assert_admin(session, current_user_id)

    since = datetime.now(tz=timezone.utc) - timedelta(days=days)
    result = await session.execute(
        select(
            func.count(AuditLog.id).label("total_queries"),
            func.count(func.distinct(AuditLog.user_id)).label("active_users"),
            func.sum(AuditLog.prompt_tokens).label("prompt_tokens"),
            func.sum(AuditLog.completion_tokens).label("completion_tokens"),
            func.avg(AuditLog.execution_ms).label("avg_ms"),
        ).where(AuditLog.created_at >= since)
    )
    row = result.one()
    return ApiResponse.ok(
        data={
            "period_days": days,
            "total_queries": row.total_queries or 0,
            "active_users": row.active_users or 0,
            "total_prompt_tokens": int(row.prompt_tokens or 0),
            "total_completion_tokens": int(row.completion_tokens or 0),
            "avg_execution_ms": round(float(row.avg_ms or 0), 1),
        }
    )


# ─── 审计日志 ─────────────────────────────────────────────────────────────────


@router.get("/audit/logs")
async def get_audit_logs(
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
    page: int = 1,
    page_size: int = 20,
    log_status: str | None = None,
    user_id: str | None = None,
) -> ApiResponse[dict]:
    """审计日志查询（支持分页与过滤）"""
    await _assert_admin(session, current_user_id)

    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    count_stmt = select(func.count(AuditLog.id))

    if log_status:
        stmt = stmt.where(AuditLog.status == log_status)
        count_stmt = count_stmt.where(AuditLog.status == log_status)
    if user_id:
        try:
            uid = uuid.UUID(user_id)
            stmt = stmt.where(AuditLog.user_id == uid)
            count_stmt = count_stmt.where(AuditLog.user_id == uid)
        except ValueError:
            pass

    total_result = await session.execute(count_stmt)
    total = total_result.scalar_one()

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    results = await session.execute(stmt)
    logs = results.scalars().all()

    items = [
        {
            "id": str(log.id),
            "user_id": str(log.user_id),
            "question": log.question,
            "generated_sql": log.generated_sql,
            "status": log.status,
            "block_reason": log.block_reason,
            "result_row_count": log.result_row_count,
            "execution_ms": log.execution_ms,
            "feedback": log.feedback,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]

    import math
    return ApiResponse.ok(
        data={
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": math.ceil(total / page_size) if page_size else 1,
        }
    )


# ─── 用户管理 ─────────────────────────────────────────────────────────────────


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=6, max_length=128)
    role: str = Field(..., description="用户角色")
    email: str | None = None


class UpdateRoleRequest(BaseModel):
    role: str = Field(..., description="新角色")


@router.get("/users")
async def list_users(
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
) -> ApiResponse[list]:
    """用户列表"""
    await _assert_admin(session, current_user_id)

    results = await session.execute(select(User).order_by(User.created_at.desc()))
    users = results.scalars().all()
    return ApiResponse.ok(
        data=[
            {
                "id": str(u.id),
                "username": u.username,
                "display_name": u.display_name,
                "email": u.email,
                "role": u.role,
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat(),
            }
            for u in users
        ]
    )


@router.post("/users")
async def create_user(
    request: CreateUserRequest,
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
) -> ApiResponse[dict]:
    """新增用户"""
    await _assert_admin(session, current_user_id)

    # 检查用户名唯一性
    existing = await session.execute(select(User.id).where(User.username == request.username))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"用户名 '{request.username}' 已存在",
        )

    new_user = User(
        username=request.username,
        display_name=request.display_name,
        email=request.email,
        password_hash=hash_password(request.password),
        role=request.role,
        is_active=True,
    )
    session.add(new_user)
    await session.commit()
    await session.refresh(new_user)

    logger.info("新用户创建", username=request.username, role=request.role, operator=str(current_user_id))
    return ApiResponse.ok(
        data={
            "id": str(new_user.id),
            "username": new_user.username,
            "display_name": new_user.display_name,
            "role": new_user.role,
        },
        message="用户创建成功",
    )


@router.patch("/users/{user_id}/role")
async def update_user_role(
    user_id: str,
    request: UpdateRoleRequest,
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
) -> ApiResponse[None]:
    """修改用户角色"""
    await _assert_admin(session, current_user_id)

    try:
        uid = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id 格式错误") from exc

    result = await session.execute(select(User).where(User.id == uid))
    user: User | None = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    await session.execute(
        update(User).where(User.id == uid).values(role=request.role)
    )
    await session.commit()
    logger.info("用户角色已修改", user_id=user_id, new_role=request.role, operator=str(current_user_id))
    return ApiResponse.ok(data=None, message="角色修改成功")


# ─── 数据时效预警 ─────────────────────────────────────────────────────────────


@router.get("/datasource/stale")
async def get_stale_datasources(
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
) -> ApiResponse[list]:
    """获取超期未更新（超过配置天数）的活跃数据源列表"""
    from app.config import get_settings

    settings = get_settings()
    threshold = datetime.now(tz=timezone.utc) - timedelta(days=settings.datasource_stale_days)

    results = await session.execute(
        select(Datasource).where(
            Datasource.status == "active",
            Datasource.updated_at <= threshold,
        )
    )
    datasources = results.scalars().all()
    return ApiResponse.ok(
        data=[
            {
                "id": str(ds.id),
                "name": ds.name,
                "domain": ds.domain,
                "data_date": str(ds.data_date),
                "updated_at": ds.updated_at.isoformat(),
                "stale_days": (datetime.now(tz=timezone.utc) - ds.updated_at.replace(tzinfo=timezone.utc)).days,
            }
            for ds in datasources
        ]
    )
