import time
from collections import deque
from datetime import datetime, timedelta

from flask import Flask, g, request
from sqlalchemy import inspect, text
from config import Config
from model import *
from flask_login import LoginManager

APP_START_TIME = datetime.utcnow()
RECENT_RESPONSE_TIMES = deque(maxlen=500)
RECENT_REQUEST_TIMESTAMPS = deque(maxlen=2000)

# ----------------------------
# Startup Migration Function
# ----------------------------

def run_startup_migrations():
    """Lightweight schema sync for existing SQLite DBs without Alembic."""
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()

    # ---------------- Students Table ----------------
    if "students" in tables:
        columns = {col["name"] for col in inspector.get_columns("students")}
        pk_columns = inspector.get_pk_constraint("students").get("constrained_columns", [])
        statements = []

        if "register_number" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN register_number VARCHAR(10)")
        if "phone" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN phone VARCHAR(20)")
        if "department" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN department VARCHAR(80)")
        if "year" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN year INTEGER")
        if "cgpa" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN cgpa FLOAT")
        if "number_of_arrears" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN number_of_arrears INTEGER DEFAULT 0")
        if "created_at" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN created_at DATETIME")

        for stmt in statements:
            db.session.execute(text(stmt))

        if statements:
            db.session.commit()

        # Fix Primary Key if needed
        if pk_columns != ["register_number"]:
            db.session.execute(text("PRAGMA foreign_keys=OFF"))

            db.session.execute(text("""
                CREATE TABLE students_new (
                    register_number VARCHAR(10) PRIMARY KEY,
                    st_id INTEGER NOT NULL UNIQUE,
                    name VARCHAR(120) NOT NULL,
                    email VARCHAR(120) NOT NULL UNIQUE,
                    password VARCHAR NOT NULL,
                    phone VARCHAR(15),
                    department VARCHAR(80),
                    year INTEGER,
                    cgpa FLOAT,
                    number_of_arrears INTEGER DEFAULT 0,
                    created_at DATETIME
                )
            """))

            db.session.execute(text("""
                INSERT INTO students_new (
                    register_number, st_id, name, email, password,
                    phone, department, year, cgpa, number_of_arrears, created_at
                )
                SELECT
                    CASE
                        WHEN register_number IS NULL OR register_number = ''
                            THEN 'CEC' || printf('%07d', st_id)
                        ELSE register_number
                    END,
                    st_id, name, email, password,
                    phone, department, year, cgpa,
                    COALESCE(number_of_arrears, 0),
                    COALESCE(created_at, CURRENT_TIMESTAMP)
                FROM students
            """))

            db.session.execute(text("DROP TABLE students"))
            db.session.execute(text("ALTER TABLE students_new RENAME TO students"))
            db.session.execute(text("PRAGMA foreign_keys=ON"))
            db.session.commit()

        db.session.execute(text("""
            UPDATE students
            SET register_number = 'CEC' || printf('%07d', st_id)
            WHERE register_number IS NULL OR register_number = ''
        """))
        db.session.execute(text("""
            UPDATE students
            SET created_at = CURRENT_TIMESTAMP
            WHERE created_at IS NULL
        """))
        db.session.commit()

    # ---------------- Placement Table ----------------
    if "placement" in tables:
        columns = {col["name"] for col in inspector.get_columns("placement")}
        statements = []

        if "date" not in columns:
            statements.append("ALTER TABLE placement ADD COLUMN date DATE")
        if "venue" not in columns:
            statements.append("ALTER TABLE placement ADD COLUMN venue VARCHAR(120)")

        for stmt in statements:
            db.session.execute(text(stmt))

        if statements:
            db.session.commit()

    # ---------------- Mock Test Table ----------------
    if "mock_test" in tables:
        columns = {col["name"] for col in inspector.get_columns("mock_test")}
        statements = []
        if "topic" not in columns:
            statements.append("ALTER TABLE mock_test ADD COLUMN topic VARCHAR(50) DEFAULT 'General'")
        for stmt in statements:
            db.session.execute(text(stmt))
        if statements:
            db.session.commit()

    # ---------------- Student Test Table ----------------
    if "student_test" in tables:
        columns = {col["name"] for col in inspector.get_columns("student_test")}
        statements = []
        if "started_at" not in columns:
            statements.append("ALTER TABLE student_test ADD COLUMN started_at DATETIME")
        for stmt in statements:
            db.session.execute(text(stmt))
        if statements:
            db.session.commit()

    # ---------------- Student Answer Table ----------------
    if "student_answer" in tables:
        columns = {col["name"] for col in inspector.get_columns("student_answer")}
        statements = []
        if "is_correct" not in columns:
            statements.append("ALTER TABLE student_answer ADD COLUMN is_correct BOOLEAN")
        if "time_spent_sec" not in columns:
            statements.append("ALTER TABLE student_answer ADD COLUMN time_spent_sec INTEGER DEFAULT 0")
        for stmt in statements:
            db.session.execute(text(stmt))
        if statements:
            db.session.commit()

    # ---------------- Companies Table ----------------
    if "companies" in tables:
        columns = {col["name"] for col in inspector.get_columns("companies")}
        statements = []

        if "gst_number" not in columns:
            statements.append("ALTER TABLE companies ADD COLUMN gst_number VARCHAR(15)")

        for stmt in statements:
            db.session.execute(text(stmt))

        if statements:
            db.session.commit()


# ----------------------------
# Application Factory
# ----------------------------

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    @app.before_request
    def track_request_start():
        g.request_start_time = time.perf_counter()

    @app.after_request
    def add_no_cache_headers(response):
        # Request timing and basic load tracking.
        started = getattr(g, "request_start_time", None)
        if started is not None:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            RECENT_RESPONSE_TIMES.append(elapsed_ms)

        now = datetime.utcnow()
        RECENT_REQUEST_TIMESTAMPS.append(now)
        window_start = now - timedelta(minutes=5)
        while RECENT_REQUEST_TIMESTAMPS and RECENT_REQUEST_TIMESTAMPS[0] < window_start:
            RECENT_REQUEST_TIMESTAMPS.popleft()

        is_api_error = ("/api/" in request.path) and response.status_code >= 400
        is_server_error = response.status_code >= 500
        if is_api_error or is_server_error:
            try:
                db.session.add(
                    SystemErrorLog(
                        endpoint=request.path,
                        method=request.method,
                        status_code=response.status_code,
                        message="API error" if is_api_error else "Server error",
                    )
                )
                db.session.commit()
            except Exception:
                db.session.rollback()

        # Prevent back-button access to cached authenticated pages after logout.
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    # 🔐 Setup Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'admin_login'

    @login_manager.user_loader
    def load_user(user_id):
        return Admin.query.get(int(user_id))

    with app.app_context():
        db.create_all()
        run_startup_migrations()

    return app


# ----------------------------
# Create App Instance
# ----------------------------

app = create_app()

# Import routes AFTER app creation
from routes import *

# ----------------------------
# Run Server
# ----------------------------

if __name__ == "__main__":
    app.run(debug=app.config.get("DEBUG", True))
