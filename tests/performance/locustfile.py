"""
Locust 性能测试脚本

测试场景：
  - 自然语言查询（核心链路，权重 60%）
  - 数据源列表查询（读接口，权重 20%）
  - 健康检查（基准，权重 20%）

运行方法：
    # Headless 模式（CI 使用）
    locust -f tests/performance/locustfile.py \
           --headless -u 20 -r 2 --run-time 60s \
           --host http://127.0.0.1:8101

    # Web UI 模式
    locust -f tests/performance/locustfile.py --host http://127.0.0.1:8101

环境变量：
    PERF_USERNAME   测试用户名（默认 user_0001）
    PERF_PASSWORD   测试密码（默认 test_password）
    PERF_HOST       目标地址（默认 http://127.0.0.1:8101）

验收阈值（见 CLAUDE.md §12.5）：
    P95 响应时间 < 15s
    错误率 < 5%
"""
from __future__ import annotations

import os
import random
from typing import Any

from locust import HttpUser, between, events, task
from locust.runners import MasterRunner


# ---------------------------------------------------------------------------
# 测试用问题列表（跨4个业务域）
# ---------------------------------------------------------------------------

SAMPLE_QUESTIONS: list[str] = [
    # 财务域
    "查询本月销售收入",
    "各产品线毛利率是多少",
    "第一季度总收入汇总",
    "收入同比增长情况",
    "哪个月份收入最高",
    # 销售域
    "本月各产品销售量",
    "客户A的采购金额",
    "热轧卷板销售趋势",
    "各客户贡献占比",
    "销售额环比变化",
    # 生产域
    "本月各产线产量",
    "合格率最低的设备",
    "热轧产线本月产量",
    "生产量与上月对比",
    "哪条产线产量最高",
    # 采购域
    "本月铁矿石采购量",
    "各供应商采购金额占比",
    "焦炭单价走势",
    "采购总金额是多少",
    "主要供应商有哪些",
]


# ---------------------------------------------------------------------------
# 全局 token 缓存（避免每个用户都登录）
# ---------------------------------------------------------------------------

_cached_token: str | None = None


def _get_token(client: Any, username: str, password: str) -> str | None:
    """登录并返回 access_token，失败返回 None。"""
    with client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        catch_response=True,
        name="/api/v1/auth/login",
    ) as resp:
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 0:
                return data["data"]["access_token"]
        resp.failure(f"Login failed: {resp.status_code} {resp.text[:200]}")
    return None


# ---------------------------------------------------------------------------
# 用户类
# ---------------------------------------------------------------------------

