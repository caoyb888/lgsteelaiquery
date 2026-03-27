#!/bin/bash
# 端口冲突检查脚本
# 被 start.sh 调用，也可单独运行：bash scripts/check_ports.sh

set -e

PROJECT="lgsteel"
PORTS=(8100 8143 8101 5441 5442 6391 8191)

echo "=== ${PROJECT} 端口冲突检查 ==="
CONFLICT=0

for port in "${PORTS[@]}"; do
    IN_USE=$(ss -tlnp 2>/dev/null | grep ":${port} " || true)
    if [ -n "$IN_USE" ]; then
        # 判断是否本项目容器自己占用（允许）
        CONTAINER=$(docker ps --format '{{.Names}}' 2>/dev/null \
            | grep -E "^${PROJECT}(_dev)?_" | head -1 || true)
        if [ -n "$CONTAINER" ]; then
            echo "  [OK]  端口 ${port} 被本项目容器 ${CONTAINER} 占用"
        else
            PID_INFO=$(ss -tlnp 2>/dev/null | grep ":${port} " | awk '{print $NF}' | head -1 || true)
            echo "  [!!]  端口 ${port} 被其他进程占用！(${PID_INFO})"
            CONFLICT=1
        fi
    else
        echo "  [空闲] 端口 ${port}"
    fi
done

if [ "$CONFLICT" -eq 1 ]; then
    echo ""
    echo "❌ 存在端口冲突，请先解决再启动！"
    echo "   提示：使用 'ss -tlnp | grep :<端口号>' 查看占用进程"
    exit 1
fi

echo ""
echo "✅ 所有端口检查通过"
exit 0
