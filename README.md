# JakartaBackend 技术文档

## 项目概述

JakartaBackend 是一个基于 FastAPI 构建的物流管理系统后端服务，主要负责配送单据（Delivery Note，简称 DN）的管理和车辆调度。系统通过与 Google Sheets 集成，实现数据的自动化同步和处理。

### 主要功能
- **数据同步**: 与 Google Sheets 的双向数据同步 (每 5 分钟自动同步)
- **配送管理**: DN 状态跟踪和历史记录管理 (支持软删除)
- **车辆调度**: 车辆签到/离场管理
- **统计分析**: 实时数据统计和趋势分析 (每小时快照)
- **文件管理**: 照片等附件的上传和存储 (S3/本地)
- **状态规范化**: 自动规范化 status_delivery 值为标准格式
- **时区支持**: 统一使用雅加达时区 (GMT+7)
- **智能时间戳**: 根据状态自动写入时间戳到 Google Sheet (支持到达/出发场景)
- **软删除查询**: 支持查询已软删除的记录 (管理员功能)
- **全局常量管理**: 统一的常量定义，提高代码可维护性

## 技术架构

### 技术栈
- **Web 框架**: FastAPI 0.116.2 (Python 3.13+)
- **数据库**: PostgreSQL + SQLAlchemy 2.0.36 ORM
- **外部集成**: Google Sheets API (gspread)
- **任务调度**: APScheduler (AsyncIO 定时任务)
- **文件存储**: AWS S3 / 本地文件系统
- **部署**: Docker 容器化
- **测试**: Pytest 8.3.4 + pytest-asyncio 0.23.5.post1

### 架构设计
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Web Client    │    │  Google Sheets  │    │   File Storage  │
│                 │    │                 │    │   (S3/Local)   │
└─────────┬───────┘    └─────────┬───────┘    └─────────┬───────┘
          │                      │                      │
          │ HTTP/JSON            │ API                  │ Upload
          │                      │                      │
          ▼                      ▼                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI Application                         │
├─────────────────┬───────────────────────┬───────────────────────┤
│   API Routes    │    Core Business      │     Data Layer        │
│                 │       Logic           │                       │
│ • DN Management │ • Sheet Sync          │ • SQLAlchemy Models   │
│ • Vehicle APIs  │ • Data Processing     │ • Database Sessions   │
│ • Statistics    │ • Task Scheduling     │ • CRUD Operations     │
│ • File Upload   │ • Error Handling      │ • Dynamic Columns     │
└─────────────────┴───────────────────────┴───────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PostgreSQL Database                         │
│                                                                 │
│ Tables: Vehicle, DN, DNRecord, DNSyncLog, StatusDeliveryLspStat │
└─────────────────────────────────────────────────────────────────┘
```

## 项目结构

```
app/
├── main.py                 # 应用入口和配置
├── settings.py             # 环境变量和配置管理
├── constants.py            # 全局常量定义 (新增)
├── db.py                   # 数据库连接和会话管理
├── models.py               # SQLAlchemy 数据模型
├── crud.py                 # 数据库操作(CRUD)
├── storage.py              # 文件存储抽象层
├── dn_columns.py           # 动态列管理
├── time_utils.py           # 时间工具函数
├── api/                    # API 路由
│   ├── __init__.py
│   ├── health.py           # 健康检查接口
│   ├── dn/                 # DN 相关接口
│   │   ├── list.py         # DN 列表和搜索
│   │   ├── query.py        # DN 查询接口
│   │   ├── update.py       # DN 更新操作
│   │   ├── stats.py        # 统计接口
│   │   ├── sync.py         # 同步管理
│   │   ├── columns.py      # 列管理
│   │   └── archive.py      # 数据归档
│   └── vehicle/            # 车辆相关接口
│       ├── signin.py       # 车辆签到
│       ├── depart.py       # 车辆离场
│       └── query.py        # 车辆查询
├── core/                   # 核心业务逻辑
│   ├── google.py           # Google API 集成
│   ├── sheet.py            # Google Sheets 操作
│   ├── sync.py             # 数据同步逻辑
│   └── status_delivery_summary.py  # 状态统计
├── schemas/                # Pydantic 数据模型
│   ├── dn.py               # DN 相关数据模型
│   └── vehicle.py          # 车辆相关数据模型
└── utils/                  # 工具函数
    ├── logging.py          # 日志配置
    ├── query.py            # 查询辅助函数
    ├── string.py           # 字符串处理
    └── time.py             # 时间处理
