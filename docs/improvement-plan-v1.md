# IT工单系统 - 改进方案 v1

> 2025-06-25 | 基于代码审查结果

---

## 改进项概览

| 序号 | 改进项 | 优先级 | 预估工时 |
|------|--------|--------|----------|
| 1 | SQLite → PostgreSQL 迁移 | 🔴 高 | 4h |
| 2 | 补充核心测试 | 🔴 高 | 3h |
| 3 | 权限缓存改为请求级 | 🔴 高 | 1h |
| 4 | 密码修改/重置功能 | 🟡 中 | 2h |

---

## 一、PostgreSQL 迁移

### 1.1 变更范围

| 文件 | 改动内容 |
|------|----------|
| `requirements.txt` | `aiosqlite` → `asyncpg`，新增 `psycopg2-binary`（Alembic 用） |
| `app/config.py` | 默认 `DATABASE_URL` 改为 PostgreSQL 连接串 |
| `app/database.py` | 移除 `check_same_thread`，显式配置 pool_size=10, max_overflow=20 |
| `app/db_migrations.py` | SQLite 专有语法 → PostgreSQL 语法（见下方对照表） |
| `app/services/ticket_service.py` | FTS5 MATCH → PostgreSQL `tsvector` / ILIKE |
| `docker-compose.yml` | 新增 `postgres` 服务 + 数据卷 |
| `Dockerfile` | 无需改动（`asyncpg` 通过 pip 装） |
| `.env.example` | 更新注释和默认连接串 |

### 1.2 关键语法迁移

| SQLite | PostgreSQL |
|--------|------------|
| `INSERT OR IGNORE` | `INSERT ... ON CONFLICT DO NOTHING` |
| `datetime('now')` | `NOW()` |
| `PRAGMA table_info('t')` | `SELECT column_name FROM information_schema.columns WHERE table_name='t'` |
| `FTS5 VIRTUAL TABLE` | `GENERATED ALWAYS AS ... STORED` 列 + `GIN` 索引 |
| `AUTOINCREMENT` | `SERIAL` / `GENERATED ALWAYS AS IDENTITY` |
| `BOOLEAN DEFAULT 1` | `BOOLEAN DEFAULT TRUE` |

### 1.3 搜索方案

淘汰 FTS5，改用 PostgreSQL 原生方案：

```sql
-- 在 tickets 表加一个 tsvector 列
ALTER TABLE tickets ADD COLUMN search_vector tsvector
  GENERATED ALWAYS AS (
    to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(description, '') || ' ' || coalesce(creator_name, '') || ' ' || coalesce(category, ''))
  ) STORED;

CREATE INDEX idx_tickets_search ON tickets USING GIN(search_vector);
```

Python 侧搜索：
```python
from sqlalchemy import func
query = query.where(
    func.to_tsvector('simple', Ticket.title + ' ' + Ticket.description)
    .match(search_term, postgresql_regconfig='simple')
)
```

### 1.4 Docker Compose 变更

```yaml
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: it_ticketing
      POSTGRES_USER: ticketing
      POSTGRES_PASSWORD: ${DB_PASSWORD:-change-me}
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ticketing -d it_ticketing"]
      interval: 5s
      timeout: 3s
      retries: 5

  app:
    build: .
    restart: unless-stopped
    ports:
      - "${APP_PORT:-8000}:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://ticketing:${DB_PASSWORD:-change-me}@postgres:5432/it_ticketing
      ...
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - app_data:/app/data

volumes:
  pg_data:
  app_data:
```

### 1.5 迁移策略

提供 `db_migrations.py` 自动建表（连接时检测并初始化），无需手动跑迁移。已有数据可通过 `pgloader` 或手动导出 SQLite → CSV → PostgreSQL。

---

## 二、补充核心测试

### 2.1 测试框架

```
pytest + pytest-asyncio + httpx (AsyncClient)
```

测试数据库：测试环境仍用 **SQLite 内存库**（CI 友好，不需要额外服务）。

### 2.2 测试文件结构

```
tests/
├── __init__.py
├── conftest.py              # fixtures: async client, test db, test user
├── test_auth_service.py     # hash_password, verify_password, token, authenticate
├── test_ticket_service.py   # create, update, transitions, SLA, list, search
└── test_dependencies.py     # get_current_user, require_user, require_admin
```

### 2.3 测试用例清单

**test_auth_service.py** (8 用例)
1. `hash_password` 生成 bcrypt hash
2. `verify_password` 正确/错误密码
3. `create_access_token` 包含正确 payload
4. `decode_access_token` 正确 token 解码成功
5. `decode_access_token` 过期 token 抛 401
6. `decode_access_token` 无效 token 抛 401
7. `authenticate_user` 正确凭据返回用户
8. `authenticate_user` 错误凭据返回 None

