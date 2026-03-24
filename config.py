import os


def _load_local_env():
    """Load environment variables from .env file if it exists."""
    # Load `.env` relative to this file so it works regardless of the current working directory.
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_local_env()


class Config:
    """Flask application configuration.
    
    Supports both SQLite (default for development) and MySQL (recommended for production).
    
    DATABASE_URL Examples:
    - SQLite: sqlite:///placement_db_sqlite3.db
    - MySQL:  mysql+pymysql://user:password@localhost:3306/placement_db
    """
    SECRET_KEY = os.getenv("SECRET_KEY", "my_super_secret_key")
    
    # Database Configuration
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", "sqlite:///placement_db_sqlite3.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # MySQL-specific connection pooling (ignored for SQLite)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,  # Verify connections before using
        "pool_recycle": 3600,   # Recycle connections after 1 hour
        "pool_size": 10,        # Connection pool size
        "max_overflow": 20,     # Maximum overflow connections
    }
    DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"
    MAIL_HOST = os.getenv("MAIL_HOST", os.getenv("MAIL_SERVER", ""))
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "").strip()
    # Gmail App Passwords are often copied with spaces; normalize them.
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "").strip().replace(" ", "")
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "1") == "1"
    MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "0") == "1"
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "").strip()
    # Flask-Mail canonical keys (kept in sync with existing MAIL_* env keys)
    MAIL_SERVER = MAIL_HOST
    MAIL_MAX_EMAILS = int(os.getenv("MAIL_MAX_EMAILS", "20"))
    MAIL_SUPPRESS_SEND = os.getenv("MAIL_SUPPRESS_SEND", "0") == "1"
    COLLEGE_NAME = os.getenv("COLLEGE_NAME", "CEC Placement Administration")
