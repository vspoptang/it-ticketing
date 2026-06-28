"""Idempotent schema migration for PostgreSQL + SQLite."""

from sqlalchemy import text
from app.config import settings


def _is_pg():
    return settings.DATABASE_URL.startswith("postgresql")


def ensure_db_schema(conn):
    is_pg = _is_pg()

    # Gather current state
    if is_pg:
        result = conn.execute(
            text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'")
        )
    else:
        result = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )
    existing_tables = {row[0] for row in result.fetchall()}

    def _get_columns(table):
        if is_pg:
            r = conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :t AND table_schema = 'public'"
            ), {"t": table})
            return {row[0] for row in r.fetchall()}
        else:
            r = conn.execute(text(f"PRAGMA table_info('{table}')"))
        return {row[1] for row in r.fetchall()}

    def _index_exists(name):
        if is_pg:
            r = conn.execute(text("SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": name})
        else:
            r = conn.execute(text(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name = :n"
            ), {"n": name})
        return r.fetchone() is not None

    now_sql = "NOW()" if is_pg else "CURRENT_TIMESTAMP"
    bool_type = "BOOLEAN" if is_pg else "INTEGER"
    bool_true = "TRUE" if is_pg else "1"
    serial_pk = "SERIAL PRIMARY KEY" if is_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ts_type = "TIMESTAMPTZ" if is_pg else "DATETIME"

    def _cols_exist(table, *cols):
        """Check if all columns exist in table. Returns True only if all exist."""
        existing = _get_columns(table)
        return all(c in existing for c in cols)

    # Users
    if "users" not in existing_tables:
        conn.execute(text(f"""
            CREATE TABLE users (
                id {serial_pk},
                username VARCHAR(50) NOT NULL UNIQUE,
                email VARCHAR(255),
                password_hash VARCHAR(255) NOT NULL,
                display_name VARCHAR(100) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'end_user',
                is_active {bool_type} DEFAULT {bool_true},
                created_at {ts_type} NOT NULL DEFAULT {now_sql},
                updated_at {ts_type}
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_username ON users (username)"))

    import bcrypt
    admin = conn.execute(text("SELECT id, role FROM users WHERE username = 'admin'")).fetchone()
    if admin is None:
        pwd = bcrypt.hashpw(settings.ADMIN_DEFAULT_PASSWORD.encode(), bcrypt.gensalt()).decode()
        conn.execute(text(
            f"INSERT INTO users (username, password_hash, display_name, role, is_active, created_at) "
            f"VALUES ('admin', :pwd, 'Admin', 'admin', {bool_true}, {now_sql})"
        ), {"pwd": pwd})
    elif admin[1] != "admin":
        conn.execute(text("UPDATE users SET role = 'admin' WHERE username = 'admin'"))

    # Categories
    if "categories" not in existing_tables:
        conn.execute(text(f"""
            CREATE TABLE categories (
                id {serial_pk},
                name VARCHAR(50) NOT NULL UNIQUE,
                description VARCHAR(200),
                is_active {bool_type} DEFAULT {bool_true},
                sort_order INTEGER DEFAULT 0,
                complexity_weight REAL DEFAULT 1.0,
                created_at {ts_type} NOT NULL DEFAULT {now_sql}
            )
        """))
        for n, d, o in [
            ("Hardware", "Hardware issues", 1), ("Software", "Software issues", 2),
            ("Network", "Network issues", 3), ("Account", "Account issues", 4),
            ("Other", "Other issues", 5),
        ]:
            conn.execute(text(
                f"INSERT INTO categories (name, description, sort_order, created_at) "
                f"VALUES (:n, :d, :o, {now_sql})"
            ), {"n": n, "d": d, "o": o})

    # Tickets
    if "tickets" not in existing_tables:
        if is_pg:
            conn.execute(text("""
                CREATE TABLE tickets (
                    id SERIAL PRIMARY KEY, title VARCHAR(200) NOT NULL,
                    description TEXT, status VARCHAR(20) DEFAULT 'pending',
                    priority VARCHAR(20) DEFAULT 'medium', assignee VARCHAR(100),
                    category VARCHAR(50), creator_name VARCHAR(100) NOT NULL,
                    resolution_notes TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    resolved_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
                    ticket_number VARCHAR(15) UNIQUE,
                    sla_due_at TIMESTAMPTZ, first_response_at TIMESTAMPTZ,
                    satisfaction VARCHAR(20),
                    search_vector TSVECTOR GENERATED ALWAYS AS (
                        to_tsvector('simple',
                            coalesce(title,'') || ' ' ||
                            coalesce(description,'') || ' ' ||
                            coalesce(creator_name,'') || ' ' ||
                            coalesce(category,'')
                        )
                    ) STORED
                )
            """))
        else:
            conn.execute(text("""
                CREATE TABLE tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title VARCHAR(200) NOT NULL,
                    description TEXT, status VARCHAR(20) DEFAULT 'pending',
                    priority VARCHAR(20) DEFAULT 'medium', assignee VARCHAR(100),
                    category VARCHAR(50), creator_name VARCHAR(100) NOT NULL,
                    resolution_notes TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    resolved_at DATETIME, updated_at DATETIME,
                    ticket_number VARCHAR(15) UNIQUE,
                    sla_due_at DATETIME, first_response_at DATETIME,
                    satisfaction VARCHAR(20),
                    search_vector TEXT
                )
            """))

    # Add any missing columns to existing tickets table
    if "tickets" in existing_tables:
        ticket_col_defs = {
            "ticket_number": "VARCHAR(15)",
            "sla_due_at": ts_type,
            "first_response_at": ts_type,
            "resolution_notes": "TEXT",
            "resolved_at": ts_type,
            "satisfaction": "VARCHAR(20)",
            "search_vector": "TEXT",
        }
        existing_cols = _get_columns("tickets")
        for col, dt in ticket_col_defs.items():
            if col not in existing_cols:
                conn.execute(text(f"ALTER TABLE tickets ADD COLUMN {col} {dt}"))

    # Sequences
    if "ticket_number_sequences" not in existing_tables:
        conn.execute(text(
            "CREATE TABLE ticket_number_sequences (year INTEGER PRIMARY KEY, next_number INTEGER DEFAULT 1)"
        ))

    # Attachments
    if "attachments" not in existing_tables:
        conn.execute(text(f"""
            CREATE TABLE attachments (
                id {serial_pk},
                ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                filename VARCHAR(255) NOT NULL,
                original_filename VARCHAR(255) NOT NULL,
                content_type VARCHAR(100), file_size INTEGER NOT NULL,
                uploaded_at {ts_type} NOT NULL DEFAULT {now_sql},
                uploaded_by VARCHAR(100)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_attachments_ticket_id ON attachments (ticket_id)"))

    # Notification events
    if "notification_events" not in existing_tables:
        conn.execute(text(f"""
            CREATE TABLE notification_events (
                id {serial_pk},
                ticket_id INTEGER REFERENCES tickets(id) ON DELETE SET NULL,
                event_type VARCHAR(20) NOT NULL, recipient VARCHAR(255) NOT NULL,
                subject VARCHAR(255), message TEXT,
                status VARCHAR(20) DEFAULT 'pending', error_message TEXT,
                created_at {ts_type} NOT NULL DEFAULT {now_sql}, sent_at {ts_type}
            )
        """))

    # Ticket events
    if "ticket_events" not in existing_tables:
        conn.execute(text(f"""
            CREATE TABLE ticket_events (
                id {serial_pk},
                ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                event_type VARCHAR(50) NOT NULL, message TEXT,
                actor VARCHAR(100),
                created_at {ts_type} NOT NULL DEFAULT {now_sql}
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ticket_events_ticket_id ON ticket_events (ticket_id)"))

    # Category complexity weight (migration)
    if "categories" in existing_tables:
        cats_cols = _get_columns("categories")
        if "complexity_weight" not in cats_cols:
            conn.execute(text("ALTER TABLE categories ADD COLUMN complexity_weight REAL DEFAULT 1.0"))

    # Transition permissions
    if "transition_permissions" not in existing_tables:
        conn.execute(text("""
            CREATE TABLE transition_permissions (
                from_status VARCHAR(20) NOT NULL, to_status VARCHAR(20) NOT NULL,
                permission VARCHAR(20) DEFAULT 'admin_only',
                PRIMARY KEY (from_status, to_status)
            )
        """))
        perms = [
            ("pending", "in_progress", "any_staff"), ("pending", "cancelled", "admin_only"),
            ("in_progress", "completed", "owner"), ("in_progress", "cancelled", "admin_only"),
            ("in_progress", "escalated", "owner"), ("escalated", "in_progress", "owner"),
            ("escalated", "completed", "owner"), ("escalated", "cancelled", "admin_only"),
            ("completed", "in_progress", "any_staff"), ("cancelled", "pending", "admin_only"),
        ]
        for fs, ts, p in perms:
            conn.execute(text(
                "INSERT INTO transition_permissions (from_status, to_status, permission) VALUES (:fs, :ts, :p)"
            ), {"fs": fs, "ts": ts, "p": p})

    # Indexes
    for idx, tbl, cols in [
        ("ix_tickets_status", "tickets", "status"),
        ("ix_tickets_priority", "tickets", "priority"),
        ("ix_tickets_category", "tickets", "category"),
        ("ix_tickets_created_at", "tickets", "created_at"),
        ("ix_tickets_resolved_at", "tickets", "resolved_at"),
        ("ix_tickets_assignee_status", "tickets", "assignee, status"),
        ("ix_tickets_assignee_resolved", "tickets", "assignee, resolved_at"),
    ]:
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {idx} ON {tbl} ({cols})"))

    # Skip GIN index on search_vector — using ILIKE search instead
    # if is_pg and not _index_exists("idx_tickets_search"):
    #     conn.execute(text("CREATE INDEX IF NOT EXISTS idx_tickets_search ON tickets USING GIN(search_vector)"))

    # Password reset tokens
    if "password_reset_tokens" not in existing_tables:
        conn.execute(text(f"""
            CREATE TABLE password_reset_tokens (
                id {serial_pk},
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token VARCHAR(64) NOT NULL UNIQUE,
                expires_at {ts_type} NOT NULL,
                used {bool_type} DEFAULT FALSE,
                created_at {ts_type} NOT NULL DEFAULT {now_sql}
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_reset_tokens_token ON password_reset_tokens (token)"))

    # Priority SLA config
    if "priority_config" not in existing_tables:
        conn.execute(text("""
            CREATE TABLE priority_config (
                priority VARCHAR(20) PRIMARY KEY,
                label VARCHAR(50) NOT NULL,
                hours REAL NOT NULL DEFAULT 4.0,
                sort_order INTEGER DEFAULT 0
            )
        """))
        # Seed defaults
        for p, label, hours, order in [
            ("urgent", "紧急", 4.0, 0),
            ("high", "高", 8.0, 1),
            ("medium", "中", 24.0, 2),
            ("low", "低", 48.0, 3),
        ]:
            conn.execute(text(
                "INSERT INTO priority_config (priority, label, hours, sort_order) "
                "VALUES (:p, :label, :hours, :order)"
            ), {"p": p, "label": label, "hours": hours, "order": order})

    # Workday & work-hours config
    if "workday_config" not in existing_tables:
        conn.execute(text("""
            CREATE TABLE workday_config (
                day_of_week INTEGER PRIMARY KEY,
                label VARCHAR(10) NOT NULL,
                is_workday BOOLEAN DEFAULT TRUE,
                work_start VARCHAR(5) DEFAULT '08:00',
                work_end VARCHAR(5) DEFAULT '17:00'
            )
        """))
        days = [
            (0, "周一", True), (1, "周二", True), (2, "周三", True),
            (3, "周四", True), (4, "周五", True),
            (5, "周六", False), (6, "周日", False),
        ]
        for d, label, wd in days:
            conn.execute(text(
                "INSERT INTO workday_config (day_of_week, label, is_workday) "
                "VALUES (:d, :label, :wd)"
            ), {"d": d, "label": label, "wd": wd})