```

## 数据模型

### 核心实体

#### 1. Vehicle (车辆)
```python
class Vehicle:
    vehicle_plate: str      # 车牌号 (主键)
    lsp: str               # 物流服务商
    vehicle_type: str      # 车辆类型
    driver_name: str       # 司机姓名
    driver_contact: str    # 司机联系方式
    arrive_time: datetime  # 到达时间
    depart_time: datetime  # 离场时间
    status: str           # 状态 (arrived/departed)
```

#### 2. DN (配送单据)
```python
class DN:
    dn_number: str        # DN号码 (主键)
    status: str          # 当前状态
    status_delivery: str # 配送状态 (自动规范化)
    plan_mos_date: str   # 计划日期 (格式: "01 Jan 25")
    lsp: str            # 物流服务商
    region: str         # 区域
    remark: str         # 备注
    photo_url: str      # 照片URL
    lng: str            # 经度
    lat: str            # 纬度
    last_updated_by: str # 最后更新人
    gs_sheet: str       # Google Sheet 名称
    gs_row: int         # 行号
    is_deleted: str     # 软删除标记 ("Y"/"N")
    # ... 动态列支持
```

#### 3. DNRecord (DN历史记录)
```python
class DNRecord:
    id: int              # 记录ID (主键)
    dn_number: str       # DN号码 (外键)
    status: str          # 状态
    remark: str          # 备注
    photo_url: str       # 照片URL
    lng: float          # 经度
    lat: float          # 纬度
    created_at: datetime # 创建时间
    updated_by: str     # 更新人
```

#### 4. StatusDeliveryLspStat (LSP 统计快照)
```python
class StatusDeliveryLspStat:
    id: int              # ID (主键)
    lsp: str            # 物流服务商
    total_dn: int       # DN 总数
    status_not_empty: int # 有状态的 DN 数
    plan_mos_date: str  # 计划日期
    recorded_at: datetime # 记录时间
