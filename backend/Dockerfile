# ============================================================
# 莱钢 AI 问数 — 后端 Dockerfile
# 多阶段构建：development / production
# ============================================================

# ---- 基础镜像 ----
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 安装系统依赖（编译 psycopg2-binary、openpyxl 等可能需要的库）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ---- 依赖安装层（仅生产依赖，利用 Docker 层缓存）----
FROM base AS deps

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ---- 开发镜像（含 dev 依赖，支持热重载）----
FROM deps AS development

COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---- 生产镜像 ----
FROM deps AS production

COPY . .

# 创建 Excel 文件存储目录
RUN mkdir -p /app/files/excel

# 非 root 用户运行
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--loop", "uvloop"]
