"""
数据源管理 API

POST   /api/v1/datasource/upload             — Excel 上传（解析预览）
POST   /api/v1/datasource/confirm/{upload_id} — 确认字段映射，触发 Celery 入库
GET    /api/v1/datasource/list               — 数据源列表
GET    /api/v1/datasource/{datasource_id}    — 数据源详情
DELETE /api/v1/datasource/{datasource_id}    — 逻辑删除数据源
"""
from __future__ import annotations

import os
import shutil
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, UploadFile, status
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.excel_parser import ExcelParser
from app.core.field_mapper import FieldMapper
from app.db.models.datasource import Datasource, FieldMapping
from app.dependencies import CurrentUserIdDep, MetaSessionDep
from app.schemas.common import ApiResponse
from app.schemas.datasource import (
    DatasourceConfirmRequest,
    DatasourceListItem,
    DatasourceUploadResponse,
    FieldMappingPreview,
)
from app.security.rbac import RBACChecker
from app.utils.exceptions import AuthorizationError

_rbac = RBACChecker()
router = APIRouter()
settings = get_settings()

_SUPPORTED_EXTS = {".xlsx", ".xls", ".csv"}


def _to_list_item(ds: Datasource) -> DatasourceListItem:
    stale_threshold = settings.datasource_stale_days
    now = datetime.now(tz=timezone.utc)
    age_days = (now - ds.updated_at.replace(tzinfo=timezone.utc)).days
    is_stale = age_days > stale_threshold and ds.status == "active"
    return DatasourceListItem(
        id=str(ds.id),
        name=ds.name,
        domain=ds.domain,
        description=ds.description,
        original_filename=ds.original_filename,
        data_date=ds.data_date,
        status=ds.status,
        total_rows=ds.total_rows,
        uploaded_by_name=(
            ds.uploaded_by_user.display_name if ds.uploaded_by_user else None
        ),
        created_at=ds.created_at,
        is_stale=is_stale,
    )


@router.post("/upload", response_model=ApiResponse[DatasourceUploadResponse])
async def upload_datasource(
    file: UploadFile,
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
    domain: str = Form(default="unknown"),
    data_date: str = Form(default=""),
    update_mode: str = Form(default="replace"),
) -> ApiResponse[DatasourceUploadResponse]:
    """
    上传 Excel 文件并返回字段映射预览。

    1. 校验文件格式和大小
    2. 获取上传用户角色，检查上传权限
    3. 保存文件到 excel_upload_dir
    4. 同步解析 Excel（轻量预览，不入库）
    5. 调用 FieldMapper 自动映射字段
    6. 将 Datasource 记录（status=pending_confirm）写入元数据库
    7. 返回 upload_id + 字段映射预览
    """
    from app.db.models.user import User

    # 校验格式
    filename = file.filename or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in _SUPPORTED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件类型 {ext}，支持: {', '.join(_SUPPORTED_EXTS)}",
        )

    # 读取内容检查大小
    content = await file.read()
    if len(content) > settings.excel_max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件超过 {settings.excel_max_size_mb}MB 限制",
        )

    # 检查上传权限（需要查角色）
    user_result = await session.execute(
        select(User.role).where(User.id == current_user_id)
    )
    user_role: str | None = user_result.scalar_one_or_none()
    if user_role is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    try:
        _rbac.check_can_upload(user_role)
    except AuthorizationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="您没有上传数据源的权限") from exc

    # 保存文件
    upload_dir = Path(settings.excel_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid.uuid4())
    saved_path = upload_dir / f"{upload_id}{ext}"
    saved_path.write_bytes(content)

    # 解析 Excel 获取字段预览（parse 为同步方法，用线程池避免阻塞事件循环）
    import asyncio
    parser = ExcelParser()
    try:
        loop = asyncio.get_event_loop()
        parse_result = await loop.run_in_executor(None, parser.parse, str(saved_path), filename)
    except Exception as exc:
        saved_path.unlink(missing_ok=True)
        logger.error("Excel 解析失败", filename=filename, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Excel 解析失败：{exc}",
        ) from exc

    # 使用前端传入的 domain（若为空则从文件名猜测）
    if not domain or domain == "unknown":
        domain = "unknown"
        for d in ("finance", "sales", "production", "procurement"):
            if d in filename.lower():
                domain = d
                break

    mapper = FieldMapper(dictionary_manager=None, llm_router=None)
    candidates = await mapper.map_fields(parse_result, domain)

    # 建立 raw_name -> ParsedField 的快速查找表
    parsed_field_map = {f.raw_name: f for f in parse_result.fields}

    field_previews: list[FieldMappingPreview] = [
        FieldMappingPreview(
            raw_name=c.raw_name,
            std_name=c.std_name,
            display_name=c.display_name,
            field_type=parsed_field_map[c.raw_name].inferred_type if c.raw_name in parsed_field_map else "text",
            unit=parsed_field_map[c.raw_name].unit if c.raw_name in parsed_field_map else None,
            confidence=c.confidence,
            needs_confirm=c.confidence < settings.field_mapping_confirm_threshold,
            mapping_source=c.mapping_source,
        )
        for c in candidates
    ]

    # 写入元数据库（pending_confirm 状态）
    ds = Datasource(
        id=uuid.UUID(upload_id),
        name=filename,
        domain=domain,
        original_filename=filename,
        file_path=str(saved_path),
        file_size_bytes=len(content),
        data_date=date.fromisoformat(data_date) if data_date else date.today(),
        update_mode=update_mode,
        status="pending_confirm",
        uploaded_by=current_user_id,
    )
    session.add(ds)

    # 同时写入字段映射记录
    for p in field_previews:
        fm = FieldMapping(
            datasource_id=uuid.UUID(upload_id),
            raw_name=p.raw_name,
            std_name=p.std_name,
            display_name=p.display_name,
            field_type=p.field_type,
            unit=p.unit,
            confidence=p.confidence,
            mapping_source=p.mapping_source,
        )
        session.add(fm)

    await session.commit()

    logger.info(
        "Excel 文件上传成功",
        upload_id=upload_id,
        filename=filename,
        fields=len(parse_result.fields),
        domain=domain,
    )

    return ApiResponse.ok(
        data=DatasourceUploadResponse(
            upload_id=upload_id,
            status="pending_confirm",
            preview={
                "total_rows": parse_result.total_rows,
                "sheet_name": parse_result.sheet_name,
                "domain": domain,
                "field_mappings": [p.model_dump() for p in field_previews],
            },
        )
    )