**test_ticket_service.py** (12 用例)
1. `create_ticket` 成功创建并生成工单号
2. `create_ticket` SLA 到期时间正确计算
3. `validate_transition` 合法转换不报错
4. `validate_transition` 非法转换抛 400
5. `update_ticket_status` pending→in_progress
6. `update_ticket_status` in_progress→completed 记录 resolved_at
7. `get_ticket` 存在/不存在
8. `list_tickets` 分页
9. `list_tickets` 关键词搜索
10. `list_tickets` SLA 筛选 overdue
11. `add_ticket_comment` 追加事件
12. `compute_sla_due_at` 跨周末场景

---

## 三、权限缓存改为请求级

### 3.1 当前问题

```python
# ticket_service.py:66-76 — 模块级全局变量，多 worker 不共享
_TRANSITION_PERMISSIONS: dict | None = None

def _get_transition_permissions():
    global _TRANSITION_PERMISSIONS
    if _TRANSITION_PERMISSIONS is not None:
        return _TRANSITION_PERMISSIONS
    _TRANSITION_PERMISSIONS = dict(_DEFAULT_PERMISSIONS)
    return _TRANSITION_PERMISSIONS
```

### 3.2 改进方案

**改为每次从 DB 加载**（权限表很小，查询成本可忽略），通过 FastAPI 的 `Depends` 注入：

```python
# 新建 app/services/permission_service.py
async def get_transition_permissions(db: AsyncSession) -> dict:
    """每次请求从 DB 加载权限映射（表很小，无性能问题）"""
    from app.models.transition_permission import TransitionPermission
    result = await db.execute(select(TransitionPermission))
    rows = result.scalars().all()
    if rows:
        return {(r.from_status, r.to_status): r.permission for r in rows}
    return dict(_DEFAULT_PERMISSIONS)
```

路由层注入：
```python
@router.post("/{ticket_id}/status")
async def change_status(
    perms: dict = Depends(get_transition_permissions),
    ...
):
    # 使用 perms 而非全局变量
```

**删除**：全局变量 `_TRANSITION_PERMISSIONS`、`_ADMIN_ONLY_TRANSITIONS`、`reload_permissions_from_db()`。

### 3.3 备选优化（如果未来权限查询变慢）

加 `functools.lru_cache(maxsize=1)` + 修改权限时调 `cache_clear()`：

```python
from functools import lru_cache

@lru_cache(maxsize=1)
def _cached_perms_key() -> str:
    return "v1"  # bump on schema change

# 管理员修改权限后调用：
_cached_perms_key.cache_clear()
```

但当前阶段不需要 — 权限表 < 20 行，直接查 DB 即可。

---

## 四、密码修改/重置功能

### 4.1 三个功能点

| 功能 | 路由 | 权限 | 说明 |
|------|------|------|------|
| 修改密码 | `POST /auth/change-password` | 已登录 | 输入旧密码 + 新密码 |
| 管理员重置 | `POST /admin/users/{id}/reset-password` | admin | 管理员直接设新密码 |
| 忘记密码 | `POST /auth/forgot-password` | 无需登录 | 发重置链接到邮箱（需 SMTP） |
| 重置密码 | `POST /auth/reset-password` | token 验证 | 通过邮箱链接中的 token 设新密码 |

### 4.2 数据模型

```python
# models/user.py 新增
class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[int] = ...
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = ...
    used: Mapped[bool] = default=False
```

### 4.3 业务流程

**修改密码**：
1. 表单：旧密码 + 新密码 + 确认新密码
2. 验证旧密码正确
3. 新密码 ≥ 8 字符
4. bcrypt 哈希后更新 `password_hash`

**管理员重置**：
1. 管理员在用户列表点「重置密码」
2. 输入新密码（或生成随机密码展示给管理员）
3. 强制用户下次登录修改？可后续加

**忘记密码**：
1. 用户输入用户名/邮箱
2. 系统生成 64 位随机 token，有效期 1 小时
3. 发邮件（含重置链接 `http://host/auth/reset-password?token=xxx`）
4. 用户点链接，设新密码
5. SMTP 未配置时，提示「请联系管理员重置」

### 4.4 文件变更

| 文件 | 改动 |
|------|------|
| `app/models/user.py` | 新增 `PasswordResetToken` 模型 |
| `app/services/auth_service.py` | 新增 `change_password`, `create_reset_token`, `reset_password` |
| `app/routers/auth.py` | 新增 3 个路由 |
| `app/db_migrations.py` | 新增 `password_reset_tokens` 表 |
| `app/templates/` | 新增 `change_password.html`, `forgot_password.html`, `reset_password.html` |

---

## 实施顺序

```
第 1 步: PostgreSQL 迁移      (影响面最大，先做)
第 2 步: 权限缓存修复          (文件改动小，快速跟进)
第 3 步: 补充核心测试          (验证前两步不引入 bug)
第 4 步: 密码修改/重置         (纯新增功能，最后加)
```

---

> 确认后开始实施。第一步 PostgreSQL 迁移会改动约 8 个文件。
