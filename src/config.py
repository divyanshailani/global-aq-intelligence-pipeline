"""
Global AQ Intelligence — Shared Configuration
===============================================
Central place for DB credentials & paths.
All scripts import from here instead of hardcoding.

Loads from environment variables first, falls back to defaults
for local development. In production (GitHub Actions), env vars
are set via repository secrets.
"""

import os

# Try to load .env file, but don't crash if python-dotenv isn't installed
# or if there's no .env file (e.g., in GitHub Actions where secrets are injected)
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)
except ImportError:
    pass  # dotenv not installed, rely on system env vars

db_password = os.environ.get("POSTGRES_PASSWORD", "").strip()
if not db_password:
    raise ValueError("POSTGRES_PASSWORD is missing. Please check your .env configuration.")

db_host = os.environ.get("POSTGRES_HOST", "localhost").strip()

# ─── Database ─────────────────────────────────────────────
DB_CONFIG = {
    "dbname": os.environ.get("POSTGRES_DB", "indiaaq").strip(),
    "user": os.environ.get("POSTGRES_USER", "postgres").strip(),
    "password": db_password,
    "host": db_host,
    "port": int(os.environ.get("POSTGRES_PORT", "5432").strip()),
}

# Azure PostgreSQL requires SSL — detect by hostname
if "azure" in db_host or "postgres.database.azure.com" in db_host:
    DB_CONFIG["sslmode"] = "require"

# ─── Paths ────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
MODEL_DIR = os.path.join(BASE_DIR, "models", "v5")
SITE_DATA_DIR = os.path.join(BASE_DIR, "data", "site_data")
DATA_DIR = os.path.join(BASE_DIR, "data")

# Frontend repo path (for deploy step)
# In GitHub Actions, the repo is cloned as a sibling directory
FRONTEND_REPO = os.environ.get(
    "FRONTEND_DATA_PATH",
    os.path.join(os.path.dirname(BASE_DIR), "global-aq-intelligence")
)

# ─── API Keys ─────────────────────────────────────────────
OPENAQ_API_KEY = os.environ.get("OPENAQ_API_KEY", "")
AIRNOW_API_KEY = os.environ.get("AIRNOW_API_KEY", "")

# ─── Countries ────────────────────────────────────────────
COUNTRIES = ["IN", "US", "GB", "AU"]
