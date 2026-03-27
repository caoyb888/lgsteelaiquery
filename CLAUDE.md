# CLAUDE.md — 莱钢集团 AI 问数项目开发规范与约束

> 本文件是项目 AI 辅助开发的核心约束文件。所有参与本项目的开发人员及 AI 编程助手，
> 在生成、修改、审查任何代码或文档时，**必须严格遵守本文件的全部规定**。

**项目名称：** 莱钢集团 AI 问数（lgsteel-ai-query）
**版本：** V2.0
**最后更新：** 2026-03-26
**技术负责人：** （待填写）
**版本说明：** 相较 V1.0，新增第3章 Docker 多项目共存规范（端口与网络隔离约束），数据层调整为 Excel 导入模式，同步更新目录结构与环境变量。

---

## 目录

1. 项目概览
2. 技术栈约束
3. **Docker 多项目共存规范**
4. 项目目录结构
5. 编码规范
6. 数据安全红线
7. API 调用规范
8. 数据库操作规范
9. 测试规范
10. Git 工作流规范
11. 禁止事项清单
12. 关键业务逻辑约束
13. 环境变量管理

---

## 1. 项目概览

### 1.1 系统定位

本系统是一个**企业内网部署的自然语言数据查询平台**，核心能力是将用户的中文自然语言问题转化为 SQL，基于业务部门上传的 Excel 文件执行查询并返回结构化结果和自然语言解释。

**第一期数据接入方式：Excel 文件上传**（不直连 ERP/MES 等现有信息系统，第二期再逐步对接）。

### 1.2 架构分层

```
用户层（Vue3 前端）
    ↕ HTTP/WebSocket
交互层（FastAPI 后端）
    ↕ 内部调用
核心引擎层（Excel解析 + Text-to-SQL Engine）
    ↕ API 调用（仅发送脱敏 Schema + 问题）
模型层（公共 LLM API：通义千问 / 文心一言）
    ↕ SQL 执行
数据层（PostgreSQL 业务库，由 Excel 导入生成）
```

### 1.3 宿主机环境说明

```
操作系统：Ubuntu 22.04 LTS
运行账号：单一 appuser（不使用多用户隔离）
项目根目录：/home/appuser/
├── lgsteel-ai-query/     ← 本项目
└── [other-project]/      ← 同一用户下的另一个 Docker 项目
```

> **设计决策：** 本项目与同服务器上的其他项目共用同一 OS 用户，通过 Docker 网络和端口约束实现完全隔离，不采用多用户方案。Docker 已提供容器级隔离（网络/文件系统/进程），OS 用户隔离是多余的运维负担。

### 1.4 第一期交付范围

- Excel 文件上传 → 自动解析 → 清洗入库 → 自然语言查询
- 财务、销售、生产、采购四大数据域
- 对话式 Web 界面，支持表格 / 图表 / 数据时效标注
- 用户权限管理（RBAC）+ 操作审计日志
- 管理后台：数据源管理、准确率统计、用户反馈

---

## 2. 技术栈约束

### 2.1 后端

| 组件 | 指定版本 | 说明 |
|------|---------|------|
| 语言 | Python 3.11+ | 严禁使用 3.10 以下版本 |
| Web 框架 | FastAPI 0.111+ | 异步优先；禁止使用 Flask/Django |
| ORM | SQLAlchemy 2.0+ | 使用 async 模式；禁止 raw string SQL 拼接 |
| Excel 解析 | pandas 2.x + openpyxl 3.x | 支持 .xlsx/.xls/.csv |
| 向量数据库 | ChromaDB 0.5+ | 第一期；第二期可迁移 Milvus |
| 缓存 | Redis 7.x | 会话缓存、Q&A 缓存 |
| 任务队列 | Celery + Redis | 异步 LLM 调用与 Excel 解析任务 |
| 日志 | loguru | 统一日志格式；禁止 print() 调试 |
| 配置管理 | pydantic-settings | 所有配置必须通过 Settings 类读取 |
| HTTP 客户端 | httpx (async) | 禁止使用 requests（同步阻塞）|

### 2.2 前端

| 组件 | 指定版本 | 说明 |
|------|---------|------|
| 框架 | Vue 3.4+ | Composition API；禁止 Options API |
| 构建工具 | Vite 5.x | 禁止 webpack |
| UI 组件库 | Element Plus 2.7+ | 统一 UI 风格；禁止混用其他 UI 库 |
| 状态管理 | Pinia 2.x | 禁止 Vuex |
| HTTP 客户端 | Axios 1.x | 统一封装 request.js，禁止裸调用 |
| 图表库 | ECharts 5.x | 禁止引入 Chart.js / D3 |
| 类型检查 | TypeScript 5.x | 所有 .vue 文件必须 `<script setup lang="ts">` |

### 2.3 基础设施

| 组件 | 方案 | 说明 |
|------|------|------|
| 容器化 | Docker 25+ + Docker Compose v2 | 所有服务必须容器化；禁止裸机部署 |
| 反向代理 | Nginx 1.25+ | 统一入口，负责 SSL 终止和静态资源 |
| 元数据库 | PostgreSQL 15+ | 用户、权限、审计日志、Q&A 库 |
| 业务数据库 | PostgreSQL 15+（独立实例）| Excel 导入的业务数据，与元数据库分离 |
| 运行环境 | 企业内网 Ubuntu 22.04 | 严禁将服务暴露至公网 |

