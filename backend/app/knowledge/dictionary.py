"""
数据字典管理器

管理业务数据字典和 Few-shot 示例：
- 字段检索：将用户问题向量化后在 ChromaDB 中做 top-k 语义搜索
- Few-shot 检索：同样语义搜索，限定 domain
- 支持新字段写入 ChromaDB（由 field_mapper 调用）
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models.knowledge import DataDictionary, FewShotExample
from app.knowledge.embedding import EmbeddingService
from app.utils.exceptions import AIQueryBaseException

# ChromaDB collection 名称
_COLLECTION_FIELDS = "lgsteel_fields"
_COLLECTION_FEW_SHOTS = "lgsteel_few_shots"

# knowledge-base/schemas/ 相对项目根目录路径
# 本文件位于 backend/app/knowledge/dictionary.py，
# 项目根目录为 backend/app/knowledge/../../.. 即 lgsteel-ai-query/
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_SCHEMA_DIR = _PROJECT_ROOT / "knowledge-base" / "schemas"


@dataclass
class SchemaContext:
    """返回给 PromptBuilder 使用的上下文"""

    matched_fields: list[dict[str, Any]] = field(default_factory=list)
    few_shot_examples: list[dict[str, Any]] = field(default_factory=list)
    domain_schema_yaml: str = ""


class DataDictionaryManager:
    """
    管理业务数据字典和 Few-shot 示例。
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        chroma_client: Any,
        meta_session_factory: Any,
        settings: Settings | None = None,
    ) -> None:
        self._embedding = embedding_service
        self._chroma = chroma_client
        self._session_factory = meta_session_factory
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # 内部辅助：获取或创建 ChromaDB collection
    # ------------------------------------------------------------------

    async def _get_fields_collection(self) -> Any:
        try:
            return await self._chroma.get_or_create_collection(
                name=_COLLECTION_FIELDS,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            logger.error(
                "failed to get ChromaDB collection",
                collection=_COLLECTION_FIELDS,
                error=str(exc),
            )
            raise AIQueryBaseException(
                f"ChromaDB collection {_COLLECTION_FIELDS} 获取失败：{exc}"
            ) from exc

    async def _get_few_shots_collection(self) -> Any:
        try:
            return await self._chroma.get_or_create_collection(
                name=_COLLECTION_FEW_SHOTS,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            logger.error(
                "failed to get ChromaDB collection",
                collection=_COLLECTION_FEW_SHOTS,
                error=str(exc),
            )
            raise AIQueryBaseException(
                f"ChromaDB collection {_COLLECTION_FEW_SHOTS} 获取失败：{exc}"
            ) from exc

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    async def search_fields(
        self,
        query: str,
        domain: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """语义搜索相关字段（先 ChromaDB，再从元数据库补全元信息）"""
        embedding_result = await self._embedding.embed(query)
        collection = await self._get_fields_collection()

        where: dict[str, Any] | None = {"domain": domain} if domain else None

        try:
            query_result = await collection.query(
                query_embeddings=[embedding_result.vector],
                n_results=top_k,
                where=where,
                include=["metadatas", "distances"],
            )
        except Exception as exc:
            logger.error(
                "ChromaDB query failed",
                collection=_COLLECTION_FIELDS,
                query=query,
                error=str(exc),
            )
            raise AIQueryBaseException(
                f"字段语义搜索失败：{exc}"
            ) from exc

        metadatas: list[dict[str, Any]] = (
            query_result["metadatas"][0] if query_result.get("metadatas") else []
        )
        distances: list[float] = (
            query_result["distances"][0] if query_result.get("distances") else []
        )

        if not metadatas:
            logger.debug(
                "ChromaDB returned no results",
                query=query,
                domain=domain,
            )
            return []

        # 提取 std_name 列表，去元数据库补全详细信息
        std_names = [m.get("std_name") for m in metadatas if m.get("std_name")]

        async with self._session_factory() as session:
            session: AsyncSession
            stmt = select(DataDictionary).where(
                DataDictionary.std_name.in_(std_names)
            )
            rows = (await session.execute(stmt)).scalars().all()

        db_map: dict[str, DataDictionary] = {row.std_name: row for row in rows}

        fields_result: list[dict[str, Any]] = []
        for meta, dist in zip(metadatas, distances):
            std_name = meta.get("std_name", "")
            db_row = db_map.get(std_name)
            entry: dict[str, Any] = {
                "std_name": std_name,
                "display_name": db_row.display_name if db_row else meta.get("display_name", ""),
                "domain": db_row.domain if db_row else meta.get("domain", ""),
                "description": db_row.description if db_row else meta.get("description", ""),
                "unit": db_row.unit if db_row else meta.get("unit", ""),
                "similarity": round(1.0 - dist, 4),
            }
            fields_result.append(entry)

        logger.debug(
            "field search completed",
            query=query,
            domain=domain,
            results=len(fields_result),
        )
        return fields_result

    async def get_few_shots(
        self,
        query: str,
        domain: str,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """获取最相关的 Few-shot 示例"""
        embedding_result = await self._embedding.embed(query)
        collection = await self._get_few_shots_collection()

        try:
            query_result = await collection.query(
                query_embeddings=[embedding_result.vector],
                n_results=top_k,
                where={"domain": domain},
                include=["metadatas", "distances"],
            )
        except Exception as exc:
            logger.error(
                "ChromaDB few-shot query failed",
                collection=_COLLECTION_FEW_SHOTS,
                domain=domain,
                query=query,
                error=str(exc),
            )
            raise AIQueryBaseException(
                f"Few-shot 语义搜索失败：{exc}"
            ) from exc

        metadatas = (
            query_result["metadatas"][0] if query_result.get("metadatas") else []
        )
        distances = (
            query_result["distances"][0] if query_result.get("distances") else []
        )

        if not metadatas:
            logger.debug(
                "no few-shot results found",
                domain=domain,
                query=query,
            )
            return []

        # 从元数据库补全完整 SQL 内容（ChromaDB metadata 中只存索引信息）
        example_ids = [m.get("example_id") for m in metadatas if m.get("example_id")]

        async with self._session_factory() as session:
            session: AsyncSession  # type: ignore[no-redef]
            stmt = select(FewShotExample).where(
                FewShotExample.id.in_(
                    [uuid.UUID(eid) for eid in example_ids if eid]
                ),
                FewShotExample.is_active.is_(True),
            )
            rows = (await session.execute(stmt)).scalars().all()

        db_map_fs: dict[str, FewShotExample] = {
            str(row.id): row for row in rows
        }

        few_shots: list[dict[str, Any]] = []
        for meta, dist in zip(metadatas, distances):
            example_id = meta.get("example_id", "")
            db_row = db_map_fs.get(example_id)
            if db_row is None:
                continue
            few_shots.append(
                {
                    "question": db_row.question,
                    "sql": db_row.sql,
                    "difficulty": db_row.difficulty or "",
                    "similarity": round(1.0 - dist, 4),
                }
            )

        logger.debug(
            "few-shot search completed",
            query=query,
            domain=domain,
            results=len(few_shots),
        )
        return few_shots

    async def get_schema_context(
        self,
        query: str,
        domain: str,
    ) -> SchemaContext:
        """组合 search_fields + get_few_shots + 读取 domain schema YAML"""
        top_k_fields = self._settings.schema_search_top_k

        matched_fields, few_shot_examples, domain_schema_yaml = (
            await self.search_fields(query, domain=domain, top_k=top_k_fields),
            await self.get_few_shots(query, domain=domain),
            await self._read_domain_schema(domain),
        )

        logger.info(
            "schema context built",
            domain=domain,
            fields_count=len(matched_fields),
            few_shots_count=len(few_shot_examples),
            has_schema_yaml=bool(domain_schema_yaml),
        )
        return SchemaContext(
            matched_fields=matched_fields,
            few_shot_examples=few_shot_examples,
            domain_schema_yaml=domain_schema_yaml,
        )

    async def upsert_field(
        self,
        std_name: str,
        display_name: str,
        domain: str,
        description: str,
        synonyms: list[str],
        unit: str | None,
    ) -> None:
        """将新字段写入 ChromaDB 和元数据库（DataDictionary 表）"""
        # 用于向量化的文本：拼接展示名、描述、同义词，提升检索相关性
        embed_text = " ".join(
            filter(None, [display_name, description] + synonyms)
        )
        embedding_result = await self._embedding.embed(embed_text)

        collection = await self._get_fields_collection()
        chroma_id = f"field_{std_name}"
        metadata: dict[str, Any] = {
            "std_name": std_name,
            "display_name": display_name,
            "domain": domain,
            "description": description,
            "unit": unit or "",
        }

        try:
            await collection.upsert(
                ids=[chroma_id],
                embeddings=[embedding_result.vector],
                metadatas=[metadata],
            )
        except Exception as exc:
            logger.error(
                "ChromaDB upsert failed",
                std_name=std_name,
                error=str(exc),
            )
            raise AIQueryBaseException(
                f"字段写入 ChromaDB 失败：{exc}"
            ) from exc

        # 写入或更新元数据库
        async with self._session_factory() as session:
            session: AsyncSession  # type: ignore[no-redef]
            stmt = select(DataDictionary).where(
                DataDictionary.std_name == std_name
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing is None:
                new_entry = DataDictionary(
                    std_name=std_name,
                    display_name=display_name,
                    domain=domain,
                    description=description,
                    synonyms=synonyms,
                    unit=unit,
                    embedding=embedding_result.vector,
                )
                session.add(new_entry)
            else:
                existing.display_name = display_name
                existing.domain = domain
                existing.description = description
                existing.synonyms = synonyms
                existing.unit = unit
                existing.embedding = embedding_result.vector

            await session.commit()

        logger.info(
            "field upserted",
            std_name=std_name,
            domain=domain,
            display_name=display_name,
        )

    # ------------------------------------------------------------------
    # 内部辅助：读取 schema YAML 文件
    # ------------------------------------------------------------------

    async def _read_domain_schema(self, domain: str) -> str:
        """读取 knowledge-base/schemas/{domain}_schema.yaml，不存在时返回空字符串"""
        schema_path = _SCHEMA_DIR / f"{domain}_schema.yaml"
        if not schema_path.exists():
            logger.warning(
                "domain schema YAML not found",
                domain=domain,
                path=str(schema_path),
            )
            return ""

        try:
            content = schema_path.read_text(encoding="utf-8")
            logger.debug(
                "domain schema YAML loaded",
                domain=domain,
                size=len(content),
            )
            return content
        except OSError as exc:
            logger.error(
                "failed to read domain schema YAML",
                path=str(schema_path),
                error=str(exc),
            )
            return ""
