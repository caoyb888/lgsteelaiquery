"""
测试数据工厂（factory-boy）

用于快速生成测试所需的 ORM 对象，避免在每个测试中重复构造数据。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import factory
from factory import Faker

from app.db.models.user import User
from app.db.models.datasource import Datasource, FieldMapping
from app.db.models.knowledge import DataDictionary, FewShotExample
from app.db.models.audit import AuditLog


class UserFactory(factory.Factory):
    class Meta:
        model = User

    id = factory.LazyFunction(uuid.uuid4)
    username = factory.Sequence(lambda n: f"user_{n:04d}")
    display_name = Faker("name", locale="zh_CN")
    email = factory.LazyAttribute(lambda o: f"{o.username}@lgsteel.internal")
    password_hash = "bcrypt_placeholder_test_hash"
    role = "analyst"
    is_active = True
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    updated_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class AdminUserFactory(UserFactory):
    role = "admin"
    username = factory.Sequence(lambda n: f"admin_{n:04d}")


class FinanceUserFactory(UserFactory):
    role = "finance_user"
    username = factory.Sequence(lambda n: f"finance_{n:04d}")


class DataManagerFactory(UserFactory):
    role = "data_manager"
    username = factory.Sequence(lambda n: f"data_manager_{n:04d}")


class DatasourceFactory(factory.Factory):
    class Meta:
        model = Datasource

    id = factory.LazyFunction(uuid.uuid4)
    name = factory.Sequence(lambda n: f"销售台账_202603_{n:02d}.xlsx")
    domain = "sales"
    original_filename = factory.LazyAttribute(lambda o: o.name)
    file_path = factory.LazyAttribute(lambda o: f"/app/files/excel/{o.id}.xlsx")
    file_size_bytes = 1024 * 500  # 500KB
    data_date = date(2026, 3, 15)
    update_mode = "replace"
    status = "active"
    biz_table_name = factory.LazyAttribute(
        lambda o: f"{o.domain}_{str(o.id).replace('-', '')[:8]}"
    )
    total_rows = 1250
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    updated_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class FieldMappingFactory(factory.Factory):
    class Meta:
        model = FieldMapping

    id = factory.LazyFunction(uuid.uuid4)
    datasource_id = factory.LazyFunction(uuid.uuid4)
    raw_name = factory.Sequence(lambda n: f"原始字段_{n:02d}")
    std_name = factory.Sequence(lambda n: f"std_field_{n:02d}")
    display_name = factory.LazyAttribute(lambda o: o.raw_name)
    field_type = "text"
    unit = None
    confidence = 0.95
    mapping_source = "embedding"
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class DataDictionaryFactory(factory.Factory):
    class Meta:
        model = DataDictionary

    id = factory.LazyFunction(uuid.uuid4)
    std_name = factory.Sequence(lambda n: f"field_{n:04d}")
    display_name = factory.Sequence(lambda n: f"字段{n:04d}")
    domain = "all"
    description = "测试字段描述"
    synonyms = factory.LazyAttribute(lambda o: [o.display_name, o.std_name])
    unit = None
    updated_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class AuditLogFactory(factory.Factory):
    class Meta:
        model = AuditLog

    id = factory.LazyFunction(uuid.uuid4)
    request_id = factory.LazyFunction(uuid.uuid4)
    user_id = factory.LazyFunction(uuid.uuid4)
    ip_address = "127.0.0.1"
    question = "查询本月销售总额"
    generated_sql = "SELECT SUM(revenue) FROM sales_orders"
    tables_accessed = ["sales_orders"]
    result_row_count = 1
    execution_ms = 250
    status = "success"
    block_reason = None
    llm_model_used = "qianwen-max"
    prompt_tokens = 150
    completion_tokens = 30
    feedback = None
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))