---

## 3. Docker 多项目共存规范

> ⚠️ **本章为本项目特有约束**。服务器上同时运行多个 Docker 项目，必须严格遵守以下端口与网络隔离规则，防止冲突导致项目间相互影响。

### 3.1 资源命名规范

所有 Docker 资源（容器、网络、Volume）**必须以项目前缀 `lgsteel_` 开头**，与其他项目完全区分。

```
容器名：
  lgsteel_nginx       lgsteel_backend
  lgsteel_celery      lgsteel_meta_db
  lgsteel_biz_db      lgsteel_redis
  lgsteel_chromadb

网络名：lgsteel_net

Volume 名：
  lgsteel_meta_db_data    lgsteel_biz_db_data
  lgsteel_redis_data      lgsteel_chroma_data
  lgsteel_excel_files
```

**强制声明方式：** 在 `docker-compose.yml` 顶层声明项目名，**不依赖目录名自动推导**：

```yaml
# docker-compose.yml 第一行必须是：
name: lgsteel
```

所有 compose 命令必须带 `-p` 参数：

```bash
# ✅ 正确
docker compose -p lgsteel up -d
docker compose -p lgsteel down
docker compose -p lgsteel logs -f backend
docker compose -p lgsteel restart backend

# ❌ 禁止：不带 -p 参数的裸命令
docker compose up -d        # 危险！项目名不明确
docker compose down         # 危险！可能误停其他项目
```

### 3.2 端口分配（硬性约束）

本项目在宿主机上**独占以下端口**，任何服务禁止使用此表之外的端口：

| 服务 | 宿主机端口 | 容器内端口 | 备注 |
|------|-----------|-----------|------|
| Nginx HTTP | **8100** | 80 | 内网用户访问入口 |
| Nginx HTTPS | **8143** | 443 | SSL 入口 |
| FastAPI 后端 | **8101** | 8000 | 仅调试用，生产走 Nginx |
| PostgreSQL 元数据库 | **5441** | 5432 | 仅本机开发连接 |
| PostgreSQL 业务数据库 | **5442** | 5432 | 仅本机开发连接 |
| Redis | **6391** | 6379 | 仅本机开发连接 |
| ChromaDB | **8191** | 8000 | 仅本机开发连接 |
| Flower（Celery监控）| **5561** | 5555 | 仅开发环境 |

> **端口选择原则：** 选用 8100+ / 5440+ / 6390+ 段，与系统默认端口（80/443/5432/6379）错开，也与同服务器其他项目协商分配，互不占用。

**本服务器多项目端口登记表（统一维护，新项目必须在此登记）：**

```
# 项目                   宿主机端口区段
# lgsteel-ai-query        8100-8199 / 5441-5442 / 6391 / 8191
# [other-project]         8200-8299 / 5451-5452 / 6392 / 8291
# 新项目须向技术负责人申请端口区段，禁止擅自占用
```

### 3.3 Docker Compose 标准配置模板

以下是本项目 `docker-compose.yml` 的**强制结构**，修改时不得破坏命名和端口约束：

```yaml
name: lgsteel                            # ← 必须声明，不可删除或改名

networks:
  lgsteel_net:                           # ← 网络名必须带前缀
    driver: bridge
    ipam:
      config:
        - subnet: 172.30.0.0/24          # ← 子网与其他项目错开

volumes:
  lgsteel_meta_db_data:
  lgsteel_biz_db_data:
  lgsteel_redis_data:
  lgsteel_chroma_data:
  lgsteel_excel_files:

services:

  nginx:
    container_name: lgsteel_nginx        # ← 容器名必须带前缀
    image: nginx:1.25-alpine
    ports:
      - "8100:80"                        # ← 宿主机端口固定 8100
      - "8143:443"
    networks: [lgsteel_net]
    restart: always

  backend:
    container_name: lgsteel_backend
    build:
      context: ./backend
    ports:
      - "127.0.0.1:8101:8000"            # ← 绑定 127.0.0.1，不对外暴露
    networks: [lgsteel_net]
    depends_on:
      meta_db: { condition: service_healthy }
      biz_db:  { condition: service_healthy }
      redis:   { condition: service_healthy }
    restart: always

  celery:
    container_name: lgsteel_celery
    build:
      context: ./backend
    command: celery -A app.worker worker --loglevel=info --concurrency=4
    networks: [lgsteel_net]
    depends_on: [redis]
    restart: always

  meta_db:
    container_name: lgsteel_meta_db
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: lgsteel_meta
      POSTGRES_USER: ${META_DB_USER}
      POSTGRES_PASSWORD: ${META_DB_PASSWORD}
    volumes:
      - lgsteel_meta_db_data:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5441:5432"            # ← 绑定 127.0.0.1
    networks: [lgsteel_net]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${META_DB_USER} -d lgsteel_meta"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: always

  biz_db:
    container_name: lgsteel_biz_db
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: lgsteel_biz
      POSTGRES_USER: ${BIZ_DB_USER}
      POSTGRES_PASSWORD: ${BIZ_DB_PASSWORD}
    volumes:
      - lgsteel_biz_db_data:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5442:5432"            # ← 绑定 127.0.0.1
    networks: [lgsteel_net]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${BIZ_DB_USER} -d lgsteel_biz"]
      interval: 10s
    restart: always

  redis:
    container_name: lgsteel_redis
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD} --appendonly yes
    volumes:
      - lgsteel_redis_data:/data
    ports:
      - "127.0.0.1:6391:6379"            # ← 绑定 127.0.0.1
    networks: [lgsteel_net]
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
    restart: always

  chromadb:
    container_name: lgsteel_chromadb
    image: chromadb/chroma:latest
    volumes:
      - lgsteel_chroma_data:/chroma/chroma
    ports:
      - "127.0.0.1:8191:8000"            # ← 绑定 127.0.0.1
    networks: [lgsteel_net]
    restart: always
```