class AIQueryUser(HttpUser):
    """
    模拟业务分析师使用 AI 问数的行为。
    思考时间 3-8 秒（模拟用户阅读结果后再提问）。
    """

    wait_time = between(3, 8)
    host = os.getenv("PERF_HOST", "http://127.0.0.1:8101")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._token: str | None = None
        self._conversation_id: str | None = None

    # ------------------------------------------------------------------
    # 初始化：登录获取 token
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        username = os.getenv("PERF_USERNAME", "user_0001")
        password = os.getenv("PERF_PASSWORD", "test_password")
        self._token = _get_token(self.client, username, password)

    def _auth_header(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    # ------------------------------------------------------------------
    # 任务定义
    # ------------------------------------------------------------------

    @task(6)
    def chat_query(self) -> None:
        """自然语言查询（核心链路，权重 6）。"""
        if not self._token:
            return
        question = random.choice(SAMPLE_QUESTIONS)
        payload: dict[str, Any] = {"question": question}
        if self._conversation_id:
            payload["conversation_id"] = self._conversation_id

        with self.client.post(
            "/api/v1/chat/query",
            json=payload,
            headers=self._auth_header(),
            catch_response=True,
            name="/api/v1/chat/query",
            timeout=30,
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    # 保存 conversation_id 以支持多轮对话测试
                    self._conversation_id = data.get("data", {}).get("conversation_id")
                    resp.success()
                else:
                    # 业务错误（如 SQL 生成失败）不计入性能失败
                    resp.success()
            elif resp.status_code in (401, 403):
                resp.failure(f"Auth error: {resp.status_code}")
                self._token = None  # 触发下次请求时重新登录
            elif resp.status_code >= 500:
                resp.failure(f"Server error: {resp.status_code}")
            else:
                resp.success()

    @task(2)
    def list_datasources(self) -> None:
        """数据源列表（读接口，权重 2）。"""
        if not self._token:
            return
        with self.client.get(
            "/api/v1/datasource/",
            headers=self._auth_header(),
            catch_response=True,
            name="/api/v1/datasource/",
        ) as resp:
            if resp.status_code in (200, 501):  # 501 = 接口未实现（Phase 0）
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(2)
    def health_check(self) -> None:
        """健康检查（基准，权重 2）。"""
        with self.client.get(
            "/health",
            catch_response=True,
            name="/health",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Health check failed: {resp.status_code}")


class AdminUser(HttpUser):
    """
    模拟管理员查询审计日志和准确率统计。
    频率较低（思考时间 8-15 秒）。
    """

    wait_time = between(8, 15)
    weight = 1  # 低比例管理员用户
    host = os.getenv("PERF_HOST", "http://127.0.0.1:8101")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._token: str | None = None

    def on_start(self) -> None:
        admin_user = os.getenv("PERF_ADMIN_USERNAME", "admin_0000")
        admin_pass = os.getenv("PERF_ADMIN_PASSWORD", "admin_password")
        self._token = _get_token(self.client, admin_user, admin_pass)

    def _auth_header(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    @task(3)
    def view_audit_logs(self) -> None:
        """查看审计日志。"""
        if not self._token:
            return
        with self.client.get(
            "/api/v1/admin/audit-logs?page=1&page_size=20",
            headers=self._auth_header(),
            catch_response=True,
            name="/api/v1/admin/audit-logs",
        ) as resp:
            if resp.status_code in (200, 501):
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(2)
    def view_accuracy_stats(self) -> None:
        """查看准确率统计。"""
        if not self._token:
            return
        with self.client.get(
            "/api/v1/admin/accuracy-stats",
            headers=self._auth_header(),
            catch_response=True,
            name="/api/v1/admin/accuracy-stats",
        ) as resp:
            if resp.status_code in (200, 501):
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")


# ---------------------------------------------------------------------------
# 自定义事件：记录 P95 阈值断言（供 CI 使用）
# ---------------------------------------------------------------------------

@events.quitting.add_listener
def assert_performance_thresholds(environment: Any, **_kwargs: Any) -> None:
    """
    在 Locust 退出时检查关键性能指标，超出阈值则设置非零退出码。
    仅在非 Master 模式下执行（避免分布式测试重复断言）。
    """
    if isinstance(environment.runner, MasterRunner):
        return

    stats = environment.runner.stats
    total = stats.total

    p95_ms = total.get_response_time_percentile(0.95)
    error_rate = total.fail_ratio

    violations: list[str] = []

    P95_THRESHOLD_MS = 15_000
    ERROR_RATE_THRESHOLD = 0.05

    if p95_ms is not None and p95_ms > P95_THRESHOLD_MS:
        violations.append(
            f"P95 响应时间 {p95_ms:.0f}ms 超出阈值 {P95_THRESHOLD_MS}ms"
        )

    if error_rate > ERROR_RATE_THRESHOLD:
        violations.append(
            f"错误率 {error_rate:.1%} 超出阈值 {ERROR_RATE_THRESHOLD:.0%}"
        )

    if violations:
        print("\n❌ 性能测试未达标：")
        for v in violations:
            print(f"  {v}")
        environment.process_exit_code = 1
    else:
        print(
            f"\n✅ 性能测试通过：P95={p95_ms:.0f}ms，"
            f"错误率={error_rate:.1%}"
        )
