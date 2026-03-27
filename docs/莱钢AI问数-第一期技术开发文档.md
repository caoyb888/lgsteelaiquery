# 莱钢集团 AI 问数 — 第一期技术开发文档

**文档编号：** LGSTEEL-DEV-2026-001
**文档版本：** V1.0
**编制日期：** 2026-03-26
**技术负责人：** （待填写）
**文档状态：** 正式版
**关联文档：** 解决方案 V2.0、CLAUDE.md V1.0

---

## 目录

1. 开发总览
2. 模块详细设计
   - 2.1 Excel 解析引擎
   - 2.2 数据清洗与字段映射
   - 2.3 Text-to-SQL 引擎
   - 2.4 SQL 安全校验
   - 2.5 查询执行与结果格式化
   - 2.6 LLM 调用层
   - 2.7 知识库与向量检索
   - 2.8 权限与审计
   - 2.9 对话管理
   - 2.10 REST API 接口
3. 数据库设计
4. 接口规范
5. 单元测试规范与用例
6. 集成测试规范与用例
7. 全面测试计划
8. 部署方案
9. 开发环境搭建
10. 编码检查清单

---

## 一、开发总览

### 1.1 第一期交付范围

```
┌─────────────────────────────────────────────────────┐
│                  第一期交付边界                        │
├──────────────┬──────────────────────────────────────┤
│  数据接入     │  Excel 上传 → 解析 → 清洗 → 入库       │
│  核心引擎     │  Text-to-SQL（单表+多表）              │
│  查询交互     │  多轮对话、结果展示、图表、导出          │
│  权限管理     │  用户登录、角色权限、数据域隔离          │
│  运维支撑     │  操作审计、准确率统计、数据更新提醒      │
├──────────────┴──────────────────────────────────────┤
│  不在第一期范围：ERP/MES 直连、移动端、AD 域集成        │
└─────────────────────────────────────────────────────┘
```

### 1.2 模块依赖关系

```mermaid
graph TD
    subgraph 前端 Frontend
        FE_CHAT[对话界面 ChatView]
        FE_UPLOAD[Excel管理 UploadView]
        FE_ADMIN[管理后台 AdminView]
    end

    subgraph 后端 Backend
        API_CHAT[/api/v1/chat]
        API_DS[/api/v1/datasource]
        API_ADMIN[/api/v1/admin]
        API_AUTH[/api/v1/auth]
    end

    subgraph 核心模块 Core
        M_EXCEL[Excel解析引擎\nexcel_parser]
        M_CLEAN[数据清洗映射\ndata_cleaner]
        M_T2S[Text-to-SQL引擎\ntext_to_sql]
        M_VALID[SQL安全校验\nsql_validator]
        M_EXEC[查询执行器\nsql_executor]
        M_FMT[结果格式化\nresult_formatter]
        M_NLG[自然语言生成\nnlg]
    end

    subgraph 支撑模块 Support
        M_LLM[LLM调用层\nllm_router]
        M_KB[知识库\nknowledge_base]
        M_AUTH[权限模块\nrbac]
        M_AUDIT[审计模块\naudit]
    end

    subgraph 存储 Storage
        DB_META[(PostgreSQL\n元数据/审计/用户)]
        DB_BIZ[(PostgreSQL\n业务数据\nExcel导入)]
        DB_VEC[(ChromaDB\n向量库)]
        REDIS[(Redis\n缓存/会话)]
        FILE_STORE[/files\nExcel原始文件]
    end

    FE_CHAT --> API_CHAT
    FE_UPLOAD --> API_DS
    FE_ADMIN --> API_ADMIN
    API_CHAT --> M_T2S
    API_DS --> M_EXCEL --> M_CLEAN
    M_CLEAN --> DB_BIZ
    M_T2S --> M_KB & M_LLM
    M_T2S --> M_VALID --> M_EXEC
    M_EXEC --> DB_BIZ
    M_EXEC --> M_FMT --> M_NLG
    M_NLG --> M_LLM
    M_KB --> DB_VEC
    M_AUTH --> DB_META
    M_AUDIT --> DB_META
    M_T2S --> REDIS
```

### 1.3 开发人员分工

| 模块 | 负责人 | 工期 |
|------|------|------|
| Excel 解析引擎 + 数据清洗映射 | 后端开发 A | 4/10~4/19 |
| Text-to-SQL 引擎 + SQL 校验 | 算法工程师 | 4/13~4/26 |
| LLM 调用层 + 知识库 | 算法工程师 | 4/13~4/24 |
| 查询执行 + 结果格式化 + NLG | 后端开发 B | 4/17~4/26 |
| 权限 + 审计 + REST API | 后端开发 A/B 协作 | 4/17~4/30 |
| 对话界面 + Excel 管理前端 | 前端开发 | 4/20~5/02 |
| 管理后台前端 | 前端开发 | 4/27~5/07 |
| 单元测试（随模块开发同步完成）| 各模块负责人 | 同开发周期 |
| 集成测试 + UAT | 测试工程师 | 5/04~5/17 |

---

## 二、模块详细设计

### 2.1 Excel 解析引擎（excel_parser）

**职责：** 接收上传的 Excel/CSV 文件，解析 Sheet 结构，识别表头与数据区域，输出标准化的中间数据结构。

**文件位置：** `backend/app/core/excel_parser.py`

#### 核心数据结构

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class FieldType(str, Enum):
    INTEGER   = "integer"
    FLOAT     = "float"
    TEXT      = "text"
    DATE      = "date"
    DATETIME  = "datetime"
    BOOLEAN   = "boolean"
    UNKNOWN   = "unknown"

@dataclass
class ParsedField:
    raw_name: str          # 原始表头名称，如"销售额（万元）"
    inferred_type: FieldType
    null_count: int        # 空值数量
    sample_values: list[Any]  # 前5个非空样本值
    has_unit_suffix: bool  # 是否含单位后缀，如"（万元）"
    unit_hint: str = ""    # 识别出的单位，如"万元"

@dataclass
class ParsedSheet:
    sheet_name: str
    header_row: int        # 表头所在行（0-indexed）
    data_start_row: int    # 数据起始行
    total_rows: int        # 数据行数（不含表头）
    fields: list[ParsedField]
    warnings: list[str]    # 解析告警，如"发现合并单元格已展开"
    raw_preview: list[dict[str, Any]]  # 前10行预览数据

@dataclass
class ExcelParseResult:
    file_name: str
    file_size_bytes: int
    sheets: list[ParsedSheet]
    parse_errors: list[str]   # 严重错误，如"文件损坏"
    is_valid: bool
```

#### 关键方法签名

```python
class ExcelParser:
    """Excel 文件解析引擎，支持 .xlsx/.xls/.csv"""

    async def parse(
        self,
        file_path: str,
        target_sheet: str | None = None,   # None 则解析所有 Sheet
        header_row_hint: int = 0,
    ) -> ExcelParseResult:
        """
        解析 Excel 文件主入口。

        Args:
            file_path: 服务器上的文件路径
            target_sheet: 指定解析的 Sheet 名，None 表示全部
            header_row_hint: 表头行索引提示，默认第0行

        Returns:
            ExcelParseResult 包含解析结果与告警

        Raises:
            FileNotFoundError: 文件不存在
            UnsupportedFormatError: 不支持的文件格式
            FileTooLargeError: 文件超过 50MB 限制
        """

    def _detect_header_row(self, df: pd.DataFrame) -> int:
        """自动检测表头行：寻找非空率最高且含文本的行"""

    def _infer_field_type(self, series: pd.Series) -> FieldType:
        """基于采样数据推断字段类型，支持中文日期格式"""

    def _expand_merged_cells(self, ws) -> None:
        """展开 openpyxl 工作表中的合并单元格，向下填充"""

    def _clean_header_name(self, raw: str) -> tuple[str, str]:
        """
        清理表头名称，提取单位后缀。
        例："销售额（万元）" -> ("销售额", "万元")
             "产量(吨)"     -> ("产量", "吨")
        """

    def _validate_structure(self, sheet: ParsedSheet) -> list[str]:
        """结构合法性校验，返回告警列表"""
```

#### 单元测试要求

测试文件：`tests/unit/test_excel_parser.py`，覆盖率要求 ≥ 90%

---

### 2.2 数据清洗与字段映射（data_cleaner + field_mapper）

**职责：** 对解析后的原始数据进行清洗标准化，并将业务表头映射为规范化字段名，最终写入业务数据库。

**文件位置：**
- `backend/app/core/data_cleaner.py`
- `backend/app/core/field_mapper.py`

#### 字段映射策略

```python
class FieldMapper:
    """
    将业务 Excel 表头映射为标准化字段名。

    映射优先级：
    1. 精确匹配：数据字典中已有记录
    2. 语义相似度：Embedding 余弦相似度 > 0.85
    3. LLM 辅助：相似度 < 0.85 时调用 LLM 推断
    4. 人工确认：LLM 置信度 < 0.70 时标记为待确认
    """

    async def map_fields(
        self,
        parsed_fields: list[ParsedField],
        domain: str,                  # 数据域：finance/sales/production/procurement
        user_confirmed: dict[str, str] | None = None,  # 人工确认的映射
    ) -> list[FieldMapping]:
        ...

@dataclass
class FieldMapping:
    raw_name: str           # 原始表头
    std_name: str           # 标准化字段名（英文 snake_case）
    display_name: str       # 展示名（中文）
    field_type: FieldType
    unit: str               # 单位
    domain: str             # 所属数据域
    confidence: float       # 映射置信度 0~1
    needs_confirm: bool     # 是否需要人工确认
    mapping_source: str     # exact_match / embedding / llm / manual
```

#### 数据清洗规则

```python
class DataCleaner:
    """数据清洗器，处理常见数据质量问题"""

    # 清洗规则注册表（可扩展）
    CLEANING_RULES: list[CleaningRule] = [
        RemoveBlankRowsRule(),       # 删除全空行
        StripWhitespaceRule(),       # 去除首尾空格
        UnifyDateFormatRule(),       # 统一日期格式为 YYYY-MM-DD
        StripNumericUnitRule(),      # "1250万元" -> 1250.0
        NormalizeBooleanRule(),      # "是/否/Y/N" -> True/False
        FillMergedCellValueRule(),   # 合并单元格向下填充
        DeduplicateRowsRule(),       # 追加模式下去重
    ]

    async def clean(
        self,
        df: pd.DataFrame,
        mappings: list[FieldMapping],
        mode: Literal["append", "replace"] = "replace",
        existing_hash_set: set[str] | None = None,  # 追加去重用
    ) -> CleanResult:
        ...

@dataclass
class CleanResult:
    df_clean: pd.DataFrame
    original_rows: int
    cleaned_rows: int
    removed_rows: int
    warnings: list[str]
    rules_applied: list[str]
```

---

### 2.3 Text-to-SQL 引擎（text_to_sql）

**职责：** 接收自然语言问题，协调 Schema 检索、Prompt 构建、LLM 调用，生成可执行 SQL。

**文件位置：** `backend/app/core/text_to_sql.py`

#### 主流程

```python
class TextToSQLEngine:

    async def generate(
        self,
        question: str,
        user_id: str,
        conversation_id: str,
        available_tables: list[str] | None = None,
    ) -> SQLGenerationResult:
        """
        主流程：
        1. 从会话上下文补全问题（多轮）
        2. 语义检索相关表和字段
        3. 构建 Prompt（含数据字典 + Few-shot）
        4. 调用 LLM 生成 SQL
        5. 校验 SQL；不通过则最多重试 MAX_RETRY=3 次
        6. 返回 SQLGenerationResult
        """

@dataclass
class SQLGenerationResult:
    sql: str
    tables_used: list[str]
    confidence: float          # 0~1，LLM 置信度估算
    retry_count: int
    prompt_tokens: int
    completion_tokens: int
    generation_ms: int
    error: str | None          # 最终仍失败时的错误信息
```

#### Prompt 模板规范

```python
# backend/app/core/prompt_builder.py

SYSTEM_PROMPT_TEMPLATE = """
你是莱钢集团的数据分析助手，负责将用户的中文业务问题转换为 SQL 查询语句。

## 数据表信息
{schema_context}

## 业务术语说明
{domain_glossary}

## 参考示例
{few_shot_examples}

## 约束规则
1. 只能生成 SELECT 语句，严禁 INSERT/UPDATE/DELETE/DROP 等操作
2. 金额字段默认单位为万元，除非用户明确指定
3. 日期字段格式为 YYYY-MM-DD
4. 表名和字段名使用数据表信息中的标准名称
5. 如果问题无法用现有数据回答，返回 SQL_NOT_POSSIBLE，并说明原因
6. 只返回 SQL 语句，不要附加任何解释文字

## 用户问题
{question}
"""

RETRY_PROMPT_TEMPLATE = """
上次生成的 SQL 执行出现以下错误：
错误信息：{error_message}
错误的 SQL：{failed_sql}

请根据错误信息修正 SQL，注意：
- {fix_hints}

重新生成正确的 SQL：
"""
```

---

### 2.4 SQL 安全校验（sql_validator）

**职责：** 对生成的 SQL 进行多层安全检查，防止危险操作和 SQL 注入。

**文件位置：** `backend/app/security/sql_validator.py`

```python
class SQLValidator:
    """
    SQL 安全校验器，多层防护。

    校验层次：
    Layer 1 - 关键字黑名单（正则匹配，最快）
    Layer 2 - SQL 解析树分析（sqlglot AST）
    Layer 3 - 白名单表校验（只允许查询已注册的业务表）
    """

    # Layer 1: 必须拦截的关键字模式
    FORBIDDEN_PATTERNS: list[re.Pattern] = [
        re.compile(r'\bDROP\b', re.IGNORECASE),
        re.compile(r'\bTRUNCATE\b', re.IGNORECASE),
        re.compile(r'\bDELETE\b', re.IGNORECASE),
        re.compile(r'\bUPDATE\b', re.IGNORECASE),
        re.compile(r'\bINSERT\b', re.IGNORECASE),
        re.compile(r'\bCREATE\b', re.IGNORECASE),
        re.compile(r'\bALTER\b', re.IGNORECASE),
        re.compile(r'\bEXEC(?:UTE)?\b', re.IGNORECASE),
        re.compile(r';\s*\w+'),         # 多语句
        re.compile(r'--'),              # 行注释
        re.compile(r'/\*.*?\*/', re.DOTALL),  # 块注释
        re.compile(r'\bINTO\s+OUTFILE\b', re.IGNORECASE),
        re.compile(r'\bLOAD_FILE\b', re.IGNORECASE),
    ]

    def validate(self, sql: str, allowed_tables: set[str]) -> ValidationResult:
        """
        同步校验（供 async 环境中 run_in_executor 调用）。

        Returns:
            ValidationResult(is_safe, violations, normalized_sql)
        """

    def _check_forbidden_patterns(self, sql: str) -> list[str]:
        """Layer 1: 正则黑名单扫描，返回所有匹配的违规项"""

    def _check_ast(self, sql: str) -> list[str]:
        """Layer 2: 用 sqlglot 解析 AST，确认只包含 SELECT 语句"""

    def _check_table_whitelist(self, sql: str, allowed: set[str]) -> list[str]:
        """Layer 3: 提取 SQL 中引用的表名，与白名单对比"""

@dataclass
class ValidationResult:
    is_safe: bool
    violations: list[str]       # 违规项描述列表
    normalized_sql: str         # 格式化后的 SQL（sqlglot 标准化）
    check_ms: int
```

---

### 2.5 查询执行与结果格式化（sql_executor + result_formatter）

**文件位置：**
- `backend/app/core/sql_executor.py`
- `backend/app/core/result_formatter.py`

```python
class SQLExecutor:
    """业务数据库查询执行器（只读账号）"""

    QUERY_MAX_ROWS: int = 10_000
    QUERY_TIMEOUT_SECONDS: int = 30

    async def execute(
        self,
        sql: str,
        datasource_id: str,
        user_id: str,
        request_id: str,
    ) -> ExecutionResult:
        """
        执行 SQL 并返回结果。
        - 自动注入 LIMIT 防止超量返回
        - 超时自动终止
        - 执行前注入行级权限过滤（row_filter）
        """

class ResultFormatter:
    """根据结果形态自动选择展示类型"""

    def format(self, result: ExecutionResult, question: str) -> FormattedResult:
        """
        展示类型选择逻辑：
        - 单值（1行1列）→ single_value
        - ≤20行          → table
        - 含时间序列列    → line_chart
        - 含分类+数值     → bar_chart（≤10类）
        - 含占比          → pie_chart
        - 其他            → table
        """

@dataclass
class FormattedResult:
    display_type: Literal["single_value", "table", "bar_chart",
                          "line_chart", "pie_chart"]
    data: dict[str, Any]       # ECharts option 或 table rows
    summary_text: str          # 自然语言摘要（由 NLG 模块填充）
    data_sources: list[DataSourceInfo]   # 数据来源与时效信息
    row_count: int
    execution_ms: int
```

---

### 2.6 LLM 调用层（llm_router）

**文件位置：** `backend/app/llm/`

```python
# base.py
class BaseLLMClient(ABC):
    @abstractmethod
    async def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 1000,
        temperature: float = 0.1,   # SQL 生成用低温度
    ) -> LLMResponse:
        ...

# router.py
class LLMRouter:
    """
    模型路由器，实现降级策略。

    降级链：qianwen-max → qianwen-plus → wenxin-4.0
    超时：15秒/次
    重试：指数退避 1s, 2s, 4s，最多3次
    """
    FALLBACK_CHAIN = ["qianwen-max", "qianwen-plus", "wenxin-4.0"]

    async def complete(self, prompt: str, **kwargs) -> LLMResponse:
        for model in self.FALLBACK_CHAIN:
            try:
                client = self._get_client(model)
                return await asyncio.wait_for(
                    client.complete(prompt, **kwargs),
                    timeout=15.0
                )
            except (asyncio.TimeoutError, LLMAPIError) as e:
                logger.warning("LLM模型调用失败，尝试降级", model=model, error=str(e))
                continue
        raise LLMAllFallbackExhaustedError("所有模型均不可用")
```

---

### 2.7 知识库与向量检索（knowledge_base）

**文件位置：** `backend/app/knowledge/`

```python
# embedding.py
class EmbeddingService:
    """文本向量化服务"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """调用 text-embedding-v3 API，批量向量化"""

    async def embed_single(self, text: str) -> list[float]:
        """单条向量化，带 Redis 缓存（TTL 24小时）"""

# dictionary.py
class DataDictionaryManager:
    """数据字典管理：字段业务含义、同义词、单位"""

    async def get_schema_context(
        self,
        question: str,
        top_k: int = 5,
    ) -> SchemaContext:
        """
        语义检索最相关的表和字段：
        1. 对 question 向量化
        2. 在 ChromaDB 中检索 top_k 个最相似的字段描述
        3. 返回包含表结构、字段说明、示例值的上下文
        """

# cache.py
class QASemanticCache:
    """
    语义缓存：相似问题复用已有结果，减少 LLM 调用。
    相似度阈值：0.92
    TTL：5分钟（数据可能更新）
    """

    async def get(self, question: str) -> CachedResult | None:
        ...

    async def set(self, question: str, result: FormattedResult) -> None:
        ...
```

---

### 2.8 权限与审计（security）

**文件位置：** `backend/app/security/`

#### RBAC 权限模型

```python
# 角色定义
class Role(str, Enum):
    ADMIN        = "admin"         # 管理员：所有权限
    DATA_MANAGER = "data_manager"  # 数据维护员：上传/管理 Excel
    ANALYST      = "analyst"       # 分析师：查询所有数据域
    FINANCE_USER = "finance_user"  # 财务用户：只能查财务域
    SALES_USER   = "sales_user"    # 销售用户：只能查销售域
    PROD_USER    = "production_user" # 生产用户：只能查生产域
    PROC_USER    = "procurement_user" # 采购用户：只能查采购域

# 权限矩阵
ROLE_DOMAIN_PERMISSION: dict[Role, set[str]] = {
    Role.ADMIN:        {"finance", "sales", "production", "procurement"},
    Role.ANALYST:      {"finance", "sales", "production", "procurement"},
    Role.DATA_MANAGER: {"finance", "sales", "production", "procurement"},
    Role.FINANCE_USER: {"finance"},
    Role.SALES_USER:   {"sales"},
    Role.PROD_USER:    {"production"},
    Role.PROC_USER:    {"procurement"},
}
```

#### 行级权限注入

```python
# row_filter.py
class RowLevelFilter:
    """
    在 SQL 执行前自动注入行级权限过滤条件。
    当前第一期实现：基于数据域隔离，每个用户只能查其有权限的数据域表。
    第二期可扩展为：基于部门、区域等更细粒度的行过滤。
    """

    def inject(
        self,
        sql: str,
        user_id: str,
        allowed_tables: set[str],
    ) -> str:
        """
        校验 SQL 中引用的表是否全部在 allowed_tables 中。
        如有越权访问，抛出 DataPermissionError。
        """
```

#### 审计日志

```python
# audit.py
@dataclass
class AuditRecord:
    id: str                     # UUID
    request_id: str
    user_id: str
    user_name: str
    ip_address: str
    question: str               # 用户原始问题
    generated_sql: str          # 生成的 SQL（不含参数值）
    tables_accessed: list[str]
    result_row_count: int
    execution_ms: int
    status: Literal["success", "failed", "blocked"]
    block_reason: str | None    # 被拦截时的原因
    llm_model_used: str
    prompt_tokens: int
    completion_tokens: int
    timestamp: datetime

# 审计记录必须异步写入，不阻塞主流程
async def record_audit(record: AuditRecord) -> None:
    await audit_repository.insert(record)
```

---

### 2.9 对话管理（conversation）

**文件位置：** `backend/app/core/conversation.py`

```python
class ConversationManager:
    """
    多轮对话上下文管理。
    存储于 Redis，TTL = 2小时。
    最多保留最近 10 轮（滑动窗口）。
    """

    MAX_TURNS: int = 10
    TTL_SECONDS: int = 7200

    async def get_context(self, conversation_id: str) -> ConversationContext:
        ...

    async def append_turn(
        self,
        conversation_id: str,
        question: str,
        sql: str,
        answer_summary: str,
    ) -> None:
        ...

    def build_contextual_question(
        self,
        question: str,
        context: ConversationContext,
    ) -> str:
        """
        将当前问题结合历史上下文改写为完整问题。
        例：上一问"查本月销售收入"，当前问"和上月比"
        → 改写为"查本月销售收入和上月的对比"
        """
```

---

### 2.10 REST API 接口设计

**文件位置：** `backend/app/api/v1/`

#### 对话查询接口

```
POST /api/v1/chat/query
Authorization: Bearer <token>

Request:
{
  "question": "查询本月各产品线的销售收入",
  "conversation_id": "uuid",     // 可选，多轮对话用
  "datasource_ids": ["ds_001"]   // 可选，指定数据源
}

Response (成功):
{
  "code": 0,
  "message": "ok",
  "request_id": "uuid",
  "data": {
    "answer_text": "本月各产品线销售收入如下...",
    "display_type": "bar_chart",
    "chart_option": { /* ECharts option */ },
    "table_data": null,
    "sql": "SELECT product_line, SUM(revenue) FROM ...",  // debug模式下返回
    "data_sources": [
      {
        "datasource_name": "销售部_月度台账_202603",
        "data_date": "2026-03-15",
        "upload_time": "2026-03-18T09:00:00"
      }
    ],
    "confidence": 0.92,
    "execution_ms": 1230
  }
}

Response (SQL生成失败):
{
  "code": 1001,
  "message": "无法理解您的问题，请尝试换一种表达方式",
  "request_id": "uuid",
  "data": {
    "suggestion": "您可以尝试：'查询2026年3月各产品线的销售金额'"
  }
}
```

#### Excel 数据源管理接口

```
POST /api/v1/datasource/upload
Content-Type: multipart/form-data
Authorization: Bearer <token> (role: data_manager / admin)

Request: form-data
  file: <binary>
  domain: "sales"                    // finance/sales/production/procurement
  data_date: "2026-03-15"            // 数据截止日期
  description: "3月销售台账"
  update_mode: "replace"             // replace / append

Response (成功，待确认映射):
{
  "code": 0,
  "data": {
    "upload_id": "upload_uuid",
    "status": "pending_confirm",
    "preview": {
      "total_rows": 1250,
      "sheets": [...],
      "field_mappings": [
        {
          "raw_name": "销售额（万元）",
          "std_name": "revenue",
          "display_name": "销售额",
          "confidence": 0.96,
          "needs_confirm": false
        },
        {
          "raw_name": "本月回款",
          "std_name": "payment_received",
          "confidence": 0.71,
          "needs_confirm": true    // 需要人工确认
        }
      ]
    }
  }
}

POST /api/v1/datasource/confirm/{upload_id}
// 人工确认字段映射后提交，触发数据入库

GET  /api/v1/datasource/list
GET  /api/v1/datasource/{datasource_id}
DELETE /api/v1/datasource/{datasource_id}
```

#### 管理后台接口

```
GET  /api/v1/admin/stats/accuracy     // SQL 准确率统计
GET  /api/v1/admin/stats/usage        // 使用量统计
GET  /api/v1/admin/audit/logs         // 审计日志查询
GET  /api/v1/admin/users              // 用户管理
POST /api/v1/admin/users              // 新增用户
PATCH /api/v1/admin/users/{user_id}/role  // 修改角色
GET  /api/v1/admin/datasource/stale   // 超期未更新的数据源列表
```

---

## 三、数据库设计

### 3.1 元数据库（PostgreSQL）

```sql
-- 用户表
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username    VARCHAR(64) UNIQUE NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    email       VARCHAR(256),
    role        VARCHAR(32) NOT NULL,           -- 对应 Role 枚举
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 数据源注册表（Excel 导入元信息）
CREATE TABLE datasources (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(256) NOT NULL,       -- 文件名
    domain          VARCHAR(32) NOT NULL,        -- finance/sales/production/procurement
    description     VARCHAR(512),
    original_filename VARCHAR(256) NOT NULL,
    file_path       VARCHAR(512) NOT NULL,       -- 服务器存储路径
    file_size_bytes BIGINT,
    data_date       DATE NOT NULL,               -- 数据截止日期
    update_mode     VARCHAR(16),                 -- replace/append
    status          VARCHAR(32) DEFAULT 'active',-- active/archived/error
    biz_table_name  VARCHAR(128),                -- 对应业务数据库的表名
    total_rows      INTEGER,
    uploaded_by     UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_datasources_domain ON datasources(domain);
CREATE INDEX idx_datasources_data_date ON datasources(data_date DESC);

-- 字段映射表（每个数据源的表头→标准字段映射）
CREATE TABLE field_mappings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    datasource_id   UUID NOT NULL REFERENCES datasources(id) ON DELETE CASCADE,
    raw_name        VARCHAR(256) NOT NULL,       -- 原始表头
    std_name        VARCHAR(128) NOT NULL,       -- 标准字段名
    display_name    VARCHAR(256) NOT NULL,       -- 中文展示名
    field_type      VARCHAR(32) NOT NULL,
    unit            VARCHAR(64),
    confidence      FLOAT,
    mapping_source  VARCHAR(32),                 -- exact_match/embedding/llm/manual
    confirmed_by    UUID REFERENCES users(id),   -- 人工确认人
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 数据字典（跨数据源的字段语义知识库）
CREATE TABLE data_dictionary (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    std_name    VARCHAR(128) UNIQUE NOT NULL,
    display_name VARCHAR(256) NOT NULL,
    domain      VARCHAR(32),
    description TEXT,
    synonyms    TEXT[],                          -- 同义词列表
    unit        VARCHAR(64),
    example_values TEXT[],
    embedding   FLOAT[],                         -- 字段描述向量（可选，主要存于 ChromaDB）
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 查询审计日志
CREATE TABLE audit_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id      UUID NOT NULL,
    user_id         UUID NOT NULL REFERENCES users(id),
    ip_address      INET,
    question        TEXT NOT NULL,
    generated_sql   TEXT,
    tables_accessed TEXT[],
    result_row_count INTEGER,
    execution_ms    INTEGER,
    status          VARCHAR(16) NOT NULL,        -- success/failed/blocked
    block_reason    TEXT,
    llm_model_used  VARCHAR(64),
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    feedback        SMALLINT,                    -- NULL/1(点赞)/-1(点踩)
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_audit_user ON audit_logs(user_id, created_at DESC);
CREATE INDEX idx_audit_status ON audit_logs(status, created_at DESC);

-- 会话历史（持久化存储，Redis 为热缓存）
CREATE TABLE conversation_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL,
    turn_index      SMALLINT NOT NULL,
    question        TEXT NOT NULL,
    generated_sql   TEXT,
    answer_summary  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_conv_id ON conversation_history(conversation_id, turn_index);

-- Few-shot 问答示例库
CREATE TABLE few_shot_examples (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain      VARCHAR(32) NOT NULL,
    question    TEXT NOT NULL,
    sql         TEXT NOT NULL,
    description TEXT,
    is_active   BOOLEAN DEFAULT TRUE,
    difficulty  VARCHAR(16),                     -- easy/medium/hard
    source      VARCHAR(32),                     -- manual/feedback
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

### 3.2 业务数据库（PostgreSQL，独立库）

```sql
-- 所有 Excel 导入的业务数据均以动态表存储
-- 表命名规则：{domain}_{datasource_uuid_short}
-- 例：sales_a3f2b1c0、finance_8d7e6f5a

-- 每张业务表都自动附加以下元数据字段
-- （在 DataCleaner 写入时由系统自动添加）:
--   _row_id      BIGSERIAL        行号
--   _datasource_id UUID           来源数据源 ID
--   _import_time TIMESTAMPTZ      导入时间
--   _data_hash   VARCHAR(64)      行数据 MD5（追加去重用）

-- 示例：销售台账表（动态生成）
CREATE TABLE sales_a3f2b1c0 (
    _row_id         BIGSERIAL,
    _datasource_id  UUID,
    _import_time    TIMESTAMPTZ,
    _data_hash      VARCHAR(64),
    -- 以下为 Excel 字段（由 field_mappings 决定）
    report_month    DATE,
    product_line    VARCHAR(64),
    product_name    VARCHAR(128),
    revenue         NUMERIC(18,4),   -- 销售额（万元）
    sales_volume    NUMERIC(18,4),   -- 销售量（吨）
    unit_price      NUMERIC(18,4)    -- 单价（元/吨）
);
```

---

## 四、接口规范

### 4.1 统一响应格式

```typescript
interface ApiResponse<T> {
  code: number;         // 0=成功，非0=业务错误
  message: string;
  request_id: string;   // UUID，用于日志追踪
  data: T | null;
  timestamp: string;    // ISO8601
}
```

### 4.2 错误码定义

| 错误码 | 含义 | HTTP状态码 |
|-------|------|-----------|
| 0 | 成功 | 200 |
| 1001 | SQL 生成失败（无法理解问题）| 200 |
| 1002 | SQL 安全校验不通过 | 200 |
| 1003 | 数据域权限不足 | 200 |
| 1004 | 查询超时 | 200 |
| 1005 | LLM API 全部不可用 | 200 |
| 1010 | Excel 文件格式错误 | 200 |
| 1011 | Excel 文件超过大小限制 | 200 |
| 1012 | Excel 解析失败 | 200 |
| 4001 | 未认证（Token 无效或过期）| 401 |
| 4003 | 无权限执行此操作 | 403 |
| 4004 | 资源不存在 | 404 |
| 5000 | 系统内部错误 | 500 |

### 4.3 分页规范

```
GET /api/v1/admin/audit/logs?page=1&page_size=20&status=success

Response:
{
  "code": 0,
  "data": {
    "items": [...],
    "total": 1500,
    "page": 1,
    "page_size": 20,
    "total_pages": 75
  }
}
```

---

## 五、单元测试规范与用例

### 5.1 测试工具栈

```
pytest 8.x                    # 测试框架
pytest-asyncio               # 异步测试支持
pytest-cov                   # 覆盖率统计
pytest-mock                  # Mock 工具
factory-boy                  # 测试数据工厂
faker                        # 随机数据生成
httpx[AsyncClient]           # API 测试客户端
```

### 5.2 覆盖率要求

| 模块 | 最低覆盖率 | 说明 |
|------|----------|------|
| `security/sql_validator.py` | **100%** | 安全核心，必须全覆盖 |
| `security/rbac.py` | **100%** | 权限核心，必须全覆盖 |
| `security/row_filter.py` | **100%** | 安全核心，必须全覆盖 |
| `core/excel_parser.py` | ≥ 90% | 解析引擎核心逻辑 |
| `core/data_cleaner.py` | ≥ 90% | 数据质量核心 |
| `core/text_to_sql.py` | ≥ 85% | 含 LLM 调用 Mock |
| `core/sql_executor.py` | ≥ 85% | 含数据库 Mock |
| `core/result_formatter.py` | ≥ 85% | 展示逻辑 |
| `llm/router.py` | ≥ 85% | 降级逻辑 |
| `knowledge/embedding.py` | ≥ 80% | |
| 其余模块 | ≥ 75% | |

### 5.3 测试文件结构

```
tests/
├── conftest.py                    # 全局 Fixtures
├── factories.py                   # 测试数据工厂（factory-boy）
├── fixtures/
│   ├── excel/
│   │   ├── standard_sales.xlsx    # 标准格式销售数据
│   │   ├── merged_cells.xlsx      # 含合并单元格
│   │   ├── multi_header.xlsx      # 多级表头
│   │   ├── mixed_types.xlsx       # 混合类型字段
│   │   ├── large_file.xlsx        # 大文件（>10万行）
│   │   ├── bad_encoding.csv       # 编码问题
│   │   └── empty_rows.xlsx        # 含空行
│   └── sql/
│       ├── valid_queries.sql
│       └── malicious_queries.sql
├── unit/
│   ├── test_excel_parser.py
│   ├── test_data_cleaner.py
│   ├── test_field_mapper.py
│   ├── test_text_to_sql.py
│   ├── test_sql_validator.py
│   ├── test_sql_executor.py
│   ├── test_result_formatter.py
│   ├── test_llm_router.py
│   ├── test_conversation.py
│   ├── test_rbac.py
│   ├── test_row_filter.py
│   └── test_audit.py
├── integration/
│   ├── test_api_chat.py
│   ├── test_api_datasource.py
│   ├── test_api_admin.py
│   ├── test_api_auth.py
│   └── test_full_query_flow.py
└── accuracy/
    ├── conftest_accuracy.py
    ├── test_finance_accuracy.py
    ├── test_sales_accuracy.py
    ├── test_production_accuracy.py
    ├── test_procurement_accuracy.py
    └── cases/
        ├── finance_cases.yaml      # ≥50条
        ├── sales_cases.yaml        # ≥50条
        ├── production_cases.yaml   # ≥50条
        └── procurement_cases.yaml  # ≥50条
```

### 5.4 单元测试详细用例

#### 5.4.1 SQL 安全校验测试（必须 100% 通过）

```python
# tests/unit/test_sql_validator.py

import pytest
from app.security.sql_validator import SQLValidator, ValidationResult

@pytest.fixture
def validator():
    return SQLValidator()

@pytest.fixture
def allowed_tables():
    return {"sales_a3f2b1c0", "finance_8d7e6f5a", "production_b2c3d4e5"}


class TestForbiddenPatterns:
    """测试危险 SQL 关键字拦截"""

    @pytest.mark.parametrize("sql,expected_violation", [
        ("DROP TABLE users",                    "DROP"),
        ("TRUNCATE TABLE sales_a3f2b1c0",       "TRUNCATE"),
        ("DELETE FROM finance_8d7e6f5a",        "DELETE"),
        ("UPDATE sales_a3f2b1c0 SET revenue=0", "UPDATE"),
        ("INSERT INTO sales_a3f2b1c0 VALUES(1)","INSERT"),
        ("CREATE TABLE evil AS SELECT 1",       "CREATE"),
        ("ALTER TABLE users ADD COLUMN x INT",  "ALTER"),
        ("EXEC xp_cmdshell('rm -rf /')",        "EXEC"),
        ("SELECT 1; DROP TABLE users",          "多语句"),
        ("SELECT 1 -- comment\nDROP TABLE t",   "注释注入"),
        ("SELECT * INTO OUTFILE '/etc/passwd'", "OUTFILE"),
        ("SELECT LOAD_FILE('/etc/passwd')",     "LOAD_FILE"),
    ])
    def test_blocks_dangerous_sql(
        self, validator, allowed_tables, sql, expected_violation
    ):
        result = validator.validate(sql, allowed_tables)
        assert result.is_safe is False, \
            f"危险 SQL 未被拦截: {sql}"
        assert len(result.violations) > 0


class TestValidSelectSQL:
    """测试合法 SELECT 语句通过校验"""

    @pytest.mark.parametrize("sql", [
        "SELECT * FROM sales_a3f2b1c0",
        "SELECT product_line, SUM(revenue) FROM sales_a3f2b1c0 GROUP BY product_line",
        "SELECT a.product_name, b.cost FROM sales_a3f2b1c0 a "
        "JOIN finance_8d7e6f5a b ON a.product_code = b.product_code",
        "SELECT * FROM sales_a3f2b1c0 WHERE report_month >= '2026-01-01' "
        "ORDER BY revenue DESC LIMIT 10",
        "WITH cte AS (SELECT * FROM sales_a3f2b1c0) SELECT * FROM cte",
    ])
    def test_allows_valid_select(self, validator, allowed_tables, sql):
        result = validator.validate(sql, allowed_tables)
        assert result.is_safe is True, \
            f"合法 SQL 被错误拦截: {sql}, 违规: {result.violations}"


class TestTableWhitelist:
    """测试表名白名单校验"""

    def test_blocks_unlisted_table(self, validator, allowed_tables):
        sql = "SELECT * FROM secret_salary_table"
        result = validator.validate(sql, allowed_tables)
        assert result.is_safe is False
        assert any("secret_salary_table" in v for v in result.violations)

    def test_blocks_system_table(self, validator, allowed_tables):
        sql = "SELECT * FROM information_schema.tables"
        result = validator.validate(sql, allowed_tables)
        assert result.is_safe is False

    def test_allows_whitelisted_table(self, validator, allowed_tables):
        sql = "SELECT COUNT(*) FROM sales_a3f2b1c0"
        result = validator.validate(sql, allowed_tables)
        assert result.is_safe is True


class TestEdgeCases:
    """边界条件测试"""

    def test_empty_sql(self, validator, allowed_tables):
        result = validator.validate("", allowed_tables)
        assert result.is_safe is False

    def test_sql_with_only_whitespace(self, validator, allowed_tables):
        result = validator.validate("   \n\t  ", allowed_tables)
        assert result.is_safe is False

    def test_case_insensitive_detection(self, validator, allowed_tables):
        for sql in ["drop table x", "Drop Table x", "DROP TABLE x", "dRoP tAbLe x"]:
            result = validator.validate(sql, allowed_tables)
            assert result.is_safe is False, f"大小写变形未被拦截: {sql}"

    def test_unicode_obfuscation(self, validator, allowed_tables):
        """Unicode 混淆攻击"""
        sql = "SＥLECT * FROM sales_a3f2b1c0"  # 全角字符
        # 应拒绝或标准化后安全通过
        result = validator.validate(sql, allowed_tables)
        # 至少不应该因为绕过检测而允许危险操作
        assert isinstance(result, ValidationResult)
```

#### 5.4.2 Excel 解析测试

```python
# tests/unit/test_excel_parser.py

import pytest
from pathlib import Path
from app.core.excel_parser import ExcelParser, FieldType

FIXTURES = Path("tests/fixtures/excel")

@pytest.fixture
def parser():
    return ExcelParser()


class TestStandardFormat:
    """标准格式 Excel 解析"""

    async def test_parse_standard_sales_excel(self, parser):
        result = await parser.parse(str(FIXTURES / "standard_sales.xlsx"))
        assert result.is_valid is True
        assert len(result.sheets) == 1
        sheet = result.sheets[0]
        assert sheet.total_rows > 0
        assert len(sheet.fields) > 0
        assert len(result.parse_errors) == 0

    async def test_header_detection(self, parser):
        result = await parser.parse(str(FIXTURES / "standard_sales.xlsx"))
        sheet = result.sheets[0]
        assert sheet.header_row == 0   # 标准格式表头在第0行
        assert sheet.data_start_row == 1

    async def test_field_type_inference_numeric(self, parser):
        result = await parser.parse(str(FIXTURES / "standard_sales.xlsx"))
        revenue_field = next(
            f for f in result.sheets[0].fields
            if "销售额" in f.raw_name
        )
        assert revenue_field.inferred_type in (FieldType.FLOAT, FieldType.INTEGER)

    async def test_field_type_inference_date(self, parser):
        result = await parser.parse(str(FIXTURES / "standard_sales.xlsx"))
        date_field = next(
            (f for f in result.sheets[0].fields if "月份" in f.raw_name or "日期" in f.raw_name),
            None
        )
        if date_field:
            assert date_field.inferred_type in (FieldType.DATE, FieldType.DATETIME)

    async def test_unit_extraction(self, parser):
        result = await parser.parse(str(FIXTURES / "standard_sales.xlsx"))
        revenue_field = next(
            f for f in result.sheets[0].fields if "万元" in f.raw_name
        )
        assert revenue_field.has_unit_suffix is True
        assert "万元" in revenue_field.unit_hint


class TestNonStandardFormat:
    """非标格式处理"""

    async def test_merged_cells_expanded(self, parser):
        result = await parser.parse(str(FIXTURES / "merged_cells.xlsx"))
        # 合并单元格应被展开，不应报错
        assert result.is_valid is True
        assert any("合并单元格" in w for w in result.sheets[0].warnings)

    async def test_empty_rows_handled(self, parser):
        result = await parser.parse(str(FIXTURES / "empty_rows.xlsx"))
        sheet = result.sheets[0]
        # 中间空行不应中断解析
        assert result.is_valid is True

    async def test_mixed_types_warned(self, parser):
        result = await parser.parse(str(FIXTURES / "mixed_types.xlsx"))
        # 含混合类型字段应有告警
        assert len(result.sheets[0].warnings) > 0


class TestFileLimits:
    """文件限制测试"""

    async def test_rejects_unsupported_format(self, parser, tmp_path):
        bad_file = tmp_path / "data.docx"
        bad_file.write_bytes(b"fake docx content")
        with pytest.raises(Exception):  # UnsupportedFormatError
            await parser.parse(str(bad_file))

    async def test_rejects_oversized_file(self, parser, tmp_path):
        # 创建超过 50MB 的假文件
        big_file = tmp_path / "big.xlsx"
        big_file.write_bytes(b"x" * (51 * 1024 * 1024))
        with pytest.raises(Exception):  # FileTooLargeError
            await parser.parse(str(big_file))

    async def test_handles_empty_file(self, parser, tmp_path):
        empty_file = tmp_path / "empty.xlsx"
        empty_file.write_bytes(b"")
        result = await parser.parse(str(empty_file))
        assert result.is_valid is False
        assert len(result.parse_errors) > 0
```

#### 5.4.3 数据清洗测试

```python
# tests/unit/test_data_cleaner.py

import pandas as pd
import pytest
from app.core.data_cleaner import DataCleaner

@pytest.fixture
def cleaner():
    return DataCleaner()


class TestNumericCleaning:
    """数值字段清洗"""

    def test_strips_unit_suffix(self, cleaner):
        df = pd.DataFrame({"revenue": ["1250万元", "860.5万元", "0万元"]})
        result = cleaner._apply_strip_unit(df["revenue"])
        assert result.tolist() == [1250.0, 860.5, 0.0]

    def test_handles_comma_separator(self, cleaner):
        df = pd.DataFrame({"amount": ["1,250,000", "860,500"]})
        result = cleaner._apply_strip_unit(df["amount"])
        assert result.tolist() == [1250000.0, 860500.0]

    def test_keeps_null_as_null(self, cleaner):
        df = pd.DataFrame({"revenue": ["1250", None, "860"]})
        result = cleaner._apply_strip_unit(df["revenue"])
        assert pd.isna(result.iloc[1])


class TestDateCleaning:
    """日期字段标准化"""

    @pytest.mark.parametrize("raw,expected", [
        ("2026/01/15",  "2026-01-15"),
        ("2026年1月15日","2026-01-15"),
        ("2026.01.15",  "2026-01-15"),
        ("20260115",    "2026-01-15"),
        ("2026-01",     "2026-01-01"),  # 月份格式补全为月初
        ("2026年1月",   "2026-01-01"),
    ])
    def test_date_normalization(self, cleaner, raw, expected):
        series = pd.Series([raw])
        result = cleaner._normalize_date(series)
        assert str(result.iloc[0])[:10] == expected


class TestDeduplication:
    """追加模式去重"""

    def test_deduplicates_on_append(self, cleaner):
        existing = {"abc123", "def456"}
        df = pd.DataFrame({
            "_data_hash": ["abc123", "new789", "def456"]
        })
        result = cleaner._deduplicate(df, existing)
        assert len(result) == 1
        assert result.iloc[0]["_data_hash"] == "new789"


class TestBlankRowRemoval:
    """空行清理"""

    def test_removes_fully_blank_rows(self, cleaner):
        df = pd.DataFrame({
            "col1": ["a", None, "c"],
            "col2": ["x", None, "z"]
        })
        result = cleaner._remove_blank_rows(df)
        assert len(result) == 2

    def test_preserves_partial_rows(self, cleaner):
        df = pd.DataFrame({
            "col1": ["a", "b", None],
            "col2": ["x", None, None]
        })
        result = cleaner._remove_blank_rows(df)
        assert len(result) == 3  # 第3行 col1 有值，不应删除
```

#### 5.4.4 LLM 路由降级测试

```python
# tests/unit/test_llm_router.py

import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from app.llm.router import LLMRouter
from app.utils.exceptions import LLMAPIError, LLMAllFallbackExhaustedError

@pytest.fixture
def router():
    return LLMRouter()


class TestFallbackChain:
    """降级链路测试"""

    async def test_uses_primary_model_when_available(self, router):
        with patch.object(router, '_get_client') as mock_get:
            mock_client = AsyncMock()
            mock_client.complete.return_value = Mock(text="SELECT 1")
            mock_get.return_value = mock_client
            result = await router.complete("test prompt")
            assert result.text == "SELECT 1"
            # 验证首先调用了主力模型
            first_call_model = mock_get.call_args_list[0][0][0]
            assert first_call_model == "qianwen-max"

    async def test_falls_back_on_primary_failure(self, router):
        call_count = {"n": 0}
        models_tried = []

        async def mock_complete(prompt, **kwargs):
            model = models_tried[-1] if models_tried else "qianwen-max"
            if model in ("qianwen-max", "qianwen-plus"):
                raise LLMAPIError("服务不可用")
            return Mock(text="fallback result")

        with patch.object(router, '_get_client') as mock_get:
            def side_effect(model_name):
                models_tried.append(model_name)
                client = AsyncMock()
                client.complete = mock_complete
                return client
            mock_get.side_effect = side_effect
            result = await router.complete("test prompt")
            assert "wenxin" in models_tried[-1]

    async def test_raises_when_all_models_fail(self, router):
        with patch.object(router, '_get_client') as mock_get:
            mock_client = AsyncMock()
            mock_client.complete.side_effect = LLMAPIError("全部失败")
            mock_get.return_value = mock_client
            with pytest.raises(LLMAllFallbackExhaustedError):
                await router.complete("test prompt")

    async def test_timeout_triggers_fallback(self, router):
        models_tried = []

        async def slow_complete(prompt, **kwargs):
            model = models_tried[-1] if models_tried else ""
            if "qianwen-max" in model:
                await asyncio.sleep(20)  # 超过 15s 超时
            return Mock(text="fast result")

        with patch.object(router, '_get_client') as mock_get:
            def side_effect(model_name):
                models_tried.append(model_name)
                client = AsyncMock()
                client.complete = slow_complete
                return client
            mock_get.side_effect = side_effect
            result = await router.complete("test prompt")
            assert len(models_tried) >= 2  # 至少触发了一次降级
```

#### 5.4.5 结果格式化测试

```python
# tests/unit/test_result_formatter.py

import pytest
import pandas as pd
from app.core.result_formatter import ResultFormatter

@pytest.fixture
def formatter():
    return ResultFormatter()


class TestDisplayTypeSelection:
    """展示类型自动选择逻辑"""

    def test_single_value_for_1x1_result(self, formatter):
        df = pd.DataFrame({"total_revenue": [12500.0]})
        result = formatter.format(df, "本月总收入是多少")
        assert result.display_type == "single_value"

    def test_table_for_small_result(self, formatter):
        df = pd.DataFrame({
            "product": ["热轧", "冷轧", "H型钢"],
            "revenue": [1250, 860, 430]
        })
        result = formatter.format(df, "各产品收入")
        assert result.display_type in ("table", "bar_chart")

    def test_line_chart_for_time_series(self, formatter):
        df = pd.DataFrame({
            "month": pd.date_range("2026-01", periods=6, freq="MS"),
            "revenue": [1200, 1350, 1100, 1500, 1600, 1450]
        })
        result = formatter.format(df, "近半年月度收入趋势")
        assert result.display_type == "line_chart"

    def test_pie_chart_for_ratio_data(self, formatter):
        df = pd.DataFrame({
            "supplier": ["A供应商", "B供应商", "C供应商"],
            "ratio": [45.2, 33.1, 21.7]
        })
        result = formatter.format(df, "供应商占比")
        assert result.display_type == "pie_chart"

    def test_table_for_large_result(self, formatter):
        df = pd.DataFrame({
            "id": range(100),
            "value": range(100)
        })
        result = formatter.format(df, "查询明细")
        assert result.display_type == "table"
```

#### 5.4.6 权限控制测试

```python
# tests/unit/test_rbac.py

import pytest
from app.security.rbac import RBACChecker, Role
from app.utils.exceptions import DataPermissionError

@pytest.fixture
def rbac():
    return RBACChecker()


class TestDomainPermission:
    """数据域权限校验"""

    @pytest.mark.parametrize("role,domain,expected", [
        (Role.ADMIN,        "finance",      True),
        (Role.ADMIN,        "sales",        True),
        (Role.ANALYST,      "production",   True),
        (Role.FINANCE_USER, "finance",      True),
        (Role.FINANCE_USER, "sales",        False),  # 无权限
        (Role.SALES_USER,   "finance",      False),  # 无权限
        (Role.SALES_USER,   "sales",        True),
        (Role.PROD_USER,    "finance",      False),  # 无权限
        (Role.PROD_USER,    "production",   True),
    ])
    def test_domain_access(self, rbac, role, domain, expected):
        if expected:
            assert rbac.check_domain(role, domain) is True
        else:
            with pytest.raises(DataPermissionError):
                rbac.check_domain(role, domain)

    def test_data_manager_can_upload(self, rbac):
        assert rbac.check_permission(Role.DATA_MANAGER, "datasource:upload") is True

    def test_finance_user_cannot_upload(self, rbac):
        with pytest.raises(DataPermissionError):
            rbac.check_permission(Role.FINANCE_USER, "datasource:upload")
```

---

## 六、集成测试规范与用例

### 6.1 集成测试环境

```python
# tests/integration/conftest.py

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from app.main import app
from app.config import settings

# 集成测试使用独立的测试数据库
@pytest.fixture(scope="session")
def test_db_url():
    return "postgresql+asyncpg://test_user:test_pass@localhost:5433/ai_query_test"

@pytest.fixture(scope="session")
async def async_client():
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

@pytest.fixture
async def auth_headers_analyst(async_client):
    """分析师角色的认证 Token"""
    resp = await async_client.post("/api/v1/auth/login", json={
        "username": "test_analyst", "password": "test_password"
    })
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
async def auth_headers_admin(async_client):
    resp = await async_client.post("/api/v1/auth/login", json={
        "username": "test_admin", "password": "test_password"
    })
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}
```

### 6.2 API 集成测试用例

```python
# tests/integration/test_api_chat.py

class TestChatQueryAPI:
    """对话查询 API 集成测试"""

    async def test_simple_query_success(self, async_client, auth_headers_analyst,
                                        seeded_sales_data):
        """基础查询：有数据时应返回 code=0"""
        resp = await async_client.post(
            "/api/v1/chat/query",
            json={"question": "查询销售数据总行数"},
            headers=auth_headers_analyst,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["display_type"] == "single_value"
        assert data["request_id"] is not None

    async def test_query_without_auth_returns_401(self, async_client):
        resp = await async_client.post(
            "/api/v1/chat/query",
            json={"question": "查收入"}
        )
        assert resp.status_code == 401

    async def test_finance_user_cannot_query_sales(
        self, async_client, auth_headers_finance_user
    ):
        resp = await async_client.post(
            "/api/v1/chat/query",
            json={"question": "查询销售订单"},
            headers=auth_headers_finance_user,
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 1003  # 权限不足

    async def test_dangerous_question_blocked(self, async_client, auth_headers_analyst):
        resp = await async_client.post(
            "/api/v1/chat/query",
            json={"question": "删除所有销售数据"},
            headers=auth_headers_analyst,
        )
        data = resp.json()
        # 要么 SQL 生成阶段失败，要么校验阶段拦截
        assert data["code"] in (1001, 1002)

    async def test_multi_turn_conversation(self, async_client, auth_headers_analyst,
                                           seeded_sales_data):
        """多轮对话测试"""
        # 第一轮
        resp1 = await async_client.post(
            "/api/v1/chat/query",
            json={"question": "查本月销售总额"},
            headers=auth_headers_analyst,
        )
        conv_id = resp1.json()["data"].get("conversation_id")

        # 第二轮：基于上文的追问
        resp2 = await async_client.post(
            "/api/v1/chat/query",
            json={
                "question": "和上月比增长了多少",
                "conversation_id": conv_id,
            },
            headers=auth_headers_analyst,
        )
        assert resp2.json()["code"] == 0

    async def test_response_includes_data_source_info(
        self, async_client, auth_headers_analyst, seeded_sales_data
    ):
        """结果应包含数据来源和时效信息"""
        resp = await async_client.post(
            "/api/v1/chat/query",
            json={"question": "查询产品线分类"},
            headers=auth_headers_analyst,
        )
        data = resp.json()["data"]
        assert "data_sources" in data
        assert len(data["data_sources"]) > 0
        source = data["data_sources"][0]
        assert "datasource_name" in source
        assert "data_date" in source
        assert "upload_time" in source


# tests/integration/test_api_datasource.py

class TestDatasourceAPI:
    """Excel 数据源 API 集成测试"""

    async def test_upload_valid_excel(self, async_client, auth_headers_data_manager,
                                      sample_excel_file):
        resp = await async_client.post(
            "/api/v1/datasource/upload",
            files={"file": ("sales.xlsx", sample_excel_file, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"domain": "sales", "data_date": "2026-03-15"},
            headers=auth_headers_data_manager,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["status"] in ("pending_confirm", "success")

    async def test_upload_rejected_for_analyst_role(
        self, async_client, auth_headers_analyst, sample_excel_file
    ):
        resp = await async_client.post(
            "/api/v1/datasource/upload",
            files={"file": ("sales.xlsx", sample_excel_file, "application/vnd.ms-excel")},
            data={"domain": "sales", "data_date": "2026-03-15"},
            headers=auth_headers_analyst,
        )
        assert resp.status_code == 403

    async def test_upload_rejects_oversized_file(
        self, async_client, auth_headers_data_manager
    ):
        big_content = b"x" * (51 * 1024 * 1024)
        resp = await async_client.post(
            "/api/v1/datasource/upload",
            files={"file": ("big.xlsx", big_content, "application/vnd.ms-excel")},
            data={"domain": "sales", "data_date": "2026-03-15"},
            headers=auth_headers_data_manager,
        )
        assert resp.json()["code"] == 1011

    async def test_full_upload_confirm_query_flow(
        self, async_client, auth_headers_data_manager, auth_headers_analyst,
        sample_excel_bytes
    ):
        """完整流程：上传→确认映射→查询"""
        # 1. 上传
        upload_resp = await async_client.post(
            "/api/v1/datasource/upload",
            files={"file": ("test.xlsx", sample_excel_bytes, "application/vnd.ms-excel")},
            data={"domain": "sales", "data_date": "2026-03-01"},
            headers=auth_headers_data_manager,
        )
        upload_id = upload_resp.json()["data"]["upload_id"]

        # 2. 确认映射（不修改，直接确认）
        confirm_resp = await async_client.post(
            f"/api/v1/datasource/confirm/{upload_id}",
            json={"confirmed_mappings": []},
            headers=auth_headers_data_manager,
        )
        assert confirm_resp.json()["code"] == 0

        # 3. 查询
        query_resp = await async_client.post(
            "/api/v1/chat/query",
            json={"question": "查刚刚上传的数据有多少行"},
            headers=auth_headers_analyst,
        )
        assert query_resp.json()["code"] == 0
```

---

## 七、全面测试计划

### 7.1 测试阶段总览

```mermaid
gantt
    title 测试计划（与开发并行）
    dateFormat  YYYY-MM-DD
    axisFormat  %m/%d

    section 开发阶段同步测试
    单元测试编写（随开发同步）      :active, t1, 2026-04-10, 35d
    代码 Review + 静态分析          :active, t2, 2026-04-10, 35d

    section 集成测试阶段
    测试环境搭建与数据准备          :t3, 2026-04-27, 5d
    API 接口集成测试                :t4, 2026-05-01, 7d
    全流程端到端测试                :t5, 2026-05-04, 5d

    section 专项测试阶段
    SQL 准确率测试                  :t6, 2026-05-07, 7d
    安全渗透测试                    :t7, 2026-05-07, 5d
    性能压力测试                    :t8, 2026-05-09, 5d

    section 验收测试阶段
    用户 UAT 验收测试               :t9, 2026-05-11, 7d
    缺陷修复与回归测试              :t10, 2026-05-18, 5d
    正式上线                        :milestone, m1, 2026-05-25, 1d
```

### 7.2 测试类型与职责矩阵

| 测试类型 | 执行时机 | 负责人 | 通过标准 | 工具 |
|---------|---------|------|---------|------|
| 单元测试 | 每次 commit（CI）| 开发人员 | 覆盖率达标，0失败 | pytest + pytest-cov |
| 代码静态分析 | 每次 commit（CI）| 开发人员 | ruff + mypy 0 error | ruff / mypy |
| 接口集成测试 | 每天构建（CI）| 测试工程师 | 所有用例通过 | pytest + httpx |
| SQL 准确率测试 | 每周 & 里程碑前 | 算法工程师 | ≥ 85%（上线前）| 自动化脚本 |
| 安全测试 | 里程碑 M2 后 | 测试工程师 | 0 高危漏洞 | 手工 + OWASP ZAP |
| 性能压力测试 | 里程碑 M3 前 | 测试工程师 | 满足 SLA 指标 | Locust |
| UAT 验收测试 | 5月11~17日 | 业务用户 | 关键场景通过率 ≥ 90% | 人工 |
| 回归测试 | 每次修复后 | 测试工程师 | 全量用例通过 | pytest |

### 7.3 SQL 准确率测试计划

#### 测试用例设计（≥ 200 条）

```yaml
# tests/accuracy/cases/finance_cases.yaml（示例）

- id: fin_001
  domain: finance
  difficulty: easy
  question: "本月销售总收入是多少？"
  expected_tables: [finance_monthly_revenue]
  expected_keywords: ["SUM", "revenue"]
  expected_result_type: single_value
  tags: [aggregation, simple]

- id: fin_002
  difficulty: medium
  question: "今年一季度各产品线的收入占比"
  expected_tables: [finance_monthly_revenue]
  expected_keywords: ["SUM", "GROUP BY", "product_line", "2026"]
  expected_result_type: pie_chart
  tags: [aggregation, groupby, date_filter]

- id: fin_003
  difficulty: hard
  question: "对比去年同期，今年各月毛利率的变化趋势"
  expected_tables: [finance_monthly_revenue, finance_monthly_cost]
  expected_keywords: ["JOIN", "GROUP BY", "month", "gross_profit"]
  expected_result_type: line_chart
  tags: [multi_table, yoy, trend]

- id: fin_004
  difficulty: medium
  question: "应收账款超过90天未回款的客户有哪些？"
  expected_tables: [finance_receivable]
  expected_keywords: ["WHERE", "days", "90"]
  expected_result_type: table
  tags: [filter, business_logic]

# tests/accuracy/cases/cross_domain_cases.yaml

- id: cross_001
  difficulty: hard
  question: "本月各产品的销售收入和上月生产成本对比，毛利率最高的前5个产品"
  expected_tables: [sales_orders, production_cost]
  expected_keywords: ["JOIN", "product_code", "GROUP BY", "ORDER BY", "LIMIT 5"]
  expected_result_type: table
  tags: [cross_domain, multi_table, ranking]
```

#### 准确率评估脚本

```python
# tests/accuracy/conftest_accuracy.py

class AccuracyEvaluator:
    """SQL 准确率自动化评估"""

    async def evaluate_case(
        self,
        case: dict,
        engine: TextToSQLEngine,
    ) -> EvaluationResult:
        """
        评估单个测试用例：
        1. 调用引擎生成 SQL
        2. 检查 expected_tables 是否都被引用
        3. 检查 expected_keywords 是否都存在
        4. 执行 SQL，检查结果格式是否符合 expected_result_type
        5. 综合打分：keyword_hit_rate + table_hit_rate + execution_success
        """

    def compute_accuracy(self, results: list[EvaluationResult]) -> AccuracyReport:
        """
        计算各维度准确率并生成报告：
        - 总体准确率
        - 按难度分组准确率（easy/medium/hard）
        - 按数据域分组准确率
        - 执行成功率（生成的 SQL 能执行不报错）
        - 结果类型匹配率
        """
```

#### 准确率验收标准

| 测试阶段 | 总体准确率 | 简单问题 | 中等问题 | 复杂问题 | SQL执行成功率 |
|---------|----------|---------|---------|---------|------------|
| M1（4月20日）| ≥ 70% | ≥ 85% | ≥ 65% | ≥ 50% | ≥ 90% |
| M2（5月07日）| ≥ 82% | ≥ 92% | ≥ 78% | ≥ 60% | ≥ 93% |
| M3 UAT（5月18日）| ≥ 85% | ≥ 95% | ≥ 82% | ≥ 65% | ≥ 95% |
| 上线后目标 | ≥ 92% | ≥ 98% | ≥ 90% | ≥ 75% | ≥ 98% |

### 7.4 安全测试计划

#### 7.4.1 SQL 注入测试用例

```python
# tests/security/test_sql_injection.py

INJECTION_TEST_CASES = [
    # 经典 SQL 注入
    {"question": "查询产品 ' OR '1'='1",               "expected": "blocked"},
    {"question": "查询产品; DROP TABLE users--",        "expected": "blocked"},
    {"question": "查询产品 UNION SELECT password FROM users", "expected": "blocked"},
    # 编码绕过
    {"question": "查询产品 %27 OR %271%27%3D%271",     "expected": "blocked"},
    # 二阶注入（将恶意内容存入后执行）
    {"question": "产品名为 test'; DELETE FROM sales--", "expected": "blocked"},
    # 时间盲注
    {"question": "查询 id=1 AND SLEEP(5)",              "expected": "blocked"},
    # 注释绕过
    {"question": "查询产品 /**/OR/**/1=1",              "expected": "blocked"},
    # 合法查询（不应误报）
    {"question": "查询产品名包含'热轧'的记录",           "expected": "allowed"},
    {"question": "查询收入大于100万元的产品",            "expected": "allowed"},
]
```

#### 7.4.2 权限越权测试

```python
PERMISSION_BYPASS_CASES = [
    # 财务用户尝试查销售数据
    {
        "user_role": "finance_user",
        "question": "查询销售部的订单数量",
        "expected_code": 1003,
    },
    # 普通用户尝试访问管理接口
    {
        "user_role": "analyst",
        "endpoint": "GET /api/v1/admin/audit/logs",
        "expected_http_status": 403,
    },
    # 数据维护员尝试删除审计日志
    {
        "user_role": "data_manager",
        "endpoint": "DELETE /api/v1/admin/audit/logs",
        "expected_http_status": 403,
    },
]
```

#### 7.4.3 数据泄露测试

```python
# 验证 LLM API 调用时不发送真实数据
async def test_llm_api_payload_contains_no_real_data(mock_llm_client):
    """
    拦截 LLM API 请求，检查 payload 中不包含：
    - 真实的金额数值（如 1250.50）
    - 真实的客户名称
    - 数据库连接信息
    """
    captured_requests = []
    mock_llm_client.capture_requests(captured_requests)

    await engine.generate("查询本月收入")

    for req in captured_requests:
        payload_str = json.dumps(req.payload)
        # 验证不含真实数值
        assert "1250.50" not in payload_str
        assert "热轧卷板" not in payload_str   # 真实产品名
        assert "postgresql://" not in payload_str
```

### 7.5 性能压力测试计划

#### 性能 SLA 指标（上线前必须达标）

| 指标 | 目标值 | 测试方法 |
|------|-------|---------|
| 简单查询 P95 响应时间 | ≤ 8 秒 | Locust 并发 20 用户 |
| 复杂查询（多表 JOIN）P95 响应时间 | ≤ 15 秒 | Locust 并发 10 用户 |
| Excel 上传（5MB）响应时间 | ≤ 10 秒 | 单并发 |
| 系统并发用户数 | ≥ 30 同时在线 | Locust 阶梯加压 |
| CPU 使用率（峰值）| ≤ 70% | Locust + 系统监控 |
| 内存使用（稳态）| ≤ 4GB | 持续运行 1 小时监控 |

#### Locust 测试脚本

```python
# tests/performance/locustfile.py

from locust import HttpUser, task, between

class AIQueryUser(HttpUser):
    wait_time = between(3, 8)  # 用户思考时间

    def on_start(self):
        resp = self.client.post("/api/v1/auth/login", json={
            "username": "perf_test_user",
            "password": "perf_test_pass"
        })
        self.token = resp.json()["data"]["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    @task(5)
    def simple_query(self):
        """高频：简单聚合查询（权重5）"""
        self.client.post(
            "/api/v1/chat/query",
            json={"question": "查询本月销售总额"},
            headers=self.headers,
        )

    @task(3)
    def medium_query(self):
        """中频：分组统计查询（权重3）"""
        self.client.post(
            "/api/v1/chat/query",
            json={"question": "各产品线本月收入排名"},
            headers=self.headers,
        )

    @task(1)
    def complex_query(self):
        """低频：跨表关联查询（权重1）"""
        self.client.post(
            "/api/v1/chat/query",
            json={"question": "本月各产品销售收入与生产成本对比"},
            headers=self.headers,
        )

    @task(2)
    def get_history(self):
        """查询历史记录（权重2）"""
        self.client.get("/api/v1/chat/history", headers=self.headers)
```

### 7.6 UAT 验收测试计划

#### 验收用户与场景矩阵

| 验收用户 | 职位 | 验收场景 | 通过标准 |
|---------|------|---------|---------|
| 财务主管 | 财务部 | 月度收入查询、同比分析、应收账款分析 | 核心查询准确率 ≥ 90%，5分钟内可完成查询 |
| 销售经理 | 销售部 | 订单查询、客户回款、产品线业绩 | 核心查询准确率 ≥ 90% |
| 生产调度 | 生产部 | 日产量查询、计划偏差分析、质量指标 | 核心查询准确率 ≥ 85% |
| 采购专员 | 采购部 | 价格对比、供应商分析、采购台账查询 | 核心查询准确率 ≥ 85% |
| 数据维护员 | IT部 | Excel 上传、字段映射确认、版本管理 | 操作流程无障碍完成 |
| 管理层用户 | 集团 | 综合数据查询、跨域分析 | 能独立提问并理解答案 |

#### UAT 测试用例模板（关键场景）

```
场景编号: UAT-FIN-001
场景名称: 财务快速查询本月收入
前置条件: 财务部已上传本月收入 Excel，用户以财务角色登录
测试步骤:
  1. 在对话框输入："查一下今年1月到3月的收入，和去年同期比增长了多少"
  2. 等待系统返回结果
  3. 查看结果是否包含同比对比数据
  4. 查看数据来源标注是否正确
预期结果:
  - 30秒内返回结果
  - 结果包含2026年Q1和2025年Q1的收入数据
  - 显示同比增长率（数值和百分比）
  - 结果底部标注数据来源文件名和截止日期
  - 结果展示为折线图或对比表格
通过标准: 数据与财务部 Excel 核对误差 ≤ 0.1%
```

### 7.7 缺陷管理流程

```mermaid
flowchart LR
    FIND[发现缺陷] --> LOG[记录缺陷\n缺陷管理系统]
    LOG --> TRIAGE{缺陷分级}
    TRIAGE -- P0 致命 --> BLOCK[阻断开发\n立即修复]
    TRIAGE -- P1 严重 --> URGENT[24小时内修复]
    TRIAGE -- P2 一般 --> NORMAL[当前迭代修复]
    TRIAGE -- P3 轻微 --> BACKLOG[纳入 Backlog]
    BLOCK & URGENT & NORMAL --> FIX[开发修复]
    FIX --> VERIFY[测试验证]
    VERIFY -- 通过 --> CLOSE[关闭缺陷]
    VERIFY -- 不通过 --> FIX
```

#### 缺陷分级标准

| 级别 | 定义 | 示例 |
|-----|------|------|
| P0 致命 | 系统崩溃、数据安全漏洞、SQL 注入成功 | 用户可访问无权限数据；系统 500 崩溃 |
| P1 严重 | 核心功能不可用、准确率严重下降 | 查询接口无响应；Excel 上传全部失败 |
| P2 一般 | 功能部分异常、展示错误 | 图表不显示；分页错误；日期格式异常 |
| P3 轻微 | UI 美观问题、文案错误 | 按钮对齐问题；提示文字不准确 |

---

## 八、部署方案

### 8.1 Docker Compose 服务编排

```yaml
# docker-compose.prod.yml

version: '3.9'

services:
  nginx:
    image: nginx:1.25-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro
    depends_on: [backend, frontend]
    restart: always

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile.prod
    environment:
      - APP_ENV=production
    env_file: .env.prod
    volumes:
      - excel_files:/app/files
    depends_on:
      meta_db:
        condition: service_healthy
      biz_db:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: always
    deploy:
      replicas: 2                  # 双副本，提升可用性

  celery_worker:
    build:
      context: ./backend
      dockerfile: Dockerfile.prod
    command: celery -A app.worker worker --loglevel=info --concurrency=4
    env_file: .env.prod
    depends_on: [redis, meta_db]
    restart: always

  meta_db:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: ai_query_meta
      POSTGRES_USER: ${META_DB_USER}
      POSTGRES_PASSWORD: ${META_DB_PASSWORD}
    volumes:
      - meta_db_data:/var/lib/postgresql/data
      - ./scripts/init_meta.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${META_DB_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: always

  biz_db:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: ai_query_biz
      POSTGRES_USER: ${BIZ_DB_USER}
      POSTGRES_PASSWORD: ${BIZ_DB_PASSWORD}
    volumes:
      - biz_db_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${BIZ_DB_USER}"]
      interval: 10s
    restart: always

  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD} --appendonly yes
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
    restart: always

  chromadb:
    image: chromadb/chroma:latest
    volumes:
      - chroma_data:/chroma/chroma
    restart: always

volumes:
  meta_db_data:
  biz_db_data:
  redis_data:
  chroma_data:
  excel_files:
```

### 8.2 CI/CD 流水线

```yaml
# .github/workflows/ci.yml（或内网 Gitea Actions）

name: CI Pipeline

on:
  push:
    branches: [develop, feature/*, fix/*]
  pull_request:
    branches: [develop, main]

jobs:
  backend-test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_DB: ai_query_test
          POSTGRES_USER: test_user
          POSTGRES_PASSWORD: test_pass
        ports: ["5433:5432"]
      redis:
        image: redis:7
        ports: ["6380:6379"]

    steps:
      - uses: actions/checkout@v4

      - name: Setup Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          pip install -r backend/requirements.txt
          pip install -r backend/requirements-dev.txt

      - name: Lint (ruff)
        run: ruff check backend/app/

      - name: Type check (mypy)
        run: mypy backend/app/ --strict

      - name: Run unit tests with coverage
        run: |
          cd backend
          pytest tests/unit/ \
            --cov=app \
            --cov-report=xml \
            --cov-fail-under=80 \
            -v

      - name: Run integration tests
        run: |
          cd backend
          pytest tests/integration/ -v
        env:
          META_DB_URL: postgresql+asyncpg://test_user:test_pass@localhost:5433/ai_query_test
          REDIS_URL: redis://localhost:6380/0

      - name: Upload coverage report
        uses: codecov/codecov-action@v4

  frontend-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: cd frontend && npm ci
      - run: cd frontend && npm run type-check
      - run: cd frontend && npm run lint
      - run: cd frontend && npm run test:unit
```

---

## 九、开发环境搭建

### 9.1 环境要求

```
操作系统：Ubuntu 22.04+ / macOS 14+ / Windows 11 WSL2
Python：3.11.x（推荐用 pyenv 管理）
Node.js：20.x LTS
Docker Desktop：4.x+
Git：2.40+
内存：≥ 16GB（运行本地容器）
```

### 9.2 后端本地启动

```bash
# 1. 克隆仓库
git clone http://internal.lgsteel.com/ai-query/lgsteel-ai-query.git
cd lgsteel-ai-query

# 2. 创建 Python 虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# 3. 安装依赖
pip install -r backend/requirements.txt
pip install -r backend/requirements-dev.txt

# 4. 启动开发依赖（数据库、Redis、ChromaDB）
docker compose -f docker-compose.dev.yml up -d

# 5. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 LLM API Key 等必要配置

# 6. 初始化数据库
cd backend
alembic upgrade head
python scripts/seed_data.py    # 导入开发测试数据和种子用户

# 7. 启动后端
uvicorn app.main:app --reload --port 8000
```

### 9.3 前端本地启动

```bash
cd frontend
npm install
npm run dev                    # 启动开发服务器，默认 http://localhost:5173
```

### 9.4 运行测试

```bash
cd backend

# 运行全部单元测试
pytest tests/unit/ -v

# 运行带覆盖率报告
pytest tests/unit/ --cov=app --cov-report=html
open htmlcov/index.html        # 查看覆盖率报告

# 运行集成测试（需要 docker compose dev 服务运行中）
pytest tests/integration/ -v

# 运行 SQL 准确率测试
pytest tests/accuracy/ -v --tb=short

# 运行性能测试
locust -f tests/performance/locustfile.py \
  --host=http://localhost:8000 \
  --users 20 --spawn-rate 2 \
  --run-time 5m --headless
```

---

## 十、编码检查清单

在提交 PR 前，开发人员必须逐项确认：

### 10.1 功能完整性

- [ ] 新功能有对应的单元测试，覆盖率达到模块要求
- [ ] 新 API 接口有集成测试用例
- [ ] 所有边界条件和异常路径已测试
- [ ] 数据字典已更新（如新增业务字段）

### 10.2 代码质量

- [ ] `ruff check` 和 `ruff format` 通过，无报错
- [ ] `mypy --strict` 通过，无类型错误
- [ ] 所有函数有完整的类型注解
- [ ] 无 `print()` 调试语句，日志统一使用 `loguru`
- [ ] 无注释掉的大段代码

### 10.3 安全合规

- [ ] 未在代码中硬编码任何密钥、密码、连接字符串
- [ ] LLM API 调用未包含真实业务数据值
- [ ] 新增查询路径已经过 `sql_validator` 校验
- [ ] 新增数据访问路径已经过 `rbac` 权限检查
- [ ] 审计日志已正确记录

### 10.4 接口规范

- [ ] API 响应使用统一的 `ApiResponse` 结构
- [ ] 错误码使用规范中定义的枚举值
- [ ] 新接口已更新 API 文档
- [ ] 分页接口遵循统一的分页规范

### 10.5 数据库

- [ ] Schema 变更通过 Alembic Migration 管理
- [ ] 新增表有必要的索引
- [ ] 未使用同步 SQLAlchemy Session
- [ ] 业务数据表命名遵循 `{domain}_{uuid_short}` 规范

---

**版本变更记录**

| 版本 | 日期 | 变更内容 | 变更人 |
|-----|------|---------|------|
| V1.0 | 2026-03-26 | 初始版本，覆盖第一期全部模块设计与测试计划 | — |

---

*本文档由莱钢集团信息化项目技术团队编制。如有技术问题，请联系技术负责人。*