### 3.4 开发环境 Compose（docker-compose.dev.yml）

```yaml
name: lgsteel_dev                        # ← dev 环境独立项目名

networks:
  lgsteel_net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.30.1.0/24          # ← 子网与生产错开

services:
  meta_db:
    container_name: lgsteel_dev_meta_db  # ← dev 容器名加 dev_ 中缀
    ports:
      - "127.0.0.1:5441:5432"

  biz_db:
    container_name: lgsteel_dev_biz_db
    ports:
      - "127.0.0.1:5442:5432"

  redis:
    container_name: lgsteel_dev_redis
    ports:
      - "127.0.0.1:6391:6379"
```

**开发/生产环境切换规则：两套环境禁止在同一台机器上同时启动（端口重叠）：**

```bash
# 启动开发环境
docker compose -p lgsteel_dev -f docker-compose.dev.yml up -d

# 切换到生产前必须先 down 开发环境
docker compose -p lgsteel_dev -f docker-compose.dev.yml down
docker compose -p lgsteel -f docker-compose.yml up -d
```

### 3.5 封装启动脚本（强制使用）

**禁止直接手动执行 docker compose 命令**，必须通过以下封装脚本操作，确保每次都带端口检查和正确的 `-p` 参数：

```bash
# scripts/start.sh
#!/bin/bash
set -e

PROJECT="lgsteel"
ENV="${1:-prod}"   # 参数：dev | prod，默认 prod

# 端口冲突检查
PORTS=(8100 8143 8101 5441 5442 6391 8191)
echo "=== ${PROJECT} 端口冲突检查 ==="
CONFLICT=0
for port in "${PORTS[@]}"; do
  IN_USE=$(ss -tlnp 2>/dev/null | grep ":${port} ")
  if [ -n "$IN_USE" ]; then
    # 判断是否本项目容器自己占用（允许）
    CONTAINER=$(docker ps --format '{{.Names}}' --filter "name=${PROJECT}_" 2>/dev/null | head -1)
    if [ -n "$CONTAINER" ]; then
      echo "  [OK]  端口 ${port} 被本项目容器占用"
    else
      echo "  [!!]  端口 ${port} 被其他进程占用！"
      CONFLICT=1
    fi
  else
    echo "  [空闲] 端口 ${port}"
  fi
done

if [ "$CONFLICT" -eq 1 ]; then
  echo ""
  echo "❌ 存在端口冲突，请先解决再启动！"
  exit 1
fi

echo ""
echo "✅ 端口检查通过，启动 ${PROJECT} [${ENV}]..."

if [ "$ENV" = "dev" ]; then
  docker compose -p "${PROJECT}_dev" -f docker-compose.dev.yml up -d
else
  docker compose -p "${PROJECT}" -f docker-compose.yml up -d
fi

echo "✅ ${PROJECT} 启动完成。"
```

```bash
# scripts/stop.sh
#!/bin/bash
PROJECT="lgsteel"
ENV="${1:-prod}"

if [ "$ENV" = "dev" ]; then
  docker compose -p "${PROJECT}_dev" -f docker-compose.dev.yml down
else
  docker compose -p "${PROJECT}" -f docker-compose.yml down
fi
```

```bash
# 日常使用
./scripts/start.sh       # 生产环境启动
./scripts/start.sh dev   # 开发环境启动
./scripts/stop.sh        # 生产环境停止
./scripts/stop.sh dev    # 开发环境停止
```

### 3.6 容器间通信规范

容器间通信**必须使用 Docker DNS 服务名**，禁止使用 localhost 或宿主机 IP：

```python
# ✅ 正确：容器内使用服务名（Docker 内置 DNS 解析）
META_DB_URL = "postgresql+asyncpg://user:pass@meta_db:5432/lgsteel_meta"
BIZ_DB_URL  = "postgresql+asyncpg://user:pass@biz_db:5432/lgsteel_biz"
REDIS_URL   = "redis://:password@redis:6379/0"
CHROMA_URL  = "http://chromadb:8000"

# ❌ 禁止：容器内用 localhost
META_DB_URL = "postgresql+asyncpg://user:pass@localhost:5441/..."

# ❌ 禁止：容器内用宿主机 IP
META_DB_URL = "postgresql+asyncpg://user:pass@192.168.1.100:5441/..."
```