```

### 数据关系
- Vehicle: 独立实体，按车牌号管理
- DN ↔ DNRecord: 一对多关系，DN 为主表，DNRecord 为历史记录
- StatusDeliveryLspStat: 独立时序数据表，每小时记录一次快照
- 支持动态列扩展，新增字段会自动同步到数据库和 ORM

### 软删除机制
- DN 表使用 `is_deleted` 字段实现软删除
- 默认值为 `"N"` (未删除)
- 删除时设置为 `"Y"` (已删除)
- 所有查询自动过滤已删除记录
- Google Sheets 同步时会自动恢复不在表格中的记录

## API 接口文档

**完整 API 文档请参考**: [API_REFERENCE.md](./API_REFERENCE.md)

### 接口概览

#### 健康检查
- `GET /` - 健康检查

#### DN 管理 (21 个接口)
- `GET /api/dn/list` - DN 列表
- `GET /api/dn/list/search` - 高级搜索
- `GET /api/dn/{dn_number}` - 单个查询
- `GET /api/dn/batch` - 批量查询
- `GET /api/dn/search` - 简单搜索
- `POST /api/dn/update` - 更新 DN (支持照片上传)
- `POST /api/dn/batch_update` - 批量创建
- `PUT /api/dn/update/{id}` - 编辑记录
- `DELETE /api/dn/update/{id}` - 删除记录
- `DELETE /api/dn/{dn_number}` - 软删除 DN
- `GET /api/dn/records` - 历史记录
- `GET /api/dn/list/batch` - 批量列表
- `GET /api/dn/stats/{date}` - 按日期统计
- `GET /api/dn/status-delivery/stats` - 状态统计 + LSP 汇总
- `GET /api/dn/status-delivery/lsp-summary-records` - LSP 历史数据
- `GET /api/dn/filters` - 筛选选项
- `POST /api/dn/sync` - 手动同步
- `GET /api/dn/sync/log/latest` - 最新日志
- `GET /api/dn/sync/log/file` - 日志文件
- `POST /api/dn/extend` - 扩展列
- `POST /api/dn/mark` - 归档标记

#### 车辆管理 (4 个接口)
- `POST /api/vehicle/signin` - 车辆签到
- `POST /api/vehicle/depart` - 车辆离场
- `GET /api/vehicle/vehicle` - 单个查询
- `GET /api/vehicle/vehicles` - 列表查询

### 重要特性

#### Status Delivery 规范化

所有 `status_delivery` 值会自动规范化为标准格式:

| 输入 | 输出 |
|------|------|
| `"on the way"` / `"ON THE WAY"` / `"On The Way"` | `"On the way"` |
| `"prepare vehicle"` / `"PREPARE VEHICLE"` | `"Prepare Vehicle"` |
| `"on site"` / `"ON SITE"` | `"On Site"` |
| `"pod"` / `"POD"` | `"POD"` |

**标准状态值**:
- `Prepare Vehicle`
- `On the way` ⚠️ (注意: 小写 "w")
- `On Site`
- `POD`
- `Waiting PIC Feedback`
- `RePlan MOS due to LSP Delay`
- `RePlan MOS Project`
- `Cancel MOS`
- `Close by RN`
- `No Status` (空值默认)

## 核心业务流程

### 1. 数据同步流程
```
Google Sheets → 数据抓取 → 格式化 → 数据库比对 → 更新/插入 → 日志记录
```

**关键步骤**:
1. 读取 Google Sheets 中的 "Plan MOS" 工作表
2. 数据标准化和清洗
3. 与数据库现有数据进行比对
4. 执行增量更新或插入操作
5. 记录同步日志和统计信息

### 2. 车辆管理流程
```
车辆到达 → 签到登记 → 状态跟踪 → 完成离场 → 记录更新
```

### 3. DN 状态更新流程
```
状态变更 → 历史记录创建 → 主表更新 → Sheet 同步 → 时间戳写入 → 通知反馈
```

**时间戳写入规则** (更新于 2025-10-02):
- **到达时间戳** (写入 S 列 `actual_arrive_time_ata`):
  - `ARRIVED AT SITE` - 到达 site
  - `ARRIVED AT XD/PM` - 到达 XD/PM
- **出发时间戳** (写入 R 列 `actual_depart_from_start_point_atd`):
  - `TRANSPORTING FROM WH` - 从 WH 出发
  - `TRANSPORTING FROM XD/PM` - 从 XD/PM 出发
- **其他状态**: 不写入时间戳 (如 `POD`, `ON THE WAY` 等)
- 时间格式: `M/D/YYYY H:MM:SS` (GMT+7)
- 状态匹配不区分大小写
- 详细说明: [API_REFERENCE.md](./docs/API_REFERENCE.md)

## 定时任务

系统使用 APScheduler 管理定时任务。

### 同步任务
- **任务ID**: `dn_sheet_sync`
- **频率**: 每 5 分钟 (300 秒)
- **触发器**: `IntervalTrigger`
- **功能**: 自动同步 Google Sheets 数据到数据库
- **入口**: `scheduled_dn_sheet_sync()`
- **首次执行**: 应用启动后 5 秒

**同步流程**:
1. 读取所有 "Plan MOS" 工作表
2. 数据清洗和规范化
3. 去重 (保留最后一条)
4. 与数据库比对
5. 增量更新 (created/updated)
6. 软删除不在表格中的 DN
7. 规范化 `plan_mos_date` 和 `status_delivery` 字段

### 统计任务
- **任务ID**: `status_delivery_lsp_summary`
- **频率**: 每小时整点
- **触发器**: `CronTrigger(minute=0)`
- **功能**: 捕获 LSP 汇总统计快照
- **入口**: `scheduled_status_delivery_lsp_summary_capture()`

**统计内容**:
- 每个 LSP 的 DN 总数
- 每个 LSP 有状态的 DN 数
- 按 `plan_mos_date` 分组
- 记录时间戳 (雅加达时区)

## 配置管理

### 环境变量
```bash
# 数据库配置
DATABASE_URL=postgresql://user:password@host:port/database

