# JakartaBackend 技术文档

## 项目概述

JakartaBackend 是一个基于 FastAPI 构建的物流管理系统后端服务，主要负责配送单据（Delivery Note，简称 DN）的管理和车辆调度。系统通过与 Google Sheets 集成，实现数据的自动化同步和处理。

### 主要功能
- **数据同步**: 与 Google Sheets 的双向数据同步
- **配送管理**: DN 状态跟踪和历史记录管理
- **车辆调度**: 车辆签到/离场管理
- **统计分析**: 实时数据统计和趋势分析
- **文件管理**: 照片等附件的上传和存储

## 技术架构

### 技术栈
- **Web 框架**: FastAPI (Python 3.11+)
- **数据库**: PostgreSQL + SQLAlchemy ORM
- **外部集成**: Google Sheets API (gspread)
- **任务调度**: AsyncIO 定时任务
- **文件存储**: AWS S3 / 本地文件系统
- **部署**: Docker 容器化
- **测试**: Pytest

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
    status_delivery: str # 配送状态
    plan_mos_date: date  # 计划日期
    lsp: str            # 物流服务商
    region: str         # 区域
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

### 数据关系
- Vehicle: 独立实体，按车牌号管理
- DN ↔ DNRecord: 一对多关系，DN 为主表，DNRecord 为历史记录
- 支持动态列扩展，新增字段会自动同步到数据库和 ORM

## API 接口文档

### 通用接口

#### 健康检查
```http
GET /
```
**响应**: `{ok: true, message: "You can use admin panel now."}`

### DN 管理接口

#### 1. DN 列表查询
```http
GET /api/dn/list/search
```
**查询参数**:
- `date[]`: 日期筛选
- `dn_number`: DN号码
- `status`: 状态
- `lsp`: 物流服务商
- `page`: 页码 (默认: 1)
- `page_size`: 每页数量 (默认: 20)

**响应示例**:
```json
{
  "ok": true,
  "total": 150,
  "page": 1,
  "page_size": 20,
  "items": [...]
}
```

#### 2. DN 状态更新
```http
POST /api/dn/update
Content-Type: multipart/form-data
```
**请求参数**:
- `dnNumber`: DN号码
- `status`: 新状态
- `remark`: 备注
- `photo`: 照片文件 (可选)
- `lng`, `lat`: 经纬度坐标

#### 3. 批量操作
```http
POST /api/dn/batch_update
Content-Type: application/json
```
```json
{
  "dn_numbers": ["DN001", "DN002", "DN003"]
}
```

#### 4. 数据同步
```http
POST /api/dn/sync
```
手动触发与 Google Sheets 的数据同步

#### 5. 统计接口
```http
GET /api/dn/status-delivery/stats?lsp=LSP_NAME&plan_mos_date=2024-01-01
```

### 车辆管理接口

#### 1. 车辆签到
```http
POST /api/vehicle/signin
Content-Type: application/json
```
```json
{
  "vehiclePlate": "B1234ABC",
  "lsp": "LSP_NAME",
  "vehicleType": "Truck",
  "driverName": "张三",
  "driverContact": "1234567890",
  "arriveTime": "2024-01-01T08:00:00Z"
}
```

#### 2. 车辆离场
```http
POST /api/vehicle/depart
Content-Type: application/json
```
```json
{
  "vehiclePlate": "B1234ABC",
  "departTime": "2024-01-01T18:00:00Z"
}
```

#### 3. 车辆查询
```http
GET /api/vehicle/vehicles?status=arrived&date=2024-01-01
```

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
状态变更 → 历史记录创建 → 主表更新 → Sheet 同步 → 通知反馈
```

## 定时任务

### 同步任务
- **频率**: 每 5 分钟
- **功能**: 自动同步 Google Sheets 数据到数据库
- **入口**: `scheduled_dn_sheet_sync()`

### 统计任务
- **频率**: 每小时整点
- **功能**: 生成 LSP 汇总统计数据
- **入口**: `scheduled_status_delivery_lsp_summary_capture()`

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
1. 安装 Python 3.11+
2. 创建虚拟环境: `python -m venv venv`
3. 激活虚拟环境: `source venv/bin/activate`
4. 安装依赖: `pip install -r requirements.txt`
5. 配置环境变量
6. 运行应用: `uvicorn app.main:app --reload`

### 测试
```bash
# 运行所有测试
pytest

# 运行特定测试文件
pytest tests/test_vehicle_crud.py

# 查看测试覆盖率
pytest --cov=app tests/
```

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

## 版本历史

当前版本实现了核心的 DN 管理、车辆调度和数据同步功能。后续版本计划：
- 性能优化和缓存机制
- 更丰富的统计分析功能
- 移动端API支持
- 实时通知系统

---

*本文档最后更新时间: 2024年9月30日*