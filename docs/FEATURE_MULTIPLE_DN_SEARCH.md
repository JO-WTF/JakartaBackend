# 功能：支持多 DN 号码搜索

## 概述

为 `GET /api/dn/list/search` 接口增加了对多个 DN 号码的筛选支持。现在用户可以在一次请求中同时查询多个 DN 号码的信息。

## 修改内容

### 1. API 接口修改 (`app/api/dn/list.py`)

- **参数类型变更**：
  - 旧：`dn_number: str | None`
  - 新：`dn_number: Optional[List[str]]`

- **处理逻辑**：
  - 使用 `normalize_batch_dn_numbers()` 函数统一处理多个 DN 号码
  - 支持重复参数和逗号分隔两种输入方式
  - 自动去重和格式规范化
  - 自动验证 DN 号码格式

### 2. CRUD 函数修改 (`app/crud.py`)

- **函数签名变更**：
  - 旧：`dn_number: str | None = None`
  - 新：`dn_numbers: Sequence[str] | None = None`

- **查询逻辑**：
  - 旧：`DN.dn_number == dn_number` (精确匹配单个)
  - 新：`DN.dn_number.in_(dn_numbers)` (支持多个匹配)

### 3. 文档更新 (`docs/API_REFERENCE.md`)

- 更新了参数说明，标注支持多个 DN 号码
- 添加了多种使用场景的示例代码

## 使用方法

### 单个 DN 号码查询

```bash
GET /api/dn/list/search?dn_number=JKT001-20241007
```

### 多个 DN 号码查询 - 重复参数方式

```bash
GET /api/dn/list/search?dn_number=JKT001-20241007&dn_number=JKT002-20241007
```

### 多个 DN 号码查询 - 逗号分隔方式

```bash
GET /api/dn/list/search?dn_number=JKT001-20241007,JKT002-20241007,JKT003-20241007
```

### 混合方式

```bash
GET /api/dn/list/search?dn_number=JKT001-20241007,JKT002-20241007&dn_number=JKT003-20241007
```

### 与其他筛选条件组合

```bash
GET /api/dn/list/search?dn_number=JKT001-20241007&dn_number=JKT002-20241007&status=Delivered&lsp=LSP-A
```

## 特性说明

### 1. 自动格式规范化

输入的 DN 号码会自动：
- 去除首尾空白字符
- 统一格式（使用 `normalize_dn()` 函数）
- 验证格式是否符合规范（使用正则表达式 `DN_RE`）

### 2. 自动去重

如果传入重复的 DN 号码，系统会自动去重：

```bash
# 以下请求实际只会查询一个 DN
GET /api/dn/list/search?dn_number=JKT001-20241007&dn_number=JKT001-20241007
```

### 3. 格式验证

如果传入的 DN 号码格式不正确，请求会失败但不会中断（返回空结果）：

```bash
# 格式错误的 DN 号码会被忽略
GET /api/dn/list/search?dn_number=INVALID_DN
```

返回：
```json
{
  "ok": true,
  "total": 0,
  "page": 1,
  "page_size": 20,
  "items": []
}
```

## 测试覆盖

创建了全面的测试套件 (`tests/test_search_multiple_dn_numbers.py`)，包含：

1. ✅ 单个 DN 号码查询
2. ✅ 多个 DN 号码查询（重复参数）
3. ✅ 多个 DN 号码查询（逗号分隔）
4. ✅ 混合格式查询
5. ✅ 与其他筛选条件组合
6. ✅ 带有空白字符的处理
7. ✅ 不存在的 DN 号码
8. ✅ 重复值去重
9. ✅ 分页支持
10. ✅ 无筛选条件（返回所有）

所有测试均通过 ✅

## 示例响应

### 请求

```bash
GET /api/dn/list/search?dn_number=JKT001-20241007&dn_number=JKT002-20241007
```

### 响应

```json
{
  "ok": true,
  "total": 2,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": 1,
      "dn_number": "JKT001-20241007",
      "status": "Delivered",
      "status_delivery": "POD",
      "plan_mos_date": "2024-10-07",
      "lsp": "LSP-A",
      "region": "Jakarta",
      ...
    },
    {
      "id": 2,
      "dn_number": "JKT002-20241007",
      "status": "In Transit",
      "status_delivery": "On the way",
      "plan_mos_date": "2024-10-07",
      "lsp": "LSP-A",
      "region": "Jakarta",
      ...
    }
  ]
}
```

## 性能考虑

- 使用 SQL `IN` 子句进行批量查询，性能优于多次单独查询
- 自动去重减少不必要的数据库查询
- 与现有的分页机制完全兼容

## 相关文件

- `app/api/dn/list.py` - API 路由定义
- `app/crud.py` - 数据库查询逻辑
- `app/utils/query.py` - 查询参数处理工具
- `tests/test_search_multiple_dn_numbers.py` - 功能测试
- `docs/API_REFERENCE.md` - API 文档

---

**作者**: GitHub Copilot  
**日期**: 2025-10-07  
**版本**: 1.0.0
