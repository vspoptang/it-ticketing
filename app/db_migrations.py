1|"""Idempotent schema migration — auto-detects PostgreSQL vs SQLite."""
2|
3|from sqlalchemy import text
4|
5|from app.config import settings
6|
7|
8|def _is_postgresql() -> bool:
9|    return settings.DATABASE_URL.startswith("postgresql")
10|
11|
12|def ensure_db_schema(conn):
13|    """Idempotent schema migration — checks existence before each step."""
14|
15|    is_pg = _is_postgresql()
16|
17|    # ── Gather current state ──
18|    if is_pg:
19|        result = conn.execute(
20|            text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'")
21|        )
22|    else:
23|        result = conn.execute(
24|            text("SELECT name FROM sqlite_master WHERE type='table'")
25|        )
26|    existing_tables = {row[0] for row in result.fetchall()}
27|
28|    def _get_columns(table: str) -> set:
29|        if table not in existing_tables:
30|            return set()
31|        if is_pg:
32|            r = conn.execute(
33|                text(
34|                    "SELECT column_name FROM information_schema.columns "
35|                    "WHERE table_name = :t AND table_schema = 'public'"
36|                ),
37|                {"t": table},
38|            )
39|        else:
40|            r = conn.execute(text(f"PRAGMA table_info('{table}')"))
41|        return {row[0] for row in r.fetchall()}
42|
43|    def _index_exists(name: str) -> bool:
44|        if is_pg:
45|            r = conn.execute(text("SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": name})
46|        else:
47|            r = conn.execute(
48|                text("SELECT 1 FROM sqlite_master WHERE type='index' AND name = :n"), {"n": name}
49|            )
50|        return r.fetchone() is not None
51|
52|    # SQL helpers
53|    now_sql = "NOW()" if is_pg else "CURRENT_TIMESTAMP"
54|    bool_true = "TRUE" if is_pg else "1"
55|    serial_pk = "SERIAL PRIMARY KEY" if is_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
56|    ts_type = "TIMESTAMPTZ" if is_pg else "DATETIME"
57|    text_type = "TEXT" if is_pg else "TEXT"
58|
59|    # ── 1. Users table ──
60|    if "users" not in existing_tables:
61|        conn.execute(
62|            text(
63|                f"""
64|                CREATE TABLE users (
65|                    id {serial_pk},
66|                    username VARCHAR(50) NOT NULL UNIQUE,
67|                    email VARCHAR(255),
68|                    password_hash VARCHAR(255) NOT NULL,
69|                    display_name VARCHAR(100) NOT NULL,
70|                    role VARCHAR(20) NOT NULL DEFAULT 'end_user',
71|                    is_active {bool_type} DEFAULT {bool_true},
72|                    created_at {ts_type} NOT NULL DEFAULT {now_sql},
73|                    updated_at {ts_type}
74|                )
75|                """
76|            )
77|        )
78|        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_username ON users (username)"))
79|
80|    import bcrypt
81|    admin_exists = conn.execute(
82|        text("SELECT id, role FROM users WHERE username = 'admin'")
83|    ).fetchone()
84|    if admin_exists is None:
85|        pwd = bcrypt.hashpw(
86|            settings.ADMIN_DEFAULT_PASSWORD.encode("utf-8"), bcrypt.gensalt()
87|        ).decode("utf-8")
88|        conn.execute(
89|            text(
90|                f"INSERT INTO users (username, password_hash, display_name, role, is_active, created_at) "
91|                f"VALUES ('admin', :pwd, 'system admin', 'admin', {bool_true}, {now_sql})"
92|            ),
93|            {"pwd": pwd},
94|        )
95|    elif admin_exists[1] != "admin":
96|        conn.execute(text("UPDATE users SET role = 'admin' WHERE username = 'admin'"))
97|
98|    # ── 2. Categories table ──
99|    if "categories" not in existing_tables:
100|        conn.execute(
101|            text(
102|                f"""
103|                CREATE TABLE categories (
104|                    id {serial_pk},
105|                    name VARCHAR(50) NOT NULL UNIQUE,
106|                    description VARCHAR(200),
107|                    is_active {bool_type} DEFAULT {bool_true},
108|                    sort_order INTEGER DEFAULT 0,
109|                    created_at {ts_type} NOT NULL DEFAULT {now_sql}
110|                )
111|                """
112|            )
113|        )
114|        defaults = [
115|            ("Hardware", "Computer, printer, monitor issues", 1),
116|            ("Software", "OS, application issues", 2),
117|            ("Network", "Network, VPN, WiFi issues", 3),
118|            ("Account", "Account permissions, password reset", 4),
119|            ("Other", "Other uncategorized issues", 5),
120|        ]
121|        for name, desc, order in defaults:
122|            conn.execute(
123|                text(
124|                    f"INSERT INTO categories (name, description, sort_order, created_at) "
125|                    f"VALUES (:name, :desc, :order, {now_sql})"
126|                ),
127|                {"name": name, "desc": desc, "order": order},
128|            )
129|
130|    # ── 3. Tickets table ──
131|    if "tickets" not in existing_tables:
132|        if is_pg:
133|            conn.execute(
134|                text(
135|                    """
136|                    CREATE TABLE tickets (
137|                        id SERIAL PRIMARY KEY,
138|                        title VARCHAR(200) NOT NULL,
139|                        description TEXT,
140|                        status VARCHAR(20) NOT NULL DEFAULT 'pending',
141|                        priority VARCHAR(20) NOT NULL DEFAULT 'medium',
142|                        assignee VARCHAR(100),
143|                        category VARCHAR(50),
144|                        creator_name VARCHAR(100) NOT NULL,
145|                        resolution_notes TEXT,
146|                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
147|                        resolved_at TIMESTAMPTZ,
148|                        updated_at TIMESTAMPTZ,
149|                        ticket_number VARCHAR(15) UNIQUE,
150|                        sla_due_at TIMESTAMPTZ,
151|                        first_response_at TIMESTAMPTZ,
152|                        satisfaction VARCHAR(20),
153|                        search_vector TSVECTOR
154|                            GENERATED ALWAYS AS (
155|                                to_tsvector('simple',
156|                                    coalesce(title, '') || ' ' ||
157|                                    coalesce(description, '') || ' ' ||
158|                                    coalesce(creator_name, '') || ' ' ||
159|                                    coalesce(category, '')
160|                                )
161|                            ) STORED
162|                    )
163|                    """
164|                )
165|            )
166|        else:
167|            conn.execute(
168|                text(
169|                    """
170|                    CREATE TABLE tickets (
171|                        id INTEGER PRIMARY KEY AUTOINCREMENT,
172|                        title VARCHAR(200) NOT NULL,
173|                        description TEXT,
174|                        status VARCHAR(20) NOT NULL DEFAULT 'pending',
175|                        priority VARCHAR(20) NOT NULL DEFAULT 'medium',
176|                        assignee VARCHAR(100),
177|                        category VARCHAR(50),
178|                        creator_name VARCHAR(100) NOT NULL,
179|                        resolution_notes TEXT,
180|                        created_at DATETIME NOT NULL,
181|                        resolved_at DATETIME,
182|                        updated_at DATETIME,
183|                        ticket_number VARCHAR(15) UNIQUE,
184|                        sla_due_at DATETIME,
185|                        first_response_at DATETIME,
186|                        satisfaction VARCHAR(20)
187|                    )
188|                    """
189|                )
190|            )
191|
192|    # Ensure columns for existing tickets table
193|    tickets_cols = _get_columns("tickets")
194|    col_type = ts_type
195|    for col in [
196|        "ticket_number", "sla_due_at", "first_response_at",
197|        "resolution_notes", "resolved_at", "satisfaction",
198|    ]:
199|        if col not in tickets_cols:
200|            col_def = f"VARCHAR(15)" if col in ("ticket_number", "satisfaction") else col_type
201|            if is_pg:
202|                conn.execute(text(f"ALTER TABLE tickets ADD COLUMN {col} {col_def}"))
203|            else:
204|                conn.execute(text(f"ALTER TABLE tickets ADD COLUMN {col} {col_def}"))
205|
206|    # ── 4. Ticket number sequences ──
207|    if "ticket_number_sequences" not in existing_tables:
208|        conn.execute(
209|            text(
210|                """
211|                CREATE TABLE ticket_number_sequences (
212|                    year INTEGER PRIMARY KEY,
213|                    next_number INTEGER NOT NULL DEFAULT 1
214|                )
215|                """
216|            )
217|        )
218|
219|    # ── 5. Attachments table ──
220|    if "attachments" not in existing_tables:
221|        if is_pg:
222|            conn.execute(
223|                text(
224|                    f"""
225|                    CREATE TABLE attachments (
226|                        id {serial_pk},
227|                        ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
228|                        filename VARCHAR(255) NOT NULL,
229|                        original_filename VARCHAR(255) NOT NULL,
230|                        content_type VARCHAR(100),
231|                        file_size INTEGER NOT NULL,
232|                        uploaded_at {ts_type} NOT NULL DEFAULT {now_sql},
233|                        uploaded_by VARCHAR(100)
234|                    )
235|                    """
236|                )
237|            )
238|        else:
239|            conn.execute(
240|                text(
241|                    f"""
242|                    CREATE TABLE attachments (
243|                        id {serial_pk},
244|                        ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
245|                        filename VARCHAR(255) NOT NULL,
246|                        original_filename VARCHAR(255) NOT NULL,
247|                        content_type VARCHAR(100),
248|                        file_size INTEGER NOT NULL,
249|                        uploaded_at {ts_type} NOT NULL DEFAULT {now_sql},
250|                        uploaded_by VARCHAR(100)
251|                    )
252|                    """
253|                )
254|            )
255|        conn.execute(
256|            text("CREATE INDEX IF NOT EXISTS ix_attachments_ticket_id ON attachments (ticket_id)")
257|        )
258|
259|    # ── 6. Notification events ──
260|    if "notification_events" not in existing_tables:
261|        ref = "REFERENCES tickets(id) ON DELETE SET NULL" if is_pg else "REFERENCES tickets(id) ON DELETE SET NULL"
262|        conn.execute(
263|            text(
264|                f"""
265|                CREATE TABLE notification_events (
266|                    id {serial_pk},
267|                    ticket_id INTEGER {ref},
268|                    event_type VARCHAR(20) NOT NULL,
269|                    recipient VARCHAR(255) NOT NULL,
270|                    subject VARCHAR(255),
271|                    message TEXT,
272|                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
273|                    error_message TEXT,
274|                    created_at {ts_type} NOT NULL DEFAULT {now_sql},
275|                    sent_at {ts_type}
276|                )
277|                """
278|            )
279|        )
280|
281|    # ── 7. Ticket events ──
282|    if "ticket_events" not in existing_tables:
283|        conn.execute(
284|            text(
285|                f"""
286|                CREATE TABLE ticket_events (
287|                    id {serial_pk},
288|                    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
289|                    event_type VARCHAR(50) NOT NULL,
290|                    message TEXT,
291|                    actor VARCHAR(100),
292|                    created_at {ts_type} NOT NULL DEFAULT {now_sql}
293|                )
294|                """
295|            )
296|        )
297|        conn.execute(
298|            text("CREATE INDEX IF NOT EXISTS ix_ticket_events_ticket_id ON ticket_events (ticket_id)")
299|        )
300|
301|    # ── 8. Category complexity weight ──
302|    cats_cols = _get_columns("categories")
303|    if "complexity_weight" not in cats_cols:
304|        conn.execute(
305|            text("ALTER TABLE categories ADD COLUMN complexity_weight REAL DEFAULT 1.0")
306|        )
307|
308|    # ── 9. Transition permissions ──
309|    if "transition_permissions" not in existing_tables:
310|        conn.execute(
311|            text(
312|                """
313|                CREATE TABLE transition_permissions (
314|                    from_status VARCHAR(20) NOT NULL,
315|                    to_status VARCHAR(20) NOT NULL,
316|                    permission VARCHAR(20) NOT NULL DEFAULT 'admin_only',
317|                    PRIMARY KEY (from_status, to_status)
318|                )
319|                """
320|            )
321|        )
322|        defaults = [
323|            ("pending", "in_progress", "any_staff"),
324|            ("pending", "cancelled", "admin_only"),
325|            ("in_progress", "completed", "owner"),
326|            ("in_progress", "cancelled", "admin_only"),
327|            ("in_progress", "escalated", "owner"),
328|            ("escalated", "in_progress", "owner"),
329|            ("escalated", "completed", "owner"),
330|            ("escalated", "cancelled", "admin_only"),
331|            ("completed", "in_progress", "any_staff"),
332|            ("cancelled", "pending", "admin_only"),
333|        ]
334|        for fs, ts, perm in defaults:
335|            conn.execute(
336|                text(
337|                    "INSERT INTO transition_permissions (from_status, to_status, permission) "
338|                    "VALUES (:fs, :ts, :p)"
339|                ),
340|                {"fs": fs, "ts": ts, "p": perm},
341|            )
342|
343|    # ── 10. Performance indexes ──
344|    index_specs = [
345|        ("ix_tickets_status", "tickets", "status"),
346|        ("ix_tickets_priority", "tickets", "priority"),
347|        ("ix_tickets_category", "tickets", "category"),
348|        ("ix_tickets_created_at", "tickets", "created_at"),
349|        ("ix_tickets_resolved_at", "tickets", "resolved_at"),
350|        ("ix_tickets_assignee_status", "tickets", "assignee, status"),
351|        ("ix_tickets_assignee_resolved", "tickets", "assignee, resolved_at"),
352|    ]
353|    for idx_name, table, cols in index_specs:
354|        conn.execute(
355|            text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({cols})")
356|        )
357|
358|    # GIN index (PostgreSQL only)
359|    if is_pg and not _index_exists("idx_tickets_search"):
360|        conn.execute(
361|            text("CREATE INDEX IF NOT EXISTS idx_tickets_search ON tickets USING GIN(search_vector)")
362|        )
363|
364|    # ── 11. Password reset tokens ──
365|    if "password_reset_tokens" not in existing_tables:
366|        conn.execute(
367|            text(
368|                f"""
369|                CREATE TABLE password_reset_tokens (
370|                    id {serial_pk},
371|                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
372|                    token VARCHAR(64) NOT NULL UNIQUE,
373|                    expires_at {ts_type} NOT NULL,
374|                    used {bool_type} DEFAULT FALSE,
375|                    created_at {ts_type} NOT NULL DEFAULT {now_sql}
376|                )
377|                """
378|            )
379|        )
380|        conn.execute(
381|            text("CREATE INDEX IF NOT EXISTS ix_reset_tokens_token ON password_reset_tokens (token)")
382|        )
383|
384|    if is_pg:
385|        conn.execute(text("COMMIT"))
386|