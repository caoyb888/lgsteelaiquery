# Excel 测试 Fixtures

此目录存放单元测试和集成测试所需的 Excel 样本文件。

## 文件清单

| 文件名 | 说明 | 用途 |
|--------|------|------|
| `standard_sales.xlsx` | 标准格式销售台账（由 seed 脚本生成）| 正常解析测试 |
| `merged_cells.xlsx` | 含合并单元格的表格 | 合并单元格展开测试 |
| `multi_header.xlsx` | 多级表头（非标准）| 表头识别鲁棒性测试 |
| `mixed_types.xlsx` | 混合类型字段（数字/文本/日期混合）| 类型推断测试 |
| `large_file.xlsx` | 大文件（>10万行，由 seed 脚本生成）| 性能和大文件处理测试 |
| `bad_encoding.csv` | GBK 编码 CSV | 编码问题处理测试 |
| `empty_rows.xlsx` | 含空行和空列 | 空值清洗测试 |

## 生成方法

```bash
# 生成所有 Excel fixtures（需要安装 openpyxl / pandas）
cd backend
python -m tests.fixtures.excel.generate_fixtures
```

> 注意：`large_file.xlsx` 文件较大，不提交 Git（已在 .gitignore 中忽略），
> 在 CI 中按需生成。
