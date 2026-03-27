#!/bin/bash
# 莱钢 AI 问数启动脚本
# 用法：
#   ./scripts/start.sh        # 生产环境（默认）
#   ./scripts/start.sh dev    # 开发环境（仅启动基础设施服务）
#   ./scripts/start.sh prod   # 生产环境（显式指定）

set -e

PROJECT="lgsteel"
ENV="${1:-prod}"

if [[ "$ENV" != "dev" && "$ENV" != "prod" ]]; then
    echo "❌ 参数错误：ENV 必须为 dev 或 prod，当前为 '${ENV}'"
    echo "   用法: ./scripts/start.sh [dev|prod]"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "================================================"
echo " 莱钢 AI 问数 (${PROJECT}) - ${ENV} 环境启动"
echo "================================================"
echo ""

# 端口冲突检查
bash "${SCRIPT_DIR}/check_ports.sh"

echo ""
echo "✅ 端口检查通过，启动 ${PROJECT} [${ENV}]..."
echo ""

if [ "$ENV" = "dev" ]; then
    # 开发环境：只启动基础设施（DB、Redis、ChromaDB），后端在 IDE 中运行
    if [ -f "${ROOT_DIR}/.env.local" ]; then
        echo "  加载本地开发环境变量：.env.local"
        set -a
        source "${ROOT_DIR}/.env.local"
        set +a
    elif [ -f "${ROOT_DIR}/.env" ]; then
        echo "  加载环境变量：.env"
        set -a
        source "${ROOT_DIR}/.env"
        set +a
    else
        echo "  ⚠️  未找到 .env 或 .env.local，使用 docker-compose.dev.yml 中的默认值"
    fi

    docker compose \
        -p "${PROJECT}_dev" \
        -f "${ROOT_DIR}/docker-compose.dev.yml" \
        up -d

    echo ""
    echo "✅ 开发环境基础设施启动完成。"
    echo ""
    echo "   服务地址（宿主机本地访问）："
    echo "   - 元数据库  PostgreSQL : 127.0.0.1:5441"
    echo "   - 业务数据库 PostgreSQL : 127.0.0.1:5442"
    echo "   - Redis               : 127.0.0.1:6391"
    echo "   - ChromaDB            : 127.0.0.1:8191"
    echo ""
    echo "   后端启动命令（在 backend/ 目录）："
    echo "   uvicorn app.main:app --reload --port 8000"

else
    # 生产环境：启动全部服务
    if [ ! -f "${ROOT_DIR}/.env" ]; then
        echo "❌ 生产环境未找到 .env 文件，请先配置！"
        echo "   参考：cp .env.example .env && vim .env"
        exit 1
    fi

    docker compose \
        -p "${PROJECT}" \
        -f "${ROOT_DIR}/docker-compose.yml" \
        up -d

    echo ""
    echo "✅ ${PROJECT} 生产环境启动完成。"
    echo ""
    echo "   服务地址："
    echo "   - Web 入口（HTTP）  : http://localhost:8100"
    echo "   - Web 入口（HTTPS） : https://localhost:8143"
    echo "   - 后端调试端口      : 127.0.0.1:8101（仅限本机）"
    echo ""
    echo "   查看日志："
    echo "   docker compose -p lgsteel logs -f backend"
fi