@router.post("/confirm/{upload_id}", response_model=ApiResponse[None])
async def confirm_field_mappings(
    upload_id: str,
    request: DatasourceConfirmRequest,
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
) -> ApiResponse[None]:
    """
    确认字段映射，触发 Celery 数据入库任务。

    request.confirmed_mappings 包含有修改的字段；
    未修改的字段沿用解析时自动映射结果。
    """
    try:
        ds_id = uuid.UUID(upload_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="upload_id 格式错误") from exc

    result = await session.execute(
        select(Datasource)
        .options(selectinload(Datasource.field_mappings))
        .where(
            Datasource.id == ds_id,
            Datasource.status == "pending_confirm",
        )
    )
    ds: Datasource | None = result.scalar_one_or_none()
    if ds is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="数据源不存在或状态不允许确认",
        )

    # 保存人工确认的字段映射
    overrides = {m.raw_name: m for m in request.confirmed_mappings}
    for fm in ds.field_mappings:
        if fm.raw_name in overrides:
            override = overrides[fm.raw_name]
            fm.std_name = override.std_name
            fm.display_name = override.display_name
            fm.field_type = override.field_type
            fm.unit = override.unit
            fm.confirmed_by = current_user_id
            fm.mapping_source = "manual"

    ds.status = "processing"
    await session.commit()

    # 触发 Celery 入库任务
    from app.worker import parse_excel_task
    parse_excel_task.delay(
        upload_id=upload_id,
        file_path=ds.file_path,
        domain=ds.domain,
        update_mode=ds.update_mode or "replace",
        user_id=str(current_user_id),
    )

    logger.info("数据入库任务已触发", upload_id=upload_id, domain=ds.domain)
    return ApiResponse.ok(data=None, message="数据处理任务已提交")


@router.get("/list", response_model=ApiResponse[list[DatasourceListItem]])
async def list_datasources(
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
    domain: str | None = None,
) -> ApiResponse[list[DatasourceListItem]]:
    """获取当前用户有权限访问的数据源列表"""
    from app.db.models.user import User

    user_result = await session.execute(
        select(User.role).where(User.id == current_user_id)
    )
    user_role = user_result.scalar_one_or_none() or "analyst"
    allowed = _rbac.get_allowed_domains(user_role)

    stmt = (
        select(Datasource)
        .options(selectinload(Datasource.uploaded_by_user))
        .where(
            Datasource.status != "archived",
            Datasource.domain.in_(allowed) if allowed else False,
        )
    )
    if domain:
        stmt = stmt.where(Datasource.domain == domain)

    results = await session.execute(stmt)
    datasources = results.scalars().all()
    return ApiResponse.ok(data=[_to_list_item(ds) for ds in datasources])


@router.get("/{datasource_id}", response_model=ApiResponse[DatasourceListItem])
async def get_datasource(
    datasource_id: str,
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
) -> ApiResponse[DatasourceListItem]:
    """获取数据源详情"""
    try:
        ds_id = uuid.UUID(datasource_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="datasource_id 格式错误") from exc

    result = await session.execute(
        select(Datasource)
        .options(selectinload(Datasource.uploaded_by_user))
        .where(Datasource.id == ds_id, Datasource.status != "archived")
    )
    ds = result.scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据源不存在")

    return ApiResponse.ok(data=_to_list_item(ds))


@router.delete("/{datasource_id}", response_model=ApiResponse[None])
async def delete_datasource(
    datasource_id: str,
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
) -> ApiResponse[None]:
    """逻辑删除数据源（status 置为 archived）"""
    from app.db.models.user import User

    # 检查权限（只有可以上传的角色才能删除）
    user_result = await session.execute(
        select(User.role).where(User.id == current_user_id)
    )
    user_role = user_result.scalar_one_or_none() or ""
    try:
        _rbac.check_can_upload(user_role)
    except AuthorizationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无删除权限") from exc

    try:
        ds_id = uuid.UUID(datasource_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="datasource_id 格式错误") from exc

    await session.execute(
        update(Datasource)
        .where(Datasource.id == ds_id)
        .values(status="archived")
    )
    await session.commit()
    logger.info("数据源已归档", datasource_id=datasource_id, operator=str(current_user_id))
    return ApiResponse.ok(data=None, message="数据源已删除")
