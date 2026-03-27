"""
ORM 模型包

所有模型通过此包统一导出，供 Alembic 自动检测表变更。
"""
from app.db.models.user import User
from app.db.models.datasource import Datasource, FieldMapping
from app.db.models.knowledge import DataDictionary, FewShotExample
from app.db.models.audit import AuditLog
from app.db.models.conversation import ConversationHistory

__all__ = [
    "User",
    "Datasource",
    "FieldMapping",
    "DataDictionary",
    "FewShotExample",
    "AuditLog",
    "ConversationHistory",
]
