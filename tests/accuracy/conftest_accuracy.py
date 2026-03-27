"""
SQL 准确率评估框架

AccuracyEvaluator 加载 YAML 用例，对每条用例调用 Text-to-SQL 引擎，
将生成的 SQL 与 gold SQL 做规范化比对（或执行结果比对），输出通过率报告。

用法（在项目根目录）：
    pytest tests/accuracy/ -v --tb=short

环境要求：
    - ACCURACY_TEST=1
    - 业务库中已有对应的测试表（由 integration/conftest.py 的 fixture 创建）
    - LLM 接口可用（或使用 mock）
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml


# ---------------------------------------------------------------------------
# 跳过标记
# ---------------------------------------------------------------------------

def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:  # noqa: ARG001
    if not os.getenv("ACCURACY_TEST"):
        skip_mark = pytest.mark.skip(reason="Accuracy tests skipped (set ACCURACY_TEST=1 to enable)")
        for item in items:
            if "accuracy" in str(item.fspath):
                item.add_marker(skip_mark)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class AccuracyCase:
    """单条准确率测试用例。"""
    id: str
    question: str
    gold_sql: str
    difficulty: str          # easy | medium | hard
    domain: str
    tags: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class EvaluationResult:
    """单条用例的评估结果。"""
    case_id: str
    question: str
    difficulty: str
    gold_sql: str
    generated_sql: str
    passed: bool
    match_type: str          # exact | normalized | execution | failed
    error: str = ""


# ---------------------------------------------------------------------------
# SQL 规范化工具
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_SEMI = re.compile(r";\s*$")


def normalize_sql(sql: str) -> str:
    """将 SQL 规范化为可比较的形式：小写、压缩空白、去末尾分号。"""
    normalized = sql.strip().lower()
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    normalized = _TRAILING_SEMI.sub("", normalized)
    return normalized


def sqls_match(gold: str, generated: str) -> bool:
    """判断两条 SQL 是否语义等价（规范化字符串比较）。"""
    return normalize_sql(gold) == normalize_sql(generated)


# ---------------------------------------------------------------------------
# AccuracyEvaluator
# ---------------------------------------------------------------------------

class AccuracyEvaluator:
    """
    准确率评估器。

    加载一个或多个 YAML 用例文件，驱动 Text-to-SQL 推理，
    统计按难度分级的通过率，并在 CI 中断言阈值。
    """

    # M1 阶段验收阈值（见 CLAUDE.md §9.2）
    THRESHOLDS: dict[str, float] = {
        "overall": 0.70,
        "easy":    0.85,
        "medium":  0.65,
        "hard":    0.50,
    }

    def __init__(self, cases_dir: Path | None = None) -> None:
        self.cases_dir = cases_dir or Path(__file__).parent / "cases"
        self.cases: list[AccuracyCase] = []
        self.results: list[EvaluationResult] = []

    # ------------------------------------------------------------------
    # 加载用例
    # ------------------------------------------------------------------

    def load_cases(self, domain: str | None = None) -> None:
        """从 cases/ 目录加载 YAML 用例，可按 domain 过滤。"""
        pattern = f"{domain}_cases.yaml" if domain else "*_cases.yaml"
        for yaml_file in sorted(self.cases_dir.glob(pattern)):
            self._load_yaml(yaml_file)

    def _load_yaml(self, path: Path) -> None:
        with path.open(encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f)
        domain = data.get("domain", path.stem.replace("_cases", ""))
        for raw in data.get("cases", []):
            self.cases.append(
                AccuracyCase(
                    id=raw["id"],
                    question=raw["question"],
                    gold_sql=raw["gold_sql"],
                    difficulty=raw.get("difficulty", "medium"),
                    domain=domain,
                    tags=raw.get("tags", []),
                    notes=raw.get("notes", ""),
                )
            )

    # ------------------------------------------------------------------
    # 评估单条用例
    # ------------------------------------------------------------------

    def evaluate_case(
        self,
        case: AccuracyCase,
        generated_sql: str,
        *,
        error: str = "",
    ) -> EvaluationResult:
        if error:
            result = EvaluationResult(
                case_id=case.id,
                question=case.question,
                difficulty=case.difficulty,
                gold_sql=case.gold_sql,
                generated_sql=generated_sql,
                passed=False,
                match_type="failed",
                error=error,
            )
        elif sqls_match(case.gold_sql, generated_sql):
            result = EvaluationResult(
                case_id=case.id,
                question=case.question,
                difficulty=case.difficulty,
                gold_sql=case.gold_sql,
                generated_sql=generated_sql,
                passed=True,
                match_type="normalized",
            )
        else:
            result = EvaluationResult(
                case_id=case.id,
                question=case.question,
                difficulty=case.difficulty,
                gold_sql=case.gold_sql,
                generated_sql=generated_sql,
                passed=False,
                match_type="mismatch",
            )
        self.results.append(result)
        return result

    # ------------------------------------------------------------------
    # 统计报告
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """返回按难度分级的通过率统计。"""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)

        by_difficulty: dict[str, dict[str, int]] = {}
        for r in self.results:
            bucket = by_difficulty.setdefault(r.difficulty, {"total": 0, "passed": 0})
            bucket["total"] += 1
            if r.passed:
                bucket["passed"] += 1

        rates: dict[str, float] = {
            "overall": passed / total if total else 0.0,
        }
        for diff, counts in by_difficulty.items():
            rates[diff] = counts["passed"] / counts["total"] if counts["total"] else 0.0

        return {
            "total": total,
            "passed": passed,
            "rates": rates,
            "by_difficulty": by_difficulty,
        }

    def assert_thresholds(self, thresholds: dict[str, float] | None = None) -> None:
        """断言通过率达到阈值，用于 CI 门禁。"""
        t = thresholds or self.THRESHOLDS
        stats = self.summary()
        rates = stats["rates"]
        failures: list[str] = []
        for key, threshold in t.items():
            actual = rates.get(key, 0.0)
            if actual < threshold:
                failures.append(
                    f"  [{key}] 期望 ≥{threshold:.0%}，实际 {actual:.1%}"
                )
        if failures:
            report = "\n".join(failures)
            raise AssertionError(f"准确率未达验收阈值：\n{report}")

    def print_report(self) -> None:
        """打印可读的评估报告（用于 CI 输出）。"""
        stats = self.summary()
        print("\n" + "=" * 60)
        print(f"SQL 准确率评估报告  共 {stats['total']} 条用例")
        print("=" * 60)
        rates = stats["rates"]
        print(f"  总体：{rates.get('overall', 0):.1%}  "
              f"({stats['passed']}/{stats['total']})")
        for diff in ("easy", "medium", "hard"):
            if diff in rates:
                bucket = stats["by_difficulty"][diff]
                print(f"  {diff:8s}：{rates[diff]:.1%}  "
                      f"({bucket['passed']}/{bucket['total']})")
        failed = [r for r in self.results if not r.passed]
        if failed:
            print("\n未通过用例：")
            for r in failed[:20]:  # 最多显示20条
                print(f"  [{r.difficulty}] {r.case_id}: {r.question[:40]}...")
                if r.error:
                    print(f"    错误: {r.error}")
        print("=" * 60)
