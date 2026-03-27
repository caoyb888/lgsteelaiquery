"""
应用配置模块

所有配置通过环境变量注入，使用 pydantic-settings 管理。
容器内使用服务名（meta_db / biz_db / redis / chromadb）；
宿主机 IDE 调试时在 .env.local 中将 HOST 覆盖为 127.0.0.1。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.local", ".env"),  # .env.local 优先（本地开发覆盖）
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 应用基础 ----
    app_env: Literal["development", "production"] = "development"
    app_secret_key: str = Field(
        default="CHANGE_ME_IN_PRODUCTION_RANDOM_32CHARS",
        description="JWT 签名密钥，生产必须替换",
    )
    app_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    project_name: str = "lgsteel"
    debug: bool = False

    # ---- 元数据库 ----
    meta_db_host: str = "meta_db"          # 容器内默认服务名
    meta_db_port: int = 5432
    meta_db_name: str = "lgsteel_meta"
    meta_db_user: str = "lgsteel_meta_user"
    meta_db_password: str = "CHANGE_ME"

    # ---- 业务数据库 ----
    biz_db_host: str = "biz_db"
    biz_db_port: int = 5432
    biz_db_name: str = "lgsteel_biz"
    biz_db_user: str = "lgsteel_biz_user"
    biz_db_password: str = "CHANGE_ME"

    # ---- Redis ----
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str = "CHANGE_ME"
    redis_db: int = 0

    # ---- ChromaDB ----
    chroma_host: str = "chromadb"
    chroma_port: int = 8000

    # ---- LLM（通义千问）----
    qianwen_api_key: str = ""
    qianwen_model: str = "qwen-max"
    qianwen_base_url: str = "https://dashscope.aliyuncs.com/api/v1"

    # ---- LLM（文心一言）----
    wenxin_api_key: str = ""
    wenxin_secret_key: str = ""
    wenxin_model: str = "ernie-4.0-8k"

    # ---- LLM 通用配置 ----
    llm_timeout_seconds: int = 15
    llm_max_tokens_per_request: int = 2000
    llm_daily_token_budget: int = 5_000_000
    llm_daily_token_budget_per_user: int = 100_000

    # ---- 安全 / JWT ----
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480          # 8 小时
    allow_origins: str = "http://internal.lgsteel.com"  # 禁止配置为 *

    # ---- 查询限制 ----
    query_max_rows: int = 10_000
    query_timeout_seconds: int = 30
    max_sql_retry: int = 3
    query_result_cache_ttl: int = 300      # 5 分钟

    # ---- Excel ----
    excel_max_size_mb: int = 50
    excel_upload_dir: str = "/app/files/excel"
    excel_max_batch_files: int = 10
    field_mapping_confirm_threshold: float = 0.70

    # ---- 对话管理 ----
    max_conversation_turns: int = 10
    conversation_ttl_seconds: int = 7200   # 2 小时

    # ---- 向量检索 ----
    embedding_cache_ttl: int = 86400       # 24 小时
    qa_cache_similarity_threshold: float = 0.92
    schema_search_top_k: int = 5

    # ---- 数据时效预警 ----
    datasource_stale_days: int = 7         # 超过 N 天未更新触发告警

    # ---- 计算属性：数据库 URL ----
    @computed_field  # type: ignore[misc]
    @property
    def meta_db_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.meta_db_user}:{self.meta_db_password}"
            f"@{self.meta_db_host}:{self.meta_db_port}/{self.meta_db_name}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def meta_db_url_sync(self) -> str:
        """Alembic 同步迁移用"""
        return (
            f"postgresql+psycopg2://{self.meta_db_user}:{self.meta_db_password}"
            f"@{self.meta_db_host}:{self.meta_db_port}/{self.meta_db_name}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def biz_db_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.biz_db_user}:{self.biz_db_password}"
            f"@{self.biz_db_host}:{self.biz_db_port}/{self.biz_db_name}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def redis_url(self) -> str:
        return (
            f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}"
            f"/{self.redis_db}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def chroma_url(self) -> str:
        return f"http://{self.chroma_host}:{self.chroma_port}"

    @computed_field  # type: ignore[misc]
    @property
    def excel_max_size_bytes(self) -> int:
        return self.excel_max_size_mb * 1024 * 1024

    @computed_field  # type: ignore[misc]
    @property
    def allow_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allow_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()