# Google Sheets API
GOOGLE_SERVICE_ACCOUNT_FILE=path/to/service-account.json
GOOGLE_SPREADSHEET_ID=your_spreadsheet_id

# 文件存储 (AWS S3)
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_S3_BUCKET=your_bucket_name
AWS_S3_REGION=your_region

# 应用配置
ALLOWED_ORIGINS=http://localhost:3000,https://your-domain.com
```

### 配置类
所有配置通过 `app/settings.py` 中的 `Settings` 类统一管理，支持环境变量和默认值。

## 部署说明

### Docker 部署
```bash
# 构建镜像
docker build -t jakarta-backend .

# 运行容器
docker run -d \
  --name jakarta-backend \
  -p 8000:8000 \
  -e DATABASE_URL=your_database_url \
  -e GOOGLE_SERVICE_ACCOUNT_FILE=your_service_account_file \
  jakarta-backend
```

### 依赖要求
详见 `requirements.txt`，主要依赖包括:
- `fastapi`: Web 框架
- `sqlalchemy`: ORM
- `psycopg2-binary`: PostgreSQL 驱动
- `gspread`: Google Sheets API 客户端
- `boto3`: AWS SDK
- `pandas`: 数据处理

## 开发指南

### 本地开发环境设置
1. 安装 Python 3.13+
2. 创建虚拟环境: `python -m venv .venv`
3. 激活虚拟环境: `source .venv/bin/activate` (macOS/Linux) 或 `.venv\Scripts\activate` (Windows)
4. 安装依赖: `pip install -r requirements.txt`
5. 配置环境变量 (复制 `.env.example` 为 `.env`)
6. 运行数据库迁移 (自动在启动时执行)
7. 运行应用: `uvicorn app.main:app --reload --port 10000`

### 测试
```bash
# 运行所有测试
pytest tests/ -v

# 运行特定测试文件
pytest tests/test_status_delivery_stats.py -v

# 运行特定测试
pytest tests/test_vehicle_crud.py::test_vehicle_signin_and_fetch -v

# 查看测试覆盖率
pytest --cov=app tests/

# 生成 HTML 覆盖率报告
pytest --cov=app --cov-report=html tests/
```

**测试文件**:
- `test_status_delivery_stats.py` - DN 状态统计测试 (6 个测试)
- `test_status_delivery_normalization.py` - 状态规范化测试 (3 个测试)
- `test_vehicle_crud.py` - 车辆 CRUD 测试 (2 个测试)
- `test_date_range.py` - 日期范围测试 (3 个测试)
- `test_plan_mos_archiving_regression.py` - 归档功能测试 (1 个测试)
- `test_timestamp_update.py` - 时间戳更新测试 (7 个测试) ⭐ 更新
- `test_show_deleted.py` - 软删除查询测试 (1 个测试) 🆕 新增

**总计**: 23 个测试，全部通过 ✅

### 代码规范
- 使用 Python 类型注解
- 遵循 PEP 8 代码风格
- 函数和类需要添加文档字符串
- 关键业务逻辑需要编写单元测试

## 故障排查

### 常见问题

#### 1. 数据库连接问题
- 检查 `DATABASE_URL` 配置
- 确认数据库服务状态
- 验证网络连接和防火墙设置

#### 2. Google Sheets 同步失败
- 验证 Service Account 凭证
- 检查 Spreadsheet ID 是否正确
- 确认 API 配额限制

#### 3. 文件上传问题
- 检查 S3 配置和权限
- 验证本地存储路径权限
- 查看文件大小限制

### 日志查看
- 应用日志: 通过 uvicorn 输出
- 同步日志: 通过 `/api/dn/sync/log/file` 下载
- 数据库日志: 查看 PostgreSQL 日志




---

## 最新更新

### 2025-10-02
- ✨ 新增全局常量管理模块 (`app/constants.py`)
- 🔧 优化时间戳回写规则，支持精确的到达/出发场景区分
- 🆕 添加软删除记录查询功能 (`show_deleted` 参数)
- 📝 完善 API 文档和测试覆盖
- 详细更新日志: [CHANGELOG.md](./docs/CHANGELOG.md)

---

*本文档最后更新时间: 2025-10-02*