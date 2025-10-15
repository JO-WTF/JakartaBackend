# JakartaBackend API 接口文档

## 目录
- [概述](#概述)
- [通用说明](#通用说明)
- [健康检查](#健康检查)
- [DN 管理接口](#dn-管理接口)
- [车辆管理接口](#车辆管理接口)
- [数据模型](#数据模型)
- [错误码](#错误码)

---

## 概述

JakartaBackend 提供 RESTful API 用于物流配送单据 (DN) 管理和车辆调度。所有接口返回 JSON 格式数据。

**Base URL**: `http://your-domain.com`  
**API Version**: `1.1.0`

---

## 通用说明

### 响应格式

成功响应通常包含 `ok: true` 字段:
```json
{
  "ok": true,
  "data": {...}
}
```

失败响应包含错误信息:
```json
{
  "ok": false,
  "error": "error_code",
  "detail": "Error description"
}
```

### 时区说明

所有日期时间字段使用 **雅加达时区 (GMT+7)**。

### Status Delivery 规范化

所有 `status_delivery` 值会自动规范化为标准格式:

| 输入 (任意大小写) | 规范化输出 |
|------------------|-----------|
| `"on the way"` / `"ON THE WAY"` / `"On The Way"` | `"On the way"` |
| `"prepare vehicle"` / `"PREPARE VEHICLE"` | `"Prepare Vehicle"` |
| `"on site"` / `"ON SITE"` | `"On Site"` |
| `"pod"` / `"POD"` | `"POD"` |
| `"close by rn"` / `"CLOSE BY RN"` | `"Close by RN"` |

标准状态值列表:
- `Prepare Vehicle`
- `On the way`
- `On Site`
- `POD`
- `Waiting PIC Feedback`
- `RePlan MOS due to LSP Delay`
- `RePlan MOS Project`
- `Cancel MOS`
- `Close by RN`
- `No Status` (空值或无效值的默认值)

---

## 健康检查

### GET /

检查 API 服务健康状态。

**响应**:
```json
{
  "ok": true,
  "message": "You can use admin panel now."
}
```

---

## DN 管理接口

### 1. DN 列表查询

#### GET /api/dn/list

获取 DN 列表。

**查询参数**:
- `page` (int, 可选): 页码，默认 1
- `page_size` (int, 可选): 每页数量，默认 20

**响应**:
```json
{
  "ok": true,
  "total": 150,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "dn_number": "DN001",
      "status": "ON THE WAY",
      "status_delivery": "On the way",
      "plan_mos_date": "01 Jan 25",
      "lsp": "LSP Name",
      ...
    }
  ]
}
```

---

### 2. DN 高级搜索

#### GET /api/dn/list/search

支持多条件筛选的 DN 列表查询。

**查询参数**:
- `dn_number` (string[], 可选): DN 号码筛选 (支持多个，可使用重复参数或逗号分隔)
- `status` (string[], 可选): 状态筛选 (可多选)
- `status_delivery` (string[], 可选): 配送状态筛选 (可多选)
- `lsp` (string[], 可选): 物流服务商筛选 (可多选)
- `region` (string[], 可选): 区域筛选 (可多选)
- `date[]` (string[], 可选): 日期范围，格式 `["2025-01-01", "2025-01-31"]`
- `status_not_empty` (boolean, 可选): 是否过滤空状态
- `has_coordinate` (boolean, 可选): 是否有坐标信息
- `show_deleted` (boolean, 可选): 是否显示已软删除的记录，默认 `false`
- `page` (int, 可选): 页码，默认 1
- `page_size` (int/string, 可选): 每页数量，默认 20，可设置为 `"all"` 获取所有记录

**响应**:
```json
{
  "ok": true,
  "total": 45,
  "page": 1,
  "page_size": 20,
  "items": [...]
}
```

**新功能说明**:

1. **软删除过滤** (`show_deleted`):
   - `false` (默认): 只返回未删除的记录
   - `true`: 返回所有记录，包括已软删除的记录 (`is_deleted = "Y"`)
   - 用于管理员查看已删除数据或数据审计

2. **无限分页** (`page_size="all"`):
   - 设置 `page_size=all` 可获取所有匹配记录
   - 自动将 `page` 设置为 1
   - 最大限制 2000 条（超过需使用 `"all"`）

**使用示例**:
```bash
# 显示已删除记录
GET /api/dn/list/search?show_deleted=true

# 获取所有 POD 状态的记录（包括已删除）
GET /api/dn/list/search?status=POD&show_deleted=true&page_size=all

# 标准查询（不含已删除）
GET /api/dn/list/search?status=POD&page=1&page_size=20

# 查询单个 DN 号码
GET /api/dn/list/search?dn_number=JKT001-20241007

# 查询多个 DN 号码（使用重复参数）
GET /api/dn/list/search?dn_number=JKT001-20241007&dn_number=JKT002-20241007

# 查询多个 DN 号码（使用逗号分隔）
GET /api/dn/list/search?dn_number=JKT001-20241007,JKT002-20241007,JKT003-20241007

# 组合多个 DN 号码与其他筛选条件
GET /api/dn/list/search?dn_number=JKT001-20241007&dn_number=JKT002-20241007&status=Delivered&lsp=LSP-A

# 混合重复参数和逗号分隔方式
GET /api/dn/list/search?dn_number=JKT001-20241007,JKT002-20241007&dn_number=JKT003-20241007
```

---

### 3. DN 单个查询

#### GET /api/dn/{dn_number}

根据 DN 号码获取单个 DN 详情。

**路径参数**:
- `dn_number` (string): DN 号码

**响应**:
```json
{
  "ok": true,
  "data": {
    "dn_number": "DN001",
    "status": "ON THE WAY",
    "status_delivery": "On the way",
    "plan_mos_date": "01 Jan 25",
    "lsp": "LSP Name",
    "region": "Jakarta",
    "lng": "106.8456",
    "lat": "-6.2088",
    ...
  }
}
```

**错误响应**:
```json
{
  "detail": "DN not found"
}
```
Status: 404

---

### 4. DN 批量查询

#### GET /api/dn/batch

批量获取多个 DN 信息。

**查询参数**:
- `dn_numbers` (string): 逗号分隔的 DN 号码列表，如 `"DN001,DN002,DN003"`

**响应**:
```json
{
  "ok": true,
  "data": [
    {
      "dn_number": "DN001",
      ...
    },
    {
      "dn_number": "DN002",
      ...
    }
  ]
}
```

---

### 5. DN 搜索 (简单)

#### GET /api/dn/search

简单的 DN 号码搜索。

**查询参数**:
- `dn_number` (string): DN 号码 (支持部分匹配)

**响应**:
```json
{
  "ok": true,
  "data": [
    {
      "dn_number": "DN001",
      ...
    }
  ]
}
```

---

### 6. DN 更新

#### POST /api/dn/update

更新 DN 状态和信息 (支持照片上传)。

**Content-Type**: `multipart/form-data`

**表单参数**:
- `dnNumber` (string, 必需): DN 号码
- `status` (string, 必需): 新状态
- `delivery_status` (string, 可选): 配送状态 (自动规范化)
- `status_delivery` (string, 可选): 配送状态别名 (向后兼容)
- `remark` (string, 可选): 备注
- `photo` (file, 可选): 照片文件
- `lng` (string/float, 可选): 经度
- `lat` (string/float, 可选): 纬度
- `updated_by` (string, 可选): 更新人

**有效状态值**:
- `PREPARE VEHICLE`
- `ON THE WAY`
- `ON SITE`
- `POD`
- `REPLAN MOS PROJECT`
- `WAITING PIC FEEDBACK`
- `REPLAN MOS DUE TO LSP DELAY`
- `CLOSE BY RN`
- `CANCEL MOS`
- `NO STATUS`
- `NEW MOS`
- `ARRIVED AT WH`
- `TRANSPORTING FROM WH`
- `ARRIVED AT XD/PM`
- `TRANSPORTING FROM XD/PM`
- `ARRIVED AT SITE`
- `开始运输`
- `运输中`
- `已到达`
- `过夜`

**响应**:
```json
{
  "ok": true,
  "id": 123,
  "photo": "https://bucket.s3.amazonaws.com/uploads/photo.jpg",
  "delivery_status_update_result": {
    "sheet": "Plan MOS 2025",
    "row": 10,
    "updated": true
  },
  "timestamp_update_result": {
    "sheet": "Plan MOS 2025",
    "row": 10,
    "column": 19,
    "column_name": "actual_arrive_time_ata",
    "new_value": "10/2/2025 7:10:00",
    "status": "ARRIVED AT SITE",
    "updated": true
  }
}
```

**响应字段说明**:
- `delivery_status_update_result`: 配送状态同步结果
- `timestamp_update_result`: 时间戳写入结果
  - `column`: 列位置 (18 = R列, 19 = S列)
  - `column_name`: 列名称
  - `new_value`: 写入的时间戳值
  - `status`: 触发写入的状态

**说明**:
- 自动创建 DN 记录历史
- 如果提供坐标和照片,同时存储
- 如果 DN 在 Google Sheets 中,自动同步 `status_delivery` 回表格
- **自动写入时间戳到 Google Sheet**:
  - **到达时间戳** (写入 **S 列** `actual_arrive_time_ata`):
    - `ARRIVED AT SITE` - 到达 site
    - `ARRIVED AT XD/PM` - 到达 XD/PM
  - **出发时间戳** (写入 **R 列** `actual_depart_from_start_point_atd`):
    - `TRANSPORTING FROM WH` - 从 WH 出发
    - `TRANSPORTING FROM XD/PM` - 从 XD/PM 出发
  - 其他状态（如 `POD`、`ON THE WAY` 等）不会触发时间戳写入
  - 时间格式: `M/D/YYYY H:MM:SS` (GMT+7),例如: `10/2/2025 7:10:00`
  - 同步逻辑也会将相同时间戳写入数据库字段 `actual_depart_from_start_point_atd` / `actual_arrive_time_ata`
  - 状态匹配不区分大小写
- 所有修改的单元格会自动添加:
  - 备注: "Modified by Fast Tracker ({updated_by})" （显示操作者名称）
  - 链接: https://idnsc.dpdns.org/admin

**重要提示**:
- 上传 `POD` 状态时，建议同时提供 `delivery_status="POD"` 以保持数据一致性
- 如果不提供 `delivery_status`，系统会根据 `status` 自动设置:
  - `status="ARRIVED AT SITE"` → `delivery_status="On Site"`
  - 其他状态 → `delivery_status="On the way"`

---

### 7. DN 批量创建

#### POST /api/dn/batch_update

批量创建新的 DN 记录。

**Content-Type**: `application/json`

**请求体**:
```json
{
  "dn_numbers": ["DN001", "DN002", "DN003"]
}
```

**响应**:
```json
{
  "status": "ok",
  "success_count": 2,
  "failure_count": 1,
  "success_dn_numbers": ["DN001", "DN002"],
  "failure_details": {
    "DN003": "DN number 已存在"
  }
}
```

**错误类型**:
- `"无效的 DN number"`: DN 格式不正确
- `"DN number 已存在"`: DN 已在数据库中
- `"请求中重复"`: 同一请求中有重复的 DN

---

### 8. DN 记录编辑

#### PUT /api/dn/update/{id}

更新指定 DN 历史记录。

**Content-Type**: `multipart/form-data` 或 `application/json`

**路径参数**:
- `id` (int): 记录 ID

**表单/JSON 参数**:
- `status` (string, 可选): 状态
- `remark` (string, 可选): 备注
- `updated_by` (string, 可选): 更新人
- `photo` (file, 可选): 照片

**响应**:
```json
{
  "ok": true,
  "id": 123
}
```

---

### 9. DN 记录删除

#### DELETE /api/dn/update/{id}

删除指定 DN 历史记录。

**路径参数**:
- `id` (int): 记录 ID

**响应**:
```json
{
  "ok": true
}
```

---

### 10. DN 软删除

#### DELETE /api/dn/{dn_number}

软删除指定 DN (设置 `is_deleted = "Y"`)。

**路径参数**:
- `dn_number` (string): DN 号码

**响应**:
```json
{
  "ok": true
}
```

**说明**: 
- 软删除的 DN 不会在列表查询中显示
- 数据仍保留在数据库中,可以通过 Google Sheets 同步恢复

---

### 11. DN 记录历史

#### GET /api/dn/records

获取 DN 的历史记录列表。

**查询参数**:
- `dn_number` (string, 可选): DN 号码
- `page` (int, 可选): 页码，默认 1
- `page_size` (int, 可选): 每页数量，默认 20

**响应**:
```json
{
  "ok": true,
  "total": 5,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": 123,
      "dn_number": "DN001",
      "status": "ON THE WAY",
      "remark": "Driver called",
      "photo_url": "https://...",
      "lng": "106.8456",
      "lat": "-6.2088",
      "created_at": "2025-01-01T08:00:00+07:00",
      "updated_by": "admin"
    }
  ]
}
```

---

### 12. DN 批量列表

#### GET /api/dn/list/batch

批量获取多个 DN 的完整信息。

**查询参数**:
- `dn_numbers` (string): 逗号分隔的 DN 号码

**响应**:
```json
{
  "ok": true,
  "data": [...]
}
```

---

### 13. 统计接口 - 按日期

#### GET /api/dn/stats/{date}

获取指定日期的 DN 状态统计。

**路径参数**:
- `date` (string): 日期，格式 `"01 Jan 25"` 或 `"2025-01-01"`

**响应**:
```json
{
  "ok": true,
  "data": [
    {
      "group": "Total",
      "date": "01 Jan 25",
      "values": [10, 25, 15, 5, 8, 3, 2, 1, 1, 70]
    }
  ]
}
```

**说明**: `values` 数组按以下顺序排列:
1. Prepare Vehicle
2. On the way
3. On Site
4. POD
5. Waiting PIC Feedback
6. RePlan MOS due to LSP Delay
7. RePlan MOS Project
8. Cancel MOS
9. Close by RN
10. 总计

---

### 14. 统计接口 - 状态配送

#### GET /api/dn/status-delivery/stats

获取 DN 状态配送统计和 LSP 汇总。

**查询参数**:
- `lsp` (string, 可选): 物流服务商筛选
- `plan_mos_date` (string, 可选): 计划日期，默认当前日期 (雅加达时间)

**响应**:
```json
{
  "ok": true,
  "data": [
    {
      "status_delivery": "On the way",
      "count": 25
    },
    {
      "status_delivery": "On Site",
      "count": 15
    },
    {
      "status_delivery": "POD",
      "count": 30
    }
  ],
  "total": 70,
  "lsp_summary": [
    {
      "lsp": "LSP Alpha",
      "total_dn": 40,
      "status_not_empty": 35
    },
    {
      "lsp": "LSP Beta",
      "total_dn": 30,
      "status_not_empty": 25
    }
  ]
}
```

**说明**:
- `total_dn`: LSP 的 DN 总数 (仅统计状态为 On the way, On Site, POD 的 DN)
- `status_not_empty`: 有状态的 DN 数量

---

### 15. LSP 汇总历史记录

#### GET /api/dn/status-delivery/lsp-summary-records

获取 LSP 汇总的历史数据 (每小时捕获一次)。

**查询参数**:
- `lsp` (string, 可选): 物流服务商筛选
- `limit` (int, 可选): 返回记录数，默认 5000，范围 1-10000

**响应**:
```json
{
  "ok": true,
  "data": {
    "by_plan_mos_date": [
      {
        "id": 1,
        "lsp": "LSP Alpha",
        "total_dn": 40,
        "status_not_empty": 35,
        "plan_mos_date": "01 Jan 25",
        "recorded_at": "2025-01-01T08:00:00+07:00"
      }
    ],
    "by_update_date": [
      {
        "id": 1,
        "lsp": "LSP Alpha",
        "updated_dn": 40,
        "update_date": "01 Jan 25",
        "recorded_at": "2025-01-01 08:00:00"
      },
      {
        "id": 2,
        "lsp": "NO_LSP",
        "updated_dn": 12,
        "update_date": "01 Jan 25",
        "recorded_at": "2025-01-01 09:00:00"
      }
    ]
  }
}
```

**说明**:
- `by_plan_mos_date`: 仍然返回按小时捕获的 LSP 快照，数据按 `recorded_at` 降序排列 (最新的在前)
- `by_update_date`: 基于每条 DN 的最新记录时间 (换算为雅加达时区并取整到小时) 计算的累计 DN 数
  - `updated_dn`: 截至该小时为止某 LSP 的累计 DN 数量
  - `update_date`: 雅加达当地日期 (格式 `%d %b %y`)
  - `recorded_at`: 雅加达当地小时 (格式 `YYYY-MM-DD HH:mm:ss`)
- 每天从 `00:00` 开始补齐到参考小时，缺少数据的小时返回 0
- 如果中间时段没有新增记录，会自动沿用上一小时的累计值
- 跨越新的一天时会重新从 0 开始累计，避免沿用上一天的值
- 默认返回最近 5000 条 Plan MOS 快照；`by_update_date` 始终返回所有符合条件的最新数据

---

### 16. 筛选选项

#### GET /api/dn/filters

获取所有可用的筛选选项 (LSP、区域、状态等)。

**响应**:
```json
{
  "ok": true,
  "data": {
    "lsp": ["LSP Alpha", "LSP Beta", "LSP Gamma"],
    "region": ["Jakarta", "Bandung", "Surabaya"],
    "status": ["ON THE WAY", "ON SITE", "POD"],
    "status_delivery": ["On the way", "On Site", "POD"],
    "total": 150
  }
}
```

---

### 17. 数据同步

#### POST /api/dn/sync

手动触发与 Google Sheets 的数据同步。

**响应**:
```json
{
  "ok": true,
  "synced": 150,
  "created": 5,
  "updated": 10,
  "ignored": 135
}
```

**说明**:
- 自动同步每 5 分钟执行一次
- 同步过程包括:
  1. 从 Google Sheets 读取所有 "Plan MOS" 工作表
  2. 数据规范化处理
  3. 与数据库比对
  4. 增量更新
  5. 软删除不在表格中的 DN

---

### 18. 同步日志

#### GET /api/dn/sync/log/latest

获取最近的同步日志。

**响应**:
```json
{
  "ok": true,
  "data": {
    "id": 123,
    "started_at": "2025-01-01T08:00:00+07:00",
    "ended_at": "2025-01-01T08:00:15+07:00",
    "synced_count": 150,
    "error_message": null
  }
}
```

---

### 19. 同步日志文件

#### GET /api/dn/sync/log/file

下载完整的同步日志文件。

**响应**: 文本文件 (Content-Type: text/plain)

---

### 20. 动态列扩展

#### POST /api/dn/extend

扩展 DN 表的动态列。

**Content-Type**: `application/json`

**请求体**:
```json
{
  "column_name": "new_field",
  "column_type": "string"
}
```

**响应**:
```json
{
  "ok": true,
  "message": "Column added successfully"
}
```

**支持的列类型**:
- `string` / `text`
- `integer` / `int`
- `float` / `decimal`
- `boolean` / `bool`
- `date`
- `datetime`

---

### 21. 数据归档标记

#### POST /api/dn/mark

标记超过指定天数的 POD 记录为归档状态 (灰色文本)。

**Content-Type**: `application/json`

**请求体**:
```json
{
  "threshold_days": 7
}
```

**响应**:
```json
{
  "ok": true,
  "threshold_days": 7,
  "threshold_date": "2024-12-25",
  "matched_rows": 50,
  "formatted_rows": 50,
  "sheets_processed": ["Plan MOS 2025", "Plan MOS 2024"],
  "affected_rows": [
    {
      "sheet": "Plan MOS 2025",
      "row": 10,
      "plan_mos_date": "20 Dec 24",
      "status_delivery": "POD",
      "formatted": true
    }
  ]
}
```

**说明**:
- 仅标记 `status_delivery = "POD"` 且 `plan_mos_date` 超过阈值的行
- 默认阈值为 7 天
- 直接在 Google Sheets 中修改文本颜色为灰色

---

## 车辆管理接口

### 1. 车辆签到

#### POST /api/vehicle/signin

登记车辆到达信息。

**Content-Type**: `application/json`

**请求体**:
```json
{
  "vehiclePlate": "B1234ABC",
  "lsp": "LSP Alpha",
  "vehicleType": "Truck",
  "driverName": "John Doe",
  "driverContact": "081234567890",
  "arriveTime": "2025-01-01T08:00:00+07:00"
}
```

**响应**:
```json
{
  "ok": true,
  "data": {
    "vehiclePlate": "B1234ABC",
    "lsp": "LSP Alpha",
    "vehicleType": "Truck",
    "driverName": "John Doe",
    "contactNumber": "081234567890",
    "LSP": "LSP Alpha",
    "status": "arrived",
    "arriveTime": "2025-01-01T08:00:00+07:00",
    "departTime": null,
    "createdAt": "2025-01-01T08:00:00+07:00",
    "updatedAt": "2025-01-01T08:00:00+07:00"
  }
}
```

**说明**:
- 车牌号作为唯一标识
- 如果车辆已签到,会更新现有记录
- 自动设置状态为 `"arrived"`

---

### 2. 车辆离场

#### POST /api/vehicle/depart

登记车辆离场时间。

**Content-Type**: `application/json`

**请求体**:
```json
{
  "vehiclePlate": "B1234ABC",
  "departTime": "2025-01-01T18:00:00+07:00"
}
```

**响应**:
```json
{
  "ok": true,
  "data": {
    "vehiclePlate": "B1234ABC",
    "status": "departed",
    "arriveTime": "2025-01-01T08:00:00+07:00",
    "departTime": "2025-01-01T18:00:00+07:00",
    ...
  }
}
```

**错误响应**:
```json
{
  "detail": "Vehicle not found"
}
```
Status: 404

---

### 3. 车辆单个查询

#### GET /api/vehicle/vehicle

查询单个车辆信息。

**查询参数**:
- `vehicle_plate` (string, 必需): 车牌号

**响应**:
```json
{
  "ok": true,
  "data": {
    "vehiclePlate": "B1234ABC",
    "lsp": "LSP Alpha",
    "status": "arrived",
    ...
  }
}
```

---

### 4. 车辆列表查询

#### GET /api/vehicle/vehicles

查询车辆列表 (支持筛选)。

**查询参数**:
- `status` (string, 可选): 状态筛选 (`"arrived"` / `"departed"`)
- `date` (string, 可选): 日期筛选，格式 `"2025-01-01"`
- `lsp` (string, 可选): LSP 筛选

**响应**:
```json
{
  "ok": true,
  "data": [
    {
      "vehiclePlate": "B1234ABC",
      "lsp": "LSP Alpha",
      "status": "arrived",
      "arriveTime": "2025-01-01T08:00:00+07:00",
      ...
    }
  ]
}
```

---

## 数据模型

### DN (Delivery Note)

```typescript
interface DN {
  dn_number: string;           // DN 号码 (主键)
  status: string;              // 当前状态
  status_delivery: string;     // 配送状态 (自动规范化)
  plan_mos_date: string;       // 计划日期 "01 Jan 25"
  lsp: string;                 // 物流服务商
  region: string;              // 区域
  remark: string;              // 备注
  photo_url: string;           // 照片 URL
  lng: string;                 // 经度
  lat: string;                 // 纬度
  last_updated_by: string;     // 最后更新人
  gs_sheet: string;            // Google Sheet 名称
  gs_row: number;              // 行号
  is_deleted: "Y" | "N";       // 软删除标记
  // ... 其他动态列
}
```

### DNRecord (DN 历史记录)

```typescript
interface DNRecord {
  id: number;                  // 记录 ID (主键)
  dn_number: string;           // DN 号码 (外键)
  status: string;              // 状态
  remark: string;              // 备注
  photo_url: string;           // 照片 URL
  lng: string;                 // 经度
  lat: string;                 // 纬度
  created_at: string;          // 创建时间 (ISO 8601)
  updated_by: string;          // 更新人
}
```

### Vehicle (车辆)

```typescript
interface Vehicle {
  vehicle_plate: string;       // 车牌号 (主键)
  lsp: string;                 // 物流服务商
  vehicle_type: string;        // 车辆类型
  driver_name: string;         // 司机姓名
  contact_number: string;      // 联系电话
  status: "arrived" | "departed"; // 状态
  arrive_time: string;         // 到达时间 (ISO 8601)
  depart_time: string;         // 离场时间 (ISO 8601)
  created_at: string;          // 创建时间
  updated_at: string;          // 更新时间
}
```

### StatusDeliveryLspStat (LSP 统计快照)

```typescript
interface StatusDeliveryLspStat {
  id: number;                  // ID (主键)
  lsp: string;                 // 物流服务商
  total_dn: number;            // DN 总数 (仅统计状态为 On the way, On Site, POD 的 DN)
  status_not_empty: number;    // 有状态的 DN 数
  plan_mos_date: string;       // 计划日期
  recorded_at: string;         // 记录时间 (ISO 8601)
}
```

---

## 错误码

### HTTP 状态码

| 状态码 | 说明 |
|-------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |

### 业务错误码

| 错误码 | 说明 |
|-------|------|
| `invalid_dn_number` | DN 号码格式无效 |
| `invalid_status` | 状态值无效 |
| `dn_not_found` | DN 不存在 |
| `vehicle_not_found` | 车辆不存在 |
| `internal_error` | 内部错误 |

---
