"""
字段映射器

四级映射策略（按优先级）：
1. 精确匹配：与 DataDictionary.std_name / display_name / synonyms 完全一致
2. Embedding 语义匹配：ChromaDB top-1，置信度阈值 0.7
3. LLM 辅助：向 LLM 发送字段名+样本值，返回最可能的标准字段名（fallback）
4. 原始名称：所有策略均失败时使用 clean_name，置信度 0.5
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.config import Settings, get_settings
from app.core.excel_parser import ExcelParseResult, ParsedField
from app.utils.exceptions import FieldMappingError

# 默认 Embedding 相似度阈值（置信度 ≥ 此值才接受 embedding 匹配结果）
_EMBEDDING_CONFIDENCE_THRESHOLD: float = 0.7
# 原始名称降级置信度
_RAW_NAME_CONFIDENCE: float = 0.5


@dataclass
class MappingCandidate:
    raw_name: str
    std_name: str
    display_name: str
    confidence: float
    mapping_source: str    # "exact" | "embedding" | "llm" | "raw"
    unit: str | None


class FieldMapper:
    """
    字段映射器：将 ExcelParseResult.fields 的每个字段映射到标准字段名。

    dictionary_manager 和 llm_router 均可为 None（降级为精确匹配 + 原始名称策略），
    便于测试时不依赖外部服务。
    """

    def __init__(
        self,
        dictionary_manager: Any | None,
        llm_router: Any | None,
        settings: Settings | None = None,
    ) -> None:
        self._dict_mgr = dictionary_manager
        self._llm_router = llm_router
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def map_fields(
        self,
        parse_result: ExcelParseResult,
        domain: str,
    ) -> list[MappingCandidate]:
        """
        对 parse_result.fields 中的每个 ParsedField 进行四级映射。
        返回每个字段的最佳映射候选列表（与 parse_result.fields 等长，顺序一致）。
        """
        if not parse_result.fields:
            logger.warning("parse_result.fields 为空，无字段需要映射")
            return []

        # 预加载字典条目（若 dictionary_manager 可用），供精确匹配使用
        dict_entries = await self._load_dict_entries(domain)

        candidates: list[MappingCandidate] = []
        for parsed_field in parse_result.fields:
            candidate = await self._map_single_field(
                parsed_field=parsed_field,
                domain=domain,
                dict_entries=dict_entries,
            )
            candidates.append(candidate)
            logger.debug(
                "字段映射完成",
                raw_name=parsed_field.raw_name,
                std_name=candidate.std_name,
                source=candidate.mapping_source,
                confidence=candidate.confidence,
            )

        logger.info(
            "字段映射全部完成",
            domain=domain,
            total=len(candidates),
            needs_confirm=len(self.needs_confirmation(candidates)),
        )
        return candidates

    def needs_confirmation(
        self, candidates: list[MappingCandidate]
    ) -> list[MappingCandidate]:
        """返回置信度低于阈值、需要人工确认的候选列表"""
        threshold = self._settings.field_mapping_confirm_threshold
        return [c for c in candidates if c.confidence < threshold]

    # ------------------------------------------------------------------
    # 内部：单字段四级映射
    # ------------------------------------------------------------------

    async def _map_single_field(
        self,
        parsed_field: ParsedField,
        domain: str,
        dict_entries: list[dict[str, Any]],
    ) -> MappingCandidate:
        """
        按优先级尝试四种映射策略，返回第一个成功的结果。
        """
        # 策略 1：精确匹配
        exact = self._try_exact_match(parsed_field, dict_entries)
        if exact is not None:
            return exact

        # 策略 2：Embedding 语义匹配
        if self._dict_mgr is not None:
            embedding = await self._try_embedding_match(parsed_field, domain)
            if embedding is not None:
                return embedding

        # 策略 3：LLM 辅助
        if self._llm_router is not None:
            llm_result = await self._try_llm_match(parsed_field, domain)
            if llm_result is not None:
                return llm_result

        # 策略 4：原始名称
        return MappingCandidate(
            raw_name=parsed_field.raw_name,
            std_name=parsed_field.clean_name,
            display_name=parsed_field.clean_name,
            confidence=_RAW_NAME_CONFIDENCE,
            mapping_source="raw",
            unit=parsed_field.unit,
        )

    # ------------------------------------------------------------------
    # 策略 1：精确匹配
    # ------------------------------------------------------------------

    def _try_exact_match(
        self,
        parsed_field: ParsedField,
        dict_entries: list[dict[str, Any]],
    ) -> MappingCandidate | None:
        """
        检查 raw_name 或 clean_name 是否与字典中的
        std_name / display_name / synonyms 完全一致（大小写敏感）。
        """
        names_to_check = {parsed_field.raw_name, parsed_field.clean_name}

        for entry in dict_entries:
            std_name: str = entry.get("std_name", "")
            display_name: str = entry.get("display_name", "")
            synonyms: list[str] = entry.get("synonyms") or []

            match_targets = {std_name, display_name} | set(synonyms)
            if names_to_check & match_targets:
                logger.debug(
                    "精确匹配成功",
                    raw_name=parsed_field.raw_name,
                    std_name=std_name,
                )
                return MappingCandidate(
                    raw_name=parsed_field.raw_name,
                    std_name=std_name,
                    display_name=display_name,
                    confidence=1.0,
                    mapping_source="exact",
                    unit=entry.get("unit") or parsed_field.unit,
                )
        return None

    # ------------------------------------------------------------------
    # 策略 2：Embedding 语义匹配
    # ------------------------------------------------------------------

    async def _try_embedding_match(
        self,
        parsed_field: ParsedField,
        domain: str,
    ) -> MappingCandidate | None:
        """
        调用 DataDictionaryManager.search_fields，取 top-1 结果。
        若相似度 ≥ 阈值则返回候选，否则返回 None。
        """
        query_text = parsed_field.raw_name
        try:
            results = await self._dict_mgr.search_fields(
                query=query_text,
                domain=domain,
                top_k=1,
            )
        except Exception as exc:
            logger.warning(
                "Embedding 语义匹配失败，降级",
                raw_name=parsed_field.raw_name,
                error=str(exc),
            )
            return None

        if not results:
            return None

        top = results[0]
        similarity: float = float(top.get("similarity", 0.0))

        if similarity < _EMBEDDING_CONFIDENCE_THRESHOLD:
            logger.debug(
                "Embedding 相似度不足，跳过",
                raw_name=parsed_field.raw_name,
                similarity=similarity,
                threshold=_EMBEDDING_CONFIDENCE_THRESHOLD,
            )
            return None

        std_name: str = top.get("std_name", parsed_field.clean_name)
        display_name: str = top.get("display_name", std_name)
        unit: str | None = top.get("unit") or parsed_field.unit or None

        logger.debug(
            "Embedding 匹配成功",
            raw_name=parsed_field.raw_name,
            std_name=std_name,
            similarity=similarity,
        )
        return MappingCandidate(
            raw_name=parsed_field.raw_name,
            std_name=std_name,
            display_name=display_name,
            confidence=similarity,
            mapping_source="embedding",
            unit=unit,
        )

    # ------------------------------------------------------------------
    # 策略 3：LLM 辅助匹配
    # ------------------------------------------------------------------

    async def _try_llm_match(
        self,
        parsed_field: ParsedField,
        domain: str,
    ) -> MappingCandidate | None:
        """
        构造 Prompt 发给 LLM，让其推断标准字段名。
        期望 LLM 返回 JSON：{"std_name": "...", "display_name": "...", "confidence": 0.x}
        解析失败时返回 None。
        """
        sample_str = ", ".join(str(v) for v in parsed_field.sample_values[:3])
        prompt = (
            f"你是一个钢铁企业数据字典专家。\n"
            f"以下是一个 Excel 字段的信息：\n"
            f"  原始字段名：{parsed_field.raw_name}\n"
            f"  字段类型：{parsed_field.inferred_type}\n"
            f"  样本值：{sample_str}\n"
            f"  数据域：{domain}\n\n"
            f"请判断该字段最可能对应的标准字段名（英文下划线格式），并给出中文展示名和置信度。\n"
            f"仅返回如下 JSON，不要有其他内容：\n"
            f'{{"std_name": "<英文标准名>", "display_name": "<中文展示名>", "confidence": <0.0-1.0>}}'
        )

        try:
            response = await self._llm_router.complete(
                prompt=prompt,
                max_tokens=200,
            )
            raw_text: str = response.content.strip()
        except Exception as exc:
            logger.warning(
                "LLM 字段映射调用失败",
                raw_name=parsed_field.raw_name,
                error=str(exc),
            )
            return None

        # 尝试从响应文本中提取 JSON
        try:
            # 有些模型会在代码块中返回 JSON
            json_text = _extract_json(raw_text)
            data = json.loads(json_text)
            std_name: str = str(data.get("std_name", "")).strip()
            display_name: str = str(data.get("display_name", std_name)).strip()
            confidence: float = float(data.get("confidence", 0.6))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "LLM 响应 JSON 解析失败",
                raw_name=parsed_field.raw_name,
                raw_text=raw_text[:200],
                error=str(exc),
            )
            return None

        if not std_name:
            return None

        logger.debug(
            "LLM 匹配成功",
            raw_name=parsed_field.raw_name,
            std_name=std_name,
            confidence=confidence,
        )
        return MappingCandidate(
            raw_name=parsed_field.raw_name,
            std_name=std_name,
            display_name=display_name,
            confidence=confidence,
            mapping_source="llm",
            unit=parsed_field.unit,
        )

    # ------------------------------------------------------------------
    # 内部辅助：加载字典条目
    # ------------------------------------------------------------------

    async def _load_dict_entries(self, domain: str) -> list[dict[str, Any]]:
        """
        从 dictionary_manager 加载当前域的字典条目（用于精确匹配）。
        若 dictionary_manager 为 None 或查询失败，返回空列表。
        """
        if self._dict_mgr is None:
            return []

        try:
            # DataDictionaryManager 提供 search_fields；
            # 精确匹配需要更宽泛的条目列表，这里用一个宽泛的 dummy 查询
            # 实际上精确匹配直接在字典条目集合上比对，不依赖语义相关性。
            # 如果 dictionary_manager 暴露了 list_all_fields(domain) 就更好，
            # 这里用约定俗成的方式：精确匹配时直接尝试 search_fields 并扩大 top_k。
            results = await self._dict_mgr.search_fields(
                query="*",
                domain=domain,
                top_k=500,
            )
            return results
        except Exception as exc:
            logger.warning(
                "加载字典条目失败，精确匹配将使用空列表",
                domain=domain,
                error=str(exc),
            )
            return []


# ------------------------------------------------------------------
# 模块级辅助
# ------------------------------------------------------------------


def _extract_json(text: str) -> str:
    """
    从文本中提取第一个 { ... } JSON 块（支持 markdown 代码块包裹）。
    """
    # 去掉 markdown 代码块标记
    text = text.replace("```json", "").replace("```", "").strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end >= start:
        return text[start: end + 1]
    return text
