"""
自定义异常体系

所有业务异常继承自 AIQueryBaseException，
FastAPI 异常处理器捕获并映射为统一错误码响应。
"""
from __future__ import annotations


class AIQueryBaseException(Exception):
    """所有业务异常的基类"""

    code: int = 5000
    default_message: str = "系统内部错误"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


# ---- Excel 相关 ----

class ExcelParseError(AIQueryBaseException):
    """Excel 文件解析失败"""
    code = 1012
    default_message = "Excel 文件解析失败"


class ExcelFileTooLargeError(AIQueryBaseException):
    """Excel 文件超过大小限制"""
    code = 1011
    default_message = "Excel 文件超过大小限制（最大 50MB）"


class UnsupportedFormatError(AIQueryBaseException):
    """不支持的文件格式"""
    code = 1010
    default_message = "不支持的文件格式，请上传 .xlsx / .xls / .csv 文件"


class FieldMappingError(AIQueryBaseException):
    """字段映射失败"""
    code = 1012
    default_message = "字段映射处理失败"


# ---- SQL 生成与执行相关 ----

class SQLGenerationError(AIQueryBaseException):
    """SQL 生成失败（LLM 无法理解问题）"""
    code = 1001
    default_message = "无法理解您的问题，请尝试换一种表达方式"


class SQLSafetyViolationError(AIQueryBaseException):
    """SQL 安全校验不通过"""
    code = 1002
    default_message = "生成的查询包含不安全操作，已被系统拦截"


class SQLExecutionError(AIQueryBaseException):
    """SQL 执行失败"""
    code = 5000
    default_message = "查询执行失败"


class QueryTimeoutError(AIQueryBaseException):
    """查询超时"""
    code = 1004
    default_message = "查询超时，请尝试缩小查询范围"


# ---- 权限相关 ----

class DataPermissionError(AIQueryBaseException):
    """数据域权限不足"""
    code = 1003
    default_message = "您没有权限访问该数据域"


class AuthenticationError(AIQueryBaseException):
    """未认证"""
    code = 4001
    default_message = "未认证，请先登录"


class AuthorizationError(AIQueryBaseException):
    """无权限执行此操作"""
    code = 4003
    default_message = "无权限执行此操作"


# ---- LLM 调用相关 ----

class LLMAPIError(AIQueryBaseException):
    """LLM API 调用失败"""
    code = 1005
    default_message = "AI 模型服务暂时不可用，请稍后重试"


class LLMAllFallbackExhaustedError(LLMAPIError):
    """所有 LLM 模型均不可用"""
    code = 1005
    default_message = "所有 AI 模型均不可用，请联系管理员"


class LLMTokenBudgetExceededError(LLMAPIError):
    """Token 预算超限"""
    code = 1005
    default_message = "今日查询次数已达上限，请明日再试"


# ---- 数据资源相关 ----

class DatasourceNotFoundError(AIQueryBaseException):
    """数据源不存在"""
    code = 4004
    default_message = "数据源不存在"


class UserNotFoundError(AIQueryBaseException):
    """用户不存在"""
    code = 4004
    default_message = "用户不存在"
