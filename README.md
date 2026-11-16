# Mars API

分层 FastAPI Web 工程，使用 SQLAlchemy 2.x 和 psycopg 3 同步访问 PostgreSQL，并使用 Alembic 管理表结构迁移。数据库访问配置位于 `app/db/`，业务逻辑和数据库操作由 `app/services/` 负责；Redis 目前仅预留目录，未添加依赖或访问实现。

## 目录结构

```text
mars-api/
├── app/
│   ├── main.py                 # 应用入口、应用工厂与资源释放
│   ├── core/
│   │   └── config.py           # .env / 环境变量配置
│   ├── api/                    # API 接口定义
│   │   ├── router.py           # 路由汇总
│   │   └── endpoints/
│   │       ├── health.py       # 健康检查接口
│   │       └── users.py        # t_user 全量查询接口
│   ├── schemas/                # Pydantic 请求与响应模型
│   │   ├── health.py
│   │   └── user.py
│   ├── models/                 # SQLAlchemy 数据库表模型
│   │   ├── base.py             # DeclarativeBase 基类
│   │   └── t_user.py           # 现有 t_user 表模型
│   ├── services/               # 业务实现
│   │   ├── health_service.py
│   │   └── user_service.py
│   └── db/                     # 数据库与中间件访问
│       ├── database.py         # PostgreSQL Engine、连接池、Session
│       └── redis/              # Redis（预留）
├── migrations/                # Alembic 迁移环境
│   ├── env.py
│   ├── script.py.mako
│   └── versions/              # 迁移版本（暂无业务表迁移）
├── tests/                     # 会话事务、迁移与启动验证
├── alembic.ini
├── .env.example
├── pyproject.toml             # 项目元数据与依赖
├── uv.lock                    # 锁定依赖，提交到 Git
└── README.md
```

各 Python 包包含 `__init__.py`，上方结构中省略展示。项目不设 Repository 层；Service 直接使用注入的 SQLAlchemy Session 访问数据库。

API 层处理 HTTP 请求、参数校验和响应转换，通过依赖注入调用 Service。Service 实现业务逻辑、直接访问数据库并管理事务，连接池与会话配置集中在 `db/database.py`。

## 安装与启动

要求 Python 3.10 或更高版本。在项目根目录执行：

```bash
uv sync
# 仅在首次初始化时创建 .env；已有配置不会被覆盖
test -f .env || cp .env.example .env
uv run uvicorn app.main:app --reload
```

启动后可访问：

- Swagger 文档：http://127.0.0.1:8000/docs
- ReDoc 文档：http://127.0.0.1:8000/redoc
- 健康检查：http://127.0.0.1:8000/api/health
- 用户列表：http://127.0.0.1:8000/api/users

```bash
curl http://127.0.0.1:8000/api/health
# {"status":"ok"}
```

健康检查仅表示应用存活，不检查数据库或 Redis。

`GET /api/users` 从 `t_user` 表按 `id` 升序返回全部行和全部字段，包括 `is_del=true` 的记录；该接口没有分页。请求时需要数据库连接可用。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 应用存活检查，不访问数据库 |
| `GET` | `/api/users` | 查询 `t_user` 全部记录，按 `id` 升序排列 |

## 数据库配置

在 `.env` 中配置现有 PostgreSQL 数据库连接及连接池参数：

```dotenv
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_USER=mars
POSTGRES_PASSWORD=your_password
POSTGRES_DATABASE=mars
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=1800
DB_CONNECT_TIMEOUT=10
DB_ECHO=false
```

配置文件固定从项目根目录读取，系统环境变量优先于 `.env`。`.env` 不提交到 Git。密码按原始值填写，应用通过 SQLAlchemy `URL.create` 构造连接 URL，无需手动 URL 编码；在 `.env` 中含 `#` 等特殊字符的密码应按 dotenv 格式加引号。

`POSTGRES_PORT`、连接池大小、超时和回收周期有默认值，具体可配置项及默认值见 `.env.example` 和 `app/core/config.py`。`DB_ECHO` 默认关闭，SQL 参数不会写入日志。

PostgreSQL 数据库和账号需要预先创建；Alembic 负责管理库内的表结构，不创建数据库或账号。应用启动只初始化 Engine，不主动连接数据库、不自动建表，未运行 PostgreSQL 时仍可访问健康检查和接口文档。关闭应用时释放连接池。

## 使用数据库会话

在接口中通过 `Depends(get_db)` 注入会话，再传给 Service：

```python
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.database import get_db

DbSession = Annotated[Session, Depends(get_db)]
```

每个请求使用独立的 Session，请求结束后关闭会话，未提交的事务会回滚。`get_db` 不会自动提交。Service 中的写操作应使用 `with session.begin():` 管理提交和异常回滚。

当前使用同步数据库驱动，访问数据库的接口应使用普通 `def`，避免在 `async def` 中直接执行阻塞操作。数据库表模型继承 `app.models.Base`；接口请求和响应模型定义在 `app/schemas/`。

## 数据库迁移

`app/models/t_user.py` 定义现有 PostgreSQL `public.t_user` 表的模型，并在 `app/models/__init__.py` 中导入，供 Alembic 发现。Alembic 仅将代码中由 `Base.metadata` 管理的表纳入自动比对，不会为数据库中其他未管理的表生成删除迁移。新增或修改模型后，确保在 `app/models/__init__.py` 中导入，再连接 PostgreSQL 执行：

```bash
# 根据模型与数据库的差异生成迁移脚本，需要连接数据库
uv run alembic revision --autogenerate -m "describe schema change"

# 检查 migrations/versions/ 下生成的脚本后执行
uv run alembic upgrade head

# 查看当前数据库版本，需要连接数据库
uv run alembic current

# 回退一个迁移版本，执行前确认对应 downgrade 内容
uv run alembic downgrade -1
```

不连接数据库也可以查看迁移历史，或输出已有迁移的 SQL：

```bash
uv run alembic history
uv run alembic upgrade head --sql
```

工程已配置迁移环境，无需再次执行 `alembic init`。当前没有针对既有 `t_user` 表的基线迁移；接管已有数据库时，应先规划基线版本，并检查自动生成的迁移脚本，避免对现有表重复执行建表迁移。

## 验证

```bash
uv run pytest
```

默认测试使用临时 SQLite，不访问 PostgreSQL。录音接口另有真实 PostgreSQL 集成测试，执行前会写入测试数据并保留数据和本地媒体文件。需要连接 `.env` 数据库时，明确设置开关运行：

```powershell
$env:RUN_RECORDING_MEDIA_INTEGRATION = "1"
uv run pytest -s tests/test_recording_media.py
```

该集成测试强制使用本地 Mock 存储，不调用真实 OSS。测试记录和 `.local-media/` 文件不会自动清理。

## 添加业务模块

1. 在 `schemas/` 下定义请求和响应模型。
2. 如需持久化，在 `models/` 定义表模型并在 `models/__init__.py` 导入。
3. 在 `services/` 实现业务逻辑、数据库读写及事务。
4. 在 `api/endpoints/` 定义接口，通过 `Depends` 注入会话及 Service。
5. 在 `api/router.py` 注册模块路由，并按需生成、执行表结构迁移。