在 `config.py` 中区分容器内与宿主机本地调试地址：

```python
class Settings(BaseSettings):
    # 容器内服务间通信（默认值，生产/容器内使用）
    META_DB_HOST: str = "meta_db"
    BIZ_DB_HOST: str  = "biz_db"
    REDIS_HOST: str   = "redis"
    CHROMA_HOST: str  = "chromadb"

    # 宿主机 IDE 调试时在 .env.local 中覆盖为：
    # META_DB_HOST=127.0.0.1
    # META_DB_PORT=5441
```

### 3.7 Docker 资源操作约束汇总

```bash
# ✅ 查看本项目容器
docker ps --filter "name=lgsteel_"

# ✅ 查看本项目网络
docker network ls --filter "name=lgsteel"

# ✅ 查看本项目 Volume
docker volume ls --filter "name=lgsteel"

# ✅ 清理本项目悬空镜像（安全）
docker image prune --filter "label=project=lgsteel" -f

# ❌ 禁止：影响所有项目的全局清理命令
docker system prune           # 会清理其他项目资源
docker network prune          # 会删除其他项目网络
docker volume prune           # 会删除其他项目数据
```

---

## 4. 项目目录结构

所有新代码必须严格遵循以下目录结构，**不得自行创建顶层目录**：

```
lgsteel-ai-query/
├── CLAUDE.md
├── README.md
├── docker-compose.yml           # 生产 Compose（name: lgsteel）
├── docker-compose.dev.yml       # 开发 Compose（name: lgsteel_dev）
├── .env.example
├── .gitignore
│
├── scripts/
│   ├── check_ports.sh           # 端口检查（内嵌于 start.sh）
│   ├── start.sh                 # 封装启动（必须通过此脚本启动）
│   ├── stop.sh                  # 封装停止
│   └── seed_data.py             # 开发测试数据初始化
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   ├── pyproject.toml
│   ├── alembic/
│   └── app/
│       ├── main.py
│       ├── config.py
│       ├── dependencies.py
│       ├── worker.py            # Celery 应用入口
│       ├── api/v1/
│       │   ├── chat.py
│       │   ├── datasource.py   # Excel 数据源管理接口
│       │   ├── admin.py
│       │   └── auth.py
│       ├── core/
│       │   ├── excel_parser.py
│       │   ├── data_cleaner.py
│       │   ├── field_mapper.py
│       │   ├── text_to_sql.py
│       │   ├── prompt_builder.py
│       │   ├── sql_validator.py
│       │   ├── sql_executor.py
│       │   ├── result_formatter.py
│       │   ├── nlg.py
│       │   └── conversation.py
│       ├── llm/
│       │   ├── base.py
│       │   ├── qianwen.py
│       │   ├── wenxin.py
│       │   └── router.py
│       ├── db/
│       │   ├── meta_session.py  # 元数据库 async session
│       │   ├── biz_session.py   # 业务数据库 async session
│       │   ├── models/
│       │   └── repositories/
│       ├── knowledge/
│       │   ├── dictionary.py
│       │   ├── embedding.py
│       │   └── cache.py
│       ├── security/
│       │   ├── auth.py
│       │   ├── rbac.py
│       │   ├── row_filter.py
│       │   ├── desensitize.py
│       │   └── audit.py
│       ├── schemas/
│       └── utils/
│           └── exceptions.py
│
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── src/
│       ├── main.ts / App.vue
│       ├── router/ / stores/ / api/
│       ├── components/
│       │   ├── chat/
│       │   ├── datasource/      # Excel 上传与管理组件
│       │   ├── charts/
│       │   └── common/
│       └── views/
│
├── nginx/
│   ├── nginx.conf
│   └── ssl/                     # 证书文件，不提交 Git
│
├── knowledge-base/
│   ├── dictionaries/
│   ├── few-shots/
│   └── schemas/
│
└── tests/
    ├── unit/
    ├── integration/
    ├── accuracy/
    └── performance/
        └── locustfile.py
```

---

## 5. 编码规范

### 5.1 Python 规范

**命名约定：**

```python
# 模块/文件名：snake_case
excel_parser.py / text_to_sql.py

# 类名：PascalCase
class ExcelParser: ...
class TextToSQLEngine: ...

# 函数/变量：snake_case
async def parse_excel(file_path: str) -> ExcelParseResult: ...

# 常量：SCREAMING_SNAKE_CASE
MAX_SQL_RETRY = 3
EXCEL_MAX_SIZE_BYTES = 50 * 1024 * 1024

# 私有方法：单下划线前缀
def _detect_header_row(self, df: pd.DataFrame) -> int: ...
```

**类型注解：强制要求，所有函数必须有完整类型注解：**

```python
# ✅ 正确
async def execute_query(
    sql: str,
    datasource_id: str,
    user_id: str,
    timeout: int = 30,
) -> QueryResult: ...

# ❌ 禁止
async def execute_query(sql, datasource_id, user_id): ...
```

**异步规范：所有 IO 操作必须使用 async/await：**

```python
# ✅ 正确
async def call_llm_api(prompt: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(...)

# ❌ 禁止：同步阻塞 IO
def call_llm_api(prompt: str) -> str:
    response = requests.post(...)
```

**错误处理：必须使用自定义异常体系：**

```python
# app/utils/exceptions.py
class AIQueryBaseException(Exception): ...
class ExcelParseError(AIQueryBaseException): ...
class FieldMappingError(AIQueryBaseException): ...
class SQLGenerationError(AIQueryBaseException): ...
class SQLSafetyViolationError(AIQueryBaseException): ...
class DataPermissionError(AIQueryBaseException): ...
class LLMAPIError(AIQueryBaseException): ...
class LLMAllFallbackExhaustedError(LLMAPIError): ...
```

**日志规范：**

```python
from loguru import logger

# ✅ 结构化日志
logger.info("SQL 生成成功", question=question, elapsed_ms=elapsed)
logger.error("Excel 解析失败", file=file_path, error=str(e))

# ❌ 禁止
print("生成 SQL:", sql)
logger.info(f"生成 SQL: {sql}")   # 禁止 f-string 拼接日志消息
```

### 5.2 TypeScript / Vue 规范

```vue
<!-- ✅ 正确：script setup + TypeScript -->
<script setup lang="ts">
import { ref } from 'vue'
import type { ChatMessage } from '@/types'
const props = defineProps<{ messages: ChatMessage[] }>()
</script>

<!-- ❌ 禁止：Options API -->
<script>
export default { data() { return {} } }
</script>
```

```typescript
// ✅ 通过封装函数调用接口
import { queryChatAPI } from '@/api/chat'
const result = await queryChatAPI({ question: '查询本月收入' })

// ❌ 禁止：组件内直接调用 axios
import axios from 'axios'
const result = await axios.post('/api/v1/chat', { question })
```

### 5.3 代码质量工具（CI 强制通过）

```bash
# 后端
ruff format .
ruff check .
mypy app/ --strict
pytest tests/unit/ --cov=app/core --cov-fail-under=80

# 前端
eslint src/ --fix
prettier --write src/
vue-tsc --noEmit
```

---

## 6. 数据安全红线

> ⚠️ **最高优先级约束，任何功能开发不得以任何理由绕过。**

### 6.1 数据不出域原则

**调用外部 LLM API 时，绝对禁止在请求体中包含：**
- 任何真实业务数据值（金额、产量、客户名、供应商名等）
- Excel 文件中的实际数据行
- 员工姓名、手机号等个人信息
- 内部账号、密码、数据库连接字符串

**允许发送给外部 API 的内容仅限于：**
- 经脱敏映射的字段名和表结构（Schema）
- 用户的自然语言问题（经敏感词过滤后）
- Few-shot 示例（虚构数据）

```python
# ✅ 正确
payload = {
    "schema": desensitizer.get_safe_schema(datasource_id),
    "question": desensitizer.clean_question(user_question),
    "examples": few_shot_manager.get_examples(domain),
}

# ❌ 严禁
payload = {
    "sample_data": df.head(5).to_dict(),  # 绝对禁止！
}
```

### 6.2 SQL 注入防护

所有 SQL 执行前必须经过 `sql_validator.py`，以下模式必须拦截：

```python
FORBIDDEN_PATTERNS = [
    r'\bDROP\b', r'\bTRUNCATE\b', r'\bDELETE\b',
    r'\bUPDATE\b', r'\bINSERT\b', r'\bCREATE\b',
    r'\bALTER\b', r'\bEXEC(?:UTE)?\b',
    r';\s*\w+', r'--', r'/\*.*?\*/',
    r'\bINTO\s+OUTFILE\b', r'\bLOAD_FILE\b',
]
```

业务数据库账号只授予 `SELECT` 权限，严禁使用 DBA 账号。

### 6.3 行级权限强制注入

```python
# 每次查询必须经过此步骤，不得跳过
filtered_sql = await row_filter.inject_permission(
    sql=generated_sql,
    user_id=current_user.id,
    allowed_tables=rbac.get_allowed_tables(current_user.role),
)
```

### 6.4 审计日志强制记录

每次查询必须完整记录（用户、问题、SQL、来源数据、时间），日志不得被删除。

---

## 7. API 调用规范

### 7.1 LLM 客户端规范

```python
# ✅ 正确：通过 router 调用
from app.llm.router import get_llm_router
llm = get_llm_router()
response = await llm.complete(prompt=prompt, max_tokens=1000)

# ❌ 禁止：直接调用 SDK
import dashscope
dashscope.Generation.call(...)
```

### 7.2 模型降级策略

```python
LLM_FALLBACK_CHAIN = ["qianwen-max", "qianwen-plus", "wenxin-4.0"]
# 单次超时：15 秒 / 最大重试：3 次 / 指数退避：1s → 2s → 4s
```

### 7.3 Token 用量控制

```python
# 在 config.py 中配置，不得硬编码
LLM_MAX_TOKENS_PER_REQUEST      = 2000
LLM_DAILY_TOKEN_BUDGET_PER_USER = 100_000
LLM_DAILY_TOKEN_BUDGET_GLOBAL   = 5_000_000
```

### 7.4 统一响应格式与错误码

```
响应格式：{ "code": 0, "message": "ok", "data": {...}, "request_id": "uuid" }

错误码：
1001 SQL生成失败    1002 SQL安全校验不通过  1003 数据域权限不足
1004 查询超时       1005 LLM API不可用      1010 Excel格式错误
1011 Excel文件超大  1012 Excel解析失败
4001 未认证         4003 无权限              5000 系统内部错误
```

---

## 8. 数据库操作规范

### 8.1 双库 Session 规范

本项目有两个独立的 PostgreSQL 实例，必须使用各自对应的 Session，**禁止混用**：

```python
# ✅ 元数据库（用户/权限/审计/Q&A）
from app.db.meta_session import get_meta_session
async with get_meta_session() as session:
    result = await session.execute(select(User).where(...))

# ✅ 业务数据库（Excel 导入数据，只读）
from app.db.biz_session import get_biz_session
async with get_biz_session() as session:
    result = await session.execute(text(validated_sql))

# ❌ 禁止：混用 / 禁止：同步 Session
```

### 8.2 业务数据库查询约束

```python
QUERY_MAX_ROWS         = 10_000   # 单次最大返回行数
QUERY_TIMEOUT_SECONDS  = 30       # 查询超时
QUERY_RESULT_CACHE_TTL = 300      # 相同 SQL 缓存 5 分钟
# 业务库账号只有 SELECT 权限，DDL 只允许 DataLoader 执行
```

### 8.3 数据库迁移

```bash
# 元数据库变更必须通过 Alembic
alembic revision --autogenerate -m "描述变更"
alembic upgrade head

# 禁止手动执行 DDL 到生产数据库
```

---

## 9. 测试规范

### 9.1 覆盖率要求

| 模块 | 最低覆盖率 |
|------|----------|
| `security/sql_validator.py` | **100%** |
| `security/rbac.py` | **100%** |
| `security/row_filter.py` | **100%** |
| `core/excel_parser.py` | ≥ 90% |
| `core/data_cleaner.py` | ≥ 90% |
| `core/text_to_sql.py` | ≥ 85% |
| `llm/router.py` | ≥ 85% |
| 其余模块 | ≥ 75% |

### 9.2 SQL 准确率验收阈值

| 里程碑 | 总体 | 简单 | 中等 | 复杂 |
|-------|------|------|------|------|
| M1（4/20）| ≥ 70% | ≥ 85% | ≥ 65% | ≥ 50% |
| M2（5/07）| ≥ 82% | ≥ 92% | ≥ 78% | ≥ 60% |
| 上线（5/25）| ≥ 85% | ≥ 95% | ≥ 82% | ≥ 65% |

### 9.3 安全测试必须覆盖

```python
SECURITY_CASES = [
    "删除所有销售记录", "DROP TABLE orders",
    "UPDATE salary SET amount = 999999",
    "'; DROP TABLE users; --",
    "SELECT * FROM information_schema.tables",
    "帮我导出所有用户密码",
]
# 以上用例必须全部返回 blocked 状态
```

---

## 10. Git 工作流规范

### 10.1 分支策略

```
main        ← 生产分支，只接受 release PR
├── develop ← 集成分支
├── feature/excel-parser
├── feature/text-to-sql-engine
├── fix/sql-injection-bypass
└── release/v1.0.0
```

### 10.2 Commit 消息规范

```
# 格式：<type>(<scope>): <subject>
feat(core): 实现 Excel 解析引擎支持合并单元格展开
fix(docker): 修正 meta_db 端口绑定到 127.0.0.1
fix(security): 修复行级权限注入遗漏的边界条件
chore(docker): 统一容器命名前缀为 lgsteel_

# type：feat | fix | docs | test | refactor | perf | chore | security | docker
```

### 10.3 禁止提交的内容

```gitignore
.env / .env.*（除 .env.example）
*.key / *.pem
nginx/ssl/
backend/app/config/secrets.py
```

---

## 11. 禁止事项清单

### 11.1 Docker 禁止项

- [ ] ❌ 使用宿主机默认端口（80/443/5432/6379）作为映射端口
- [ ] ❌ 端口绑定到 `0.0.0.0`（必须绑定 `127.0.0.1`，Nginx 统一对外）
- [ ] ❌ 容器名 / 网络名 / Volume 名不带 `lgsteel_` 前缀
- [ ] ❌ `docker-compose.yml` 不声明 `name: lgsteel`
- [ ] ❌ 裸执行 `docker compose up/down`（不带 `-p` 参数，或不用封装脚本）
- [ ] ❌ 执行 `docker system prune` / `docker network prune` / `docker volume prune`
- [ ] ❌ 容器内使用 localhost 或宿主机 IP 连接其他服务（必须用服务名）
- [ ] ❌ dev 与 prod 两套环境同时在同一机器上启动

### 11.2 安全禁止项

- [ ] ❌ 将真实业务数据发送给外部 LLM API
- [ ] ❌ 在代码中硬编码 API Key、数据库密码
- [ ] ❌ 业务数据库使用 DBA 权限账号
- [ ] ❌ 绕过 SQL 安全校验直接执行 SQL
- [ ] ❌ 绕过行级权限过滤
- [ ] ❌ 禁用审计日志

### 11.3 架构禁止项

- [ ] ❌ 在 API 路由层（api/）写业务逻辑
- [ ] ❌ 在业务代码中直接实例化 LLM SDK
- [ ] ❌ 拼接 SQL 字符串（使用参数化查询或 ORM）
- [ ] ❌ 在 Vue 组件中直接调用 axios
- [ ] ❌ 使用同步 IO 操作（requests / 同步 sqlalchemy）
- [ ] ❌ 元数据库与业务数据库混用同一 Session

### 11.4 质量禁止项

- [ ] ❌ 提交无类型注解的 Python 函数
- [ ] ❌ 提交 print() 调试语句
- [ ] ❌ 提交未通过 ruff / mypy / eslint 检查的代码
- [ ] ❌ 注释掉大段代码提交

---

## 12. 关键业务逻辑约束

### 12.1 Excel 数据接入约束

```python
EXCEL_MAX_SIZE_BYTES            = 50 * 1024 * 1024  # 50MB
EXCEL_SUPPORTED_TYPES           = {".xlsx", ".xls", ".csv"}
EXCEL_MAX_BATCH_FILES           = 10
FIELD_MAPPING_CONFIRM_THRESHOLD = 0.70   # 低于此置信度必须人工确认
```

### 12.2 Text-to-SQL 重试策略

```python
MAX_SQL_GENERATION_RETRY = 3
# 每次重试必须附带上次错误信息，引导 LLM 修正
```

### 12.3 多轮对话管理

```python
MAX_CONVERSATION_TURNS   = 10     # 保留最近10轮，滑动窗口
CONVERSATION_TTL_SECONDS = 7200   # 2小时无操作后清除
```

### 12.4 数据时效强制标注

每条查询结果**必须**附带数据来源信息，不得省略：

```python
# result_formatter.py 强制注入
DataSourceInfo(
    datasource_name = "销售部_月度台账_202603.xlsx",
    data_date       = "2026-03-15",          # 数据截止日期
    upload_time     = "2026-03-18T09:00:00", # 上传时间
)
```

### 12.5 准确率监控告警阈值

| 指标 | 告警阈值 |
|------|---------|
| SQL 生成成功率 | < 90% |
| SQL 执行成功率 | < 85% |
| 用户满意度（点赞率）| < 70% |
| P95 响应时间 | > 15s |
| LLM API 可用率 | < 99% 触发降级 |

---

## 13. 环境变量管理

所有配置通过环境变量注入，禁止硬编码。参考 `.env.example`：

```bash
# ===== 应用 =====
APP_ENV=development              # development | production
APP_SECRET_KEY=                  # JWT 密钥，生产必须替换为随机强密码
APP_LOG_LEVEL=INFO
PROJECT_NAME=lgsteel             # 与 docker-compose.yml name 保持一致

# ===== Docker 宿主机端口（与 docker-compose.yml 严格对应）=====
HOST_PORT_NGINX_HTTP=8100        # Nginx HTTP 入口
HOST_PORT_NGINX_HTTPS=8143       # Nginx HTTPS 入口
HOST_PORT_BACKEND=8101           # 后端调试端口（仅开发用）
HOST_PORT_META_DB=5441           # 元数据库宿主机端口
HOST_PORT_BIZ_DB=5442            # 业务数据库宿主机端口
HOST_PORT_REDIS=6391             # Redis 宿主机端口
HOST_PORT_CHROMA=8191            # ChromaDB 宿主机端口

# ===== 元数据库 =====
META_DB_HOST=meta_db             # 容器内用服务名；宿主机调试改为 127.0.0.1
META_DB_PORT=5432                # 容器内固定 5432
META_DB_NAME=lgsteel_meta
META_DB_USER=
META_DB_PASSWORD=

# ===== 业务数据库 =====
BIZ_DB_HOST=biz_db
BIZ_DB_PORT=5432
BIZ_DB_NAME=lgsteel_biz
BIZ_DB_USER=
BIZ_DB_PASSWORD=

# ===== Redis =====
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0

# ===== ChromaDB =====
CHROMA_HOST=chromadb
CHROMA_PORT=8000

# ===== LLM =====
QIANWEN_API_KEY=
QIANWEN_MODEL=qwen-max
WENXIN_API_KEY=
WENXIN_SECRET_KEY=
LLM_DAILY_TOKEN_BUDGET=5000000

# ===== 安全 =====
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=480
ALLOW_ORIGINS=http://internal.lgsteel.com   # 禁止配置为 *

# ===== 查询限制 =====
QUERY_MAX_ROWS=10000
QUERY_TIMEOUT_SECONDS=30
MAX_SQL_RETRY=3

# ===== Excel =====
EXCEL_MAX_SIZE_MB=50
EXCEL_UPLOAD_DIR=/app/files/excel
```

---

## 附：AI 辅助开发使用指南

当使用 Claude 或其他 AI 编程助手生成代码时，在会话开始时粘贴以下上下文：

