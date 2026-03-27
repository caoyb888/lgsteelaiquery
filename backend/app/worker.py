"""
Celery 应用入口

任务类型：
- Excel 文件异步解析入库（parse_excel_task）
- 向量化新增数据字典条目（embed_dictionary_task）

启动命令（容器内）：
    celery -A app.worker worker --loglevel=info --concurrency=4
"""
from __future__ import annotations

import asyncio
import uuid

from celery import Celery
from loguru import logger

from app.config import get_settings

settings = get_settings()

# 创建 Celery 应用，使用 Redis 作为 broker 和 backend
celery_app = Celery(
    "lgsteel_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,                    # 任务执行完后才确认，防止丢失
    worker_prefetch_multiplier=1,           # 每次只取一个任务，避免长任务占用
    task_routes={
        "app.worker.parse_excel_task": {"queue": "excel"},
        "app.worker.embed_dictionary_task": {"queue": "embed"},
    },
)


@celery_app.task(name="app.worker.parse_excel_task", bind=True, max_retries=3)  # type: ignore[misc]
def parse_excel_task(
    self,  # noqa: ANN001
    upload_id: str,
    file_path: str,
    domain: str,
    update_mode: str,
    user_id: str,
) -> dict[str, object]:
    """
    异步解析 Excel 文件并入库。

    由 datasource/confirm API 触发，在用户确认字段映射后执行。

    步骤：
    1. 解析 Excel 文件
    2. 数据清洗，写入业务数据库
    3. 更新元数据库中 Datasource 状态为 active / error
    4. 触发向量化任务
    """
    logger.info(
        "开始处理 Excel 解析任务",
        upload_id=upload_id,
        file_path=file_path,
        domain=domain,
    )

    try:
        result = asyncio.run(_async_parse_excel(upload_id, file_path, domain, update_mode, user_id))
        return result
    except Exception as exc:
        logger.error(
            "Excel 解析任务失败",
            upload_id=upload_id,
            error=str(exc),
            retry_count=self.request.retries,
        )
        # 更新状态为 error
        try:
            asyncio.run(_update_datasource_status(upload_id, "error"))
        except Exception:
            pass
        # 指数退避重试
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)


async def _async_parse_excel(
    upload_id: str,
    file_path: str,
    domain: str,
    update_mode: str,
    user_id: str,
) -> dict[str, object]:
    """异步执行 Excel 解析和入库的核心逻辑"""
    from app.core.data_cleaner import DataCleaner
    from app.core.excel_parser import ExcelParser
    from app.db.meta_session import _MetaSessionFactory

    parser = ExcelParser()
    parse_result = await parser.parse(file_path, file_path.split("/")[-1])

    cleaner = DataCleaner()
    load_result = await cleaner.clean_and_load(
        parse_result=parse_result,
        datasource_id=upload_id,
        domain=domain,
        update_mode=update_mode,
    )

    # 更新元数据库
    async with _MetaSessionFactory() as session:
        from sqlalchemy import update as sql_update
        from app.db.models.datasource import Datasource

        await session.execute(
            sql_update(Datasource)
            .where(Datasource.id == uuid.UUID(upload_id))
            .values(
                status="active",
                biz_table_name=load_result.table_name,
                total_rows=load_result.loaded_rows,
            )
        )
        await session.commit()

    logger.info(
        "Excel 解析任务完成",
        upload_id=upload_id,
        table_name=load_result.table_name,
        loaded_rows=load_result.loaded_rows,
    )

    # 触发向量化任务（异步，不等待）
    field_names = [f.display_name or f.raw_name for f in parse_result.fields]
    embed_dictionary_task.delay(field_names=field_names, domain=domain)

    return {
        "status": "success",
        "upload_id": upload_id,
        "table_name": load_result.table_name,
        "loaded_rows": load_result.loaded_rows,
    }


async def _update_datasource_status(upload_id: str, new_status: str) -> None:
    from app.db.meta_session import _MetaSessionFactory
    from app.db.models.datasource import Datasource
    from sqlalchemy import update as sql_update

    async with _MetaSessionFactory() as session:
        await session.execute(
            sql_update(Datasource)
            .where(Datasource.id == uuid.UUID(upload_id))
            .values(status=new_status)
        )
        await session.commit()


@celery_app.task(name="app.worker.embed_dictionary_task", bind=True, max_retries=3)  # type: ignore[misc]
def embed_dictionary_task(
    self,  # noqa: ANN001
    field_names: list[str],
    domain: str,
) -> dict[str, object]:
    """
    异步将新字段向量化并写入 ChromaDB。

    在 parse_excel_task 完成后触发，确保新字段可被语义检索到。
    """
    logger.info("开始向量化数据字典条目", field_count=len(field_names), domain=domain)

    try:
        result = asyncio.run(_async_embed_fields(field_names, domain))
        return result
    except Exception as exc:
        logger.error(
            "向量化任务失败",
            domain=domain,
            error=str(exc),
            retry_count=self.request.retries,
        )
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)


async def _async_embed_fields(field_names: list[str], domain: str) -> dict[str, object]:
    """异步将字段名向量化写入 ChromaDB"""
    import redis.asyncio as aioredis

    from app.knowledge.dictionary import DataDictionaryManager
    from app.knowledge.embedding import EmbeddingService

    redis_client = aioredis.Redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=False,
    )
    try:
        embed_service = EmbeddingService(redis_client=redis_client)
        dict_manager = DataDictionaryManager(
            redis_client=redis_client,
            embedding_service=embed_service,
        )
        for name in field_names:
            await dict_manager.upsert_field(
                std_name=name,
                display_name=name,
                domain=domain,
                description=name,
                synonyms=[],
                unit=None,
            )
        logger.info("向量化完成", field_count=len(field_names), domain=domain)
        return {"status": "success", "field_count": len(field_names)}
    finally:
        await redis_client.aclose()
