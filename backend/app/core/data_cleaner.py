"""
数据清洗器

对 ExcelParseResult 进行 7 条规则清洗，并将结果写入业务数据库（动态建表）。
支持 replace（DROP+CREATE）和 append（CREATE IF NOT EXISTS + 去重）两种写入模式。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.excel_parser import ExcelParseResult
from app.utils.exceptions import FieldMappingError

# 提取数字部分（支持负号/小数）
_NUM_RE = re.compile(r"([+-]?\d+\.?\d*)")

# 规范化为 True 的字符串集合（大小写不敏感，已 strip）
_TRUE_STRINGS: frozenset[str] = frozenset(
    {"是", "y", "yes", "1", "true"}
)
# 规范化为 False 的字符串集合
_FALSE_STRINGS: frozenset[str] = frozenset(
    {"否", "n", "no", "0", "false"}
)

# PostgreSQL 类型映射
_PG_TYPE_MAP: dict[str, str] = {
    "numeric": "NUMERIC(20,4)",
    "date": "TEXT",
    "boolean": "BOOLEAN",
    "text": "TEXT",
}


@dataclass
class CleanResult:
    table_name: str          # 写入业务库的表名（如 sales_a3f2b1c0）
    rows_written: int        # 实际写入行数
    rows_skipped: int        # 跳过行数（空行、重复行）
    warnings: list[str] = field(default_factory=list)   # 清洗过程中的告警


class DataCleaner:
    """
    对 ExcelParseResult 进行数据清洗，并将结果写入业务数据库。

    用法：
        cleaner = DataCleaner(biz_session_factory)
        result = await cleaner.clean_and_load(parse_result, datasource_id, domain, "replace")
    """

    def __init__(
        self,
        biz_session_factory: Any,
        settings: Settings | None = None,
    ) -> None:
        self._session_factory = biz_session_factory
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def clean_and_load(
        self,
        parse_result: ExcelParseResult,
        datasource_id: str,
        domain: str,
        update_mode: str,
    ) -> CleanResult:
        """
        主流程：
        1. _apply_cleaning_rules(df) → 清洗后的 DataFrame
        2. 根据 update_mode 决定建表策略（replace=DROP+CREATE，append=CREATE IF NOT EXISTS）
        3. 写入业务库（动态建表）
        4. 返回 CleanResult
        """
        if update_mode not in ("replace", "append"):
            raise FieldMappingError(
                f"update_mode 必须为 'replace' 或 'append'，收到：{update_mode!r}"
            )

        table_name = _build_table_name(domain, datasource_id)
        warnings: list[str] = list(parse_result.warnings)

        logger.info(
            "开始数据清洗",
            datasource_id=datasource_id,
            domain=domain,
            update_mode=update_mode,
            table_name=table_name,
            input_rows=len(parse_result.df),
        )

        # 1. 清洗规则
        cleaned_df, _dedup_skipped = self._apply_cleaning_rules(
            parse_result.df.copy(),
            parse_result,
            update_mode,
            warnings,
        )

        rows_to_write = len(cleaned_df)
        # rows_skipped = 输入行数 - 最终写入行数（已含空行删除 + 去重跳过）
        rows_skipped_total = len(parse_result.df) - rows_to_write

        # 2 & 3. 动态建表并写入
        if rows_to_write > 0:
            await self._create_table_and_insert(
                cleaned_df=cleaned_df,
                parse_result=parse_result,
                table_name=table_name,
                update_mode=update_mode,
                warnings=warnings,
            )
        else:
            warnings.append("清洗后无有效数据行，跳过写入")
            logger.warning("清洗后无有效数据行，跳过写入", table_name=table_name)

        logger.info(
            "数据清洗完成",
            table_name=table_name,
            rows_written=rows_to_write,
            rows_skipped=rows_skipped_total,
        )

        return CleanResult(
            table_name=table_name,
            rows_written=rows_to_write,
            rows_skipped=rows_skipped_total,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # 清洗规则流水线
    # ------------------------------------------------------------------

    def _apply_cleaning_rules(
        self,
        df: pd.DataFrame,
        parse_result: ExcelParseResult,
        update_mode: str,
        warnings: list[str],
    ) -> tuple[pd.DataFrame, int]:
        """
        按顺序应用 7 条清洗规则，返回 (清洗后的 df, 规则7去重跳过行数)。
        """
        # 规则 1：去空行（全部字段均为 NaN/None）
        before = len(df)
        df = df.dropna(how="all")
        dropped_empty = before - len(df)
        if dropped_empty > 0:
            warnings.append(f"规则1：删除全空行 {dropped_empty} 行")
            logger.debug("规则1 去空行", dropped=dropped_empty)

        # 规则 2：去首尾空格（object 类型列）
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].apply(
                    lambda v: v.strip() if isinstance(v, str) else v
                )

        # 构建字段名 → inferred_type 的映射表（使用 clean_name）
        type_map: dict[str, str] = {
            f.clean_name: f.inferred_type for f in parse_result.fields
        }

        # 规则 3：统一日期格式
        for col in df.columns:
            if type_map.get(col) == "date":
                df[col] = df[col].apply(_normalize_date)

        # 规则 4：去数字单位（numeric 类型列）
        for col in df.columns:
            if type_map.get(col) == "numeric":
                df[col] = df[col].apply(_strip_unit_to_numeric)

        # 规则 5：统一布尔值（boolean 类型列）
        for col in df.columns:
            if type_map.get(col) == "boolean":
                df[col] = df[col].apply(_normalize_boolean)

        # 规则 6：截断超长字符串（text 类型列）
        for col in df.columns:
            if type_map.get(col) == "text":
                df[col] = df[col].apply(_truncate_long_string)

        # 规则 7：追加模式去重（基于行内容 MD5）
        dedup_skipped = 0
        if update_mode == "append":
            df, dedup_skipped = _dedup_by_hash(df, warnings)

        return df, dedup_skipped

    # ------------------------------------------------------------------
    # 动态建表与数据写入
    # ------------------------------------------------------------------

    async def _create_table_and_insert(
        self,
        cleaned_df: pd.DataFrame,
        parse_result: ExcelParseResult,
        table_name: str,
        update_mode: str,
        warnings: list[str],
    ) -> None:
        """
        根据 update_mode 建表，然后逐行插入清洗后的数据。
        """
        # 构建列定义
        type_map: dict[str, str] = {
            f.clean_name: f.inferred_type for f in parse_result.fields
        }
        col_defs: list[str] = []
        for col in cleaned_df.columns:
            if col == "_row_hash":
                continue
            pg_type = _PG_TYPE_MAP.get(type_map.get(col, "text"), "TEXT")
            safe_col = _quote_ident(col)
            col_defs.append(f"    {safe_col} {pg_type}")
        col_defs.append("    _row_hash TEXT")

        col_defs_sql = ",\n".join(col_defs)
        quoted_table = _quote_ident(table_name)

        if update_mode == "replace":
            drop_sql = f"DROP TABLE IF EXISTS {quoted_table}"
            create_sql = (
                f"CREATE TABLE {quoted_table} (\n{col_defs_sql}\n)"
            )
        else:
            create_sql = (
                f"CREATE TABLE IF NOT EXISTS {quoted_table} (\n{col_defs_sql}\n)"
            )
            drop_sql = None

        async with self._session_factory() as session:
            session: AsyncSession
            if drop_sql:
                await session.execute(text(drop_sql))
                logger.debug("DROP TABLE IF EXISTS 执行", table=table_name)

            await session.execute(text(create_sql))
            logger.debug(
                "建表完成",
                table=table_name,
                mode=update_mode,
            )

            # 逐行插入
            insert_cols = [_quote_ident(c) for c in cleaned_df.columns]
            if "_row_hash" not in cleaned_df.columns:
                insert_cols.append(_quote_ident("_row_hash"))

            placeholders = ", ".join(
                f":col_{i}" for i in range(len(insert_cols))
            )
            col_list = ", ".join(insert_cols)
            insert_sql = (
                f"INSERT INTO {quoted_table} ({col_list}) VALUES ({placeholders})"
            )

            for _, row in cleaned_df.iterrows():
                row_hash = _compute_row_hash(row)
                params: dict[str, Any] = {}
                for i, col in enumerate(cleaned_df.columns):
                    val = row[col]
                    params[f"col_{i}"] = _coerce_value(val)
                # 若 df 已含 _row_hash 列，覆盖；否则追加
                if "_row_hash" in cleaned_df.columns:
                    hash_idx = list(cleaned_df.columns).index("_row_hash")
                    params[f"col_{hash_idx}"] = row_hash
                else:
                    params[f"col_{len(cleaned_df.columns)}"] = row_hash

                await session.execute(text(insert_sql), params)

            await session.commit()
            logger.info(
                "数据写入完成",
                table=table_name,
                rows=len(cleaned_df),
            )


# ------------------------------------------------------------------
# 模块级纯函数（可单独测试）
# ------------------------------------------------------------------


def _coerce_value(val: Any) -> Any:
    """
    将 DataFrame 单元格值转换为 SQLAlchemy 可安全绑定的 Python 原生类型。
    - None / float NaN → None
    - numpy bool_ → Python bool
    - numpy integer / floating → Python int / float
    - 其他值保持原样
    """
    if val is None:
        return None
    # numpy bool_ → Python bool（必须在 int 检查前，因为 numpy.bool_ 是 int 子类）
    import numpy as np  # 延迟导入，避免顶层强依赖
    if isinstance(val, np.bool_):
        return bool(val)
    # numpy integer → Python int
    if isinstance(val, np.integer):
        return int(val)
    # numpy floating → Python float（含 NaN 检查）
    if isinstance(val, np.floating):
        if np.isnan(val):
            return None
        return float(val)
    # Python float NaN 检查
    if isinstance(val, float):
        try:
            if pd.isna(val):
                return None
        except (TypeError, ValueError):
            pass
    return val


def _build_table_name(domain: str, datasource_id: str) -> str:
    """生成业务库表名：{domain}_{datasource_id_hex[:8]}"""
    # datasource_id 可能是 UUID 字符串（含或不含连字符）
    hex_part = datasource_id.replace("-", "")[:8]
    return f"{domain}_{hex_part}"


def _quote_ident(name: str) -> str:
    """对 PostgreSQL 标识符加双引号（简单转义）"""
    return '"' + name.replace('"', '""') + '"'


def _normalize_date(value: Any) -> str | None:
    """
    尝试将各种日期字符串/值统一转为 YYYY-MM-DD。
    无法解析时返回 None。
    支持格式包括：
    - "2026-01-15" / "2026/01/15" / "20260115"
    - "2026年1月" → "2026-01-01"
    - "2026-01" → "2026-01-01"
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    str_val = str(value).strip()
    if not str_val:
        return None

    # 先尝试 pandas 通用解析
    try:
        parsed = pd.to_datetime(str_val, format="%Y-%m-%d")
        return parsed.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass

    # 中文年月日格式：2026年1月15日
    match = re.match(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", str_val
    )
    if match:
        y, m, d = match.groups()
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"

    # 中文年月（无日）：2026年1月 → 2026-01-01
    match = re.match(r"(\d{4})\s*年\s*(\d{1,2})\s*月", str_val)
    if match:
        y, m = match.groups()
        return f"{int(y):04d}-{int(m):02d}-01"

    # 其他格式交给 pandas 宽松解析
    _FMT_LIST = [
        "%Y/%m/%d",
        "%Y%m%d",
        "%Y-%m",
        "%Y/%m",
    ]
    for fmt in _FMT_LIST:
        try:
            parsed = pd.to_datetime(str_val, format=fmt)
            return parsed.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue

    # 最后尝试通用宽松解析
    try:
        parsed = pd.to_datetime(str_val)
        return parsed.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _strip_unit_to_numeric(value: Any) -> float | None:
    """
    提取数值：如 "1250万" → 1250.0，"860元" → 860.0，纯数字直接转。
    无法提取时返回 None。
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    str_val = str(value).strip()
    match = _NUM_RE.search(str_val)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _normalize_boolean(value: Any) -> bool | None:
    """
    统一布尔值：是/Y/Yes/1/True → True；否/N/No/0/False → False；其他 → None。
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return value
    str_val = str(value).strip().lower()
    if str_val in _TRUE_STRINGS:
        return True
    if str_val in _FALSE_STRINGS:
        return False
    return None


def _truncate_long_string(value: Any, max_len: int = 500) -> Any:
    """
    截断超过 max_len 字符的字符串，追加 "..."。
    """
    if not isinstance(value, str):
        return value
    if len(value) > max_len:
        return value[:max_len] + "..."
    return value


def _compute_row_hash(row: pd.Series) -> str:
    """基于行所有字段值（字符串拼接）计算 MD5"""
    content = "|".join(
        str(v) if v is not None and not (isinstance(v, float) and pd.isna(v)) else ""
        for v in row
    )
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _dedup_by_hash(
    df: pd.DataFrame,
    warnings: list[str],
) -> tuple[pd.DataFrame, int]:
    """
    为每行计算 MD5 hash，写入 _row_hash 列，并在 DataFrame 内部去重。
    （与已有数据库数据的对比由调用方负责；此处仅对当前批次内部去重。）
    """
    df = df.copy()
    df["_row_hash"] = df.apply(_compute_row_hash, axis=1)
    before = len(df)
    df = df.drop_duplicates(subset=["_row_hash"])
    skipped = before - len(df)
    if skipped > 0:
        warnings.append(f"规则7：追加去重，跳过 {skipped} 条重复行")
        logger.debug("规则7 去重", skipped=skipped)
    return df, skipped