```
请阅读并严格遵守项目 CLAUDE.md 中的开发规范，关键约束如下：

【Docker 约束】
- 所有容器名/网络名/Volume名必须带 lgsteel_ 前缀
- docker-compose.yml 必须声明 name: lgsteel
- 宿主机端口：Nginx=8100/8143，后端=8101，元数据库=5441，
  业务数据库=5442，Redis=6391，ChromaDB=8191
- 所有端口绑定到 127.0.0.1，不得用 0.0.0.0
- 容器间通信使用服务名（meta_db/biz_db/redis/chromadb），禁止 localhost
- 所有 compose 操作通过 ./scripts/start.sh 或带 -p lgsteel 参数执行

【代码约束】
- 所有 Python 函数必须有完整类型注解
- 所有 IO 操作必须是 async/await
- 禁止在 LLM API 调用中包含真实业务数据
- SQL 执行前必须经过 sql_validator 和 row_filter
- 使用 loguru 记录日志，禁止 print()
- 元数据库（meta_session）和业务数据库（biz_session）使用各自独立的 Session

【当前任务模块】：[填写当前开发的模块名称]
```

---

---

## 14. 首次部署已知问题清单（2026-03-27 实测）

> 以下问题均在新服务器首次部署时实际触发并已修复，再次部署时需注意。

### 14.1 依赖版本约束（requirements.txt）

| 包 | 约束 | 原因 |
|----|------|------|
| `numpy` | `==1.26.4` | 服务器 CPU（Intel Core 2 Duo T7700）不支持 POPCNT 指令，numpy 2.x 要求 X86_V2 baseline，启动时 RuntimeError 崩溃 |
| `bcrypt` | `==4.2.1` | passlib 1.7.4 与 bcrypt 5.x 不兼容，登录时密码验证报 AttributeError |
| `chromadb` | `==0.5.17` | 必须与 docker-compose.yml 中的镜像版本一致（`chromadb/chroma:0.5.17`），版本不匹配会导致 KeyError `_type` |
| `psycopg2-binary` | `==2.9.9` | Alembic env.py 使用同步驱动 `postgresql+psycopg2://`，容器中缺少此包则迁移命令报 ModuleNotFoundError |

### 14.2 代码 Bug（已在代码库中修复，记录供参考）

**datasource.py — 上传/确认流程**
- `upload_datasource` 上传后必须同时写入 `FieldMapping` 记录，否则 confirm 时 `ds.field_mappings` 为空
- `confirm_field_mappings` 查询 Datasource 时必须加 `selectinload(Datasource.field_mappings)`，否则 async 上下文 lazy load 报 `MissingGreenlet`
- `upload_datasource` 函数签名需显式声明 `Form` 参数：`domain`、`data_date`、`update_mode`

**worker.py — Celery 入库任务**
- Celery forked worker 中**禁止复用模块级 `async_engine`**（与父进程事件循环绑定），必须在任务函数内部 `create_async_engine()`，用完 `await engine.dispose()`
- `docker-compose.yml` 中 celery 启动命令必须加 `-Q celery,excel,embed`，否则路由到 `excel`/`embed` 队列的任务永远不被消费
- `CleanResult` 的行数字段名为 `rows_written`（非 `loaded_rows`）
- `ParsedField` 的清理后列名字段为 `clean_name`（非 `display_name`）
- `DataDictionaryManager.__init__` 需要三个参数：`embedding_service`、`chroma_client`、`meta_session_factory`

**admin.py**
- SQLAlchemy `cast()` 参数必须用 `Integer` 类型（`from sqlalchemy import Integer`），不能传 Python 内置 `int`

### 14.3 前端问题

| 问题 | 修复方式 |
|------|---------|
| 登录密码错误无提示 | `request.ts` 响应拦截器对 `/auth/login` 的 401 不跳转，直接透传错误给调用方 |
| 刷新页面后路由守卫将已登录用户踢到登录页 | 登录时将 `user_role`、`user_id`、`username`、`display_name` 全部持久化到 `localStorage` |
| `el-table` 带 `fixed` 列时 `ElMessageBox.confirm` 弹窗不可见 | 固定列产生独立 stacking context，改用 `el-popconfirm`（内联气泡确认）即可 |

### 14.4 前端缺失文件

- `frontend/index.html` 未提交到仓库，Vite build 报 "Could not resolve entry module"，需手动创建

### 14.5 Nginx 动态 DNS 解析

```nginx
# nginx.conf 必须配置，否则容器重建后 IP 变化导致 502
resolver 127.0.0.11 valid=10s ipv6=off;
location /api/ {
    set $backend http://lgsteel_backend:8000;
    proxy_pass $backend;
}
```

---

**版本变更记录**

| 版本 | 日期 | 变更内容 |
|-----|------|---------|
| V1.0 | 2026-03-26 | 初始版本 |
| V2.0 | 2026-03-26 | 新增第3章 Docker 多项目共存规范；数据层调整为 Excel 导入；新增双库 Session 规范 |
| V2.1 | 2026-03-27 | 新增第14章：首次部署已知问题清单（依赖版本约束、代码 Bug、前端问题、nginx 配置） |

---

*本文件随项目演进持续更新，变更须经技术负责人审批后提交至主分支。*
*最后更新：2026-03-27 | 版本：V2.1*
