"""Start script for IT Ticketing dev server."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./tickets.db")
os.environ.setdefault("JWT_SECRET_KEY", "dev-" * 10 + "!!")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123")

import uvicorn
uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
