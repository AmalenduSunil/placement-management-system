import os
import time
from collections import deque
from datetime import datetime, timedelta

from flask import Flask, g, request
from flask_mail import Mail
from werkzeug.security import generate_password_hash
from sqlalchemy import inspect, text
from config import Config
from model import *
from flask_login import LoginManager

APP_START_TIME = datetime.utcnow()
RECENT_RESPONSE_TIMES = deque(maxlen=500)
RECENT_REQUEST_TIMESTAMPS = deque(maxlen=2000)
mail = Mail()
_DB_INIT_DONE = False


def _should_run_startup_db_init(app: Flask) -> bool:
    # Avoid touching a persistent DB when running unit tests.
    try:
        import sys

        if app.config.get("TESTING") or "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
            return False
    except Exception:
        pass

    # Avoid duplicate startup work under Flask debug reloader.
    if app.config.get("DEBUG", False):
        return os.environ.get("WERKZEUG_RUN_MAIN") == "true"

    return True


def _ensure_default_admin(app: Flask):
    """
    Dev-friendly safety net: create a default admin in the configured DB
    when enabled and no matching admin exists.
    """
    enabled_raw = (os.getenv("AUTO_CREATE_DEFAULT_ADMIN") or "").strip().lower()
    if not enabled_raw:
        enabled_raw = "1" if app.config.get("DEBUG", False) else "0"
    if enabled_raw not in ("1", "true", "yes", "on"):
        return

    default_email = (os.getenv("DEFAULT_ADMIN_EMAIL") or "tpo@cec.ac.in").strip().lower()
    default_name = (os.getenv("DEFAULT_ADMIN_NAME") or "TPO_CEC").strip()
    default_password = os.getenv("DEFAULT_ADMIN_PASSWORD") or "admin123"

    if not default_email or "@" not in default_email or not default_name or not default_password:
        return

    if Admin.query.filter_by(email=default_email).first():
        return
    if Admin.query.filter_by(name=default_name).first():
        return

    try:
        db.session.add(
            Admin(
                email=default_email,
                name=default_name,
                password=generate_password_hash(default_password),
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

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
        if "tenth_percentage" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN tenth_percentage FLOAT")
        if "twelfth_percentage" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN twelfth_percentage FLOAT")
        if "number_of_arrears" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN number_of_arrears INTEGER DEFAULT 0")
        if "technical_skills" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN technical_skills TEXT")
        if "programming_languages" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN programming_languages TEXT")
        if "tools_technologies" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN tools_technologies TEXT")
        if "projects" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN projects TEXT")
        if "internship_experience" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN internship_experience TEXT")
        if "certifications" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN certifications TEXT")
        if "resume_pdf_path" not in columns:
            statements.append("ALTER TABLE students ADD COLUMN resume_pdf_path VARCHAR(255)")
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
                    tenth_percentage FLOAT,
                    twelfth_percentage FLOAT,
                    number_of_arrears INTEGER DEFAULT 0,
                    technical_skills TEXT,
                    programming_languages TEXT,
                    tools_technologies TEXT,
                    projects TEXT,
                    internship_experience TEXT,
                    certifications TEXT,
                    resume_pdf_path VARCHAR(255),
                    created_at DATETIME
                )
            """))

            db.session.execute(text("""
                INSERT INTO students_new (
                    register_number, st_id, name, email, password,
                    phone, department, year, cgpa, tenth_percentage, twelfth_percentage,
                    number_of_arrears, technical_skills, programming_languages,
                    tools_technologies, projects, internship_experience,
                    certifications, resume_pdf_path, created_at
                )
                SELECT
                    CASE
                        WHEN register_number IS NULL OR register_number = ''
                            THEN 'CEC' || printf('%07d', st_id)
                        ELSE register_number
                    END,
                    st_id, name, email, password,
                    phone, department, year, cgpa,
                    tenth_percentage, twelfth_percentage,
                    COALESCE(number_of_arrears, 0),
                    technical_skills, programming_languages, tools_technologies,
                    projects, internship_experience, certifications, resume_pdf_path,
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
        if "min_cgpa" not in columns:
            statements.append("ALTER TABLE placement ADD COLUMN min_cgpa FLOAT")
        if "department" not in columns:
            statements.append("ALTER TABLE placement ADD COLUMN department VARCHAR(80)")
        if "allowed_year" not in columns:
            statements.append("ALTER TABLE placement ADD COLUMN allowed_year INTEGER")
        if "max_arrears" not in columns:
            statements.append("ALTER TABLE placement ADD COLUMN max_arrears INTEGER")
        if "required_programming_languages" not in columns:
            statements.append("ALTER TABLE placement ADD COLUMN required_programming_languages TEXT")
        if "required_technical_skills" not in columns:
            statements.append("ALTER TABLE placement ADD COLUMN required_technical_skills TEXT")
        if "required_tools" not in columns:
            statements.append("ALTER TABLE placement ADD COLUMN required_tools TEXT")

        for stmt in statements:
            db.session.execute(text(stmt))

        if statements:
            db.session.commit()

    # ---------------- Companies Table ----------------
    if "companies" in tables:
        columns = {col["name"] for col in inspector.get_columns("companies")}
        statements = []

        if "industry" not in columns:
            statements.append("ALTER TABLE companies ADD COLUMN industry VARCHAR(100)")
        if "website" not in columns:
            statements.append("ALTER TABLE companies ADD COLUMN website VARCHAR(200)")
        if "contact_person" not in columns:
            statements.append("ALTER TABLE companies ADD COLUMN contact_person VARCHAR(100)")
        if "contact_email" not in columns:
            statements.append("ALTER TABLE companies ADD COLUMN contact_email VARCHAR(120)")
        if "contact_phone" not in columns:
            statements.append("ALTER TABLE companies ADD COLUMN contact_phone VARCHAR(20)")
        if "address" not in columns:
            statements.append("ALTER TABLE companies ADD COLUMN address TEXT")
        if "description" not in columns:
            statements.append("ALTER TABLE companies ADD COLUMN description TEXT")
        if "registration_number" not in columns:
            statements.append("ALTER TABLE companies ADD COLUMN registration_number VARCHAR(50)")
        if "gst_number" not in columns:
            statements.append("ALTER TABLE companies ADD COLUMN gst_number VARCHAR(15)")
        if "status" not in columns:
            statements.append("ALTER TABLE companies ADD COLUMN status VARCHAR(50) DEFAULT 'Pending'")
        if "created_at" not in columns:
            statements.append("ALTER TABLE companies ADD COLUMN created_at DATETIME")

        for stmt in statements:
            db.session.execute(text(stmt))

        if statements:
            db.session.commit()

    # ---------------- Fraud Detection Records Table ----------------
    if "fraud_detection_records" in tables:
        columns = {col["name"] for col in inspector.get_columns("fraud_detection_records")}
        statements = []

        if "features_used" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN features_used TEXT")
        if "status" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN status VARCHAR(20) DEFAULT 'pending'")
        if "fraud_reasons" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN fraud_reasons TEXT")
        if "classification" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN classification VARCHAR(20)")
        if "risk_score_pct" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN risk_score_pct FLOAT DEFAULT 0")
        if "is_fraud" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN is_fraud BOOLEAN DEFAULT 0")
        if "anomaly_score" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN anomaly_score FLOAT DEFAULT 0")
        if "reasons" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN reasons TEXT")
        if "scoring_breakdown_json" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN scoring_breakdown_json TEXT")
        if "layer1_format_json" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN layer1_format_json TEXT")
        if "layer3_web_json" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN layer3_web_json TEXT")
        if "ml_score" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN ml_score FLOAT")
        if "details_json" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN details_json TEXT")
        if "created_at" not in columns:
            statements.append("ALTER TABLE fraud_detection_records ADD COLUMN created_at DATETIME")

        for stmt in statements:
            db.session.execute(text(stmt))

        if statements:
            db.session.commit()

    # ---------------- Notification Table ----------------
    if "notification" in tables:
        columns = {col["name"] for col in inspector.get_columns("notification")}
        statements = []

        if "placement_id" not in columns:
            statements.append("ALTER TABLE notification ADD COLUMN placement_id INTEGER")

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
    mail.init_app(app)

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
        global _DB_INIT_DONE
        if (not _DB_INIT_DONE) and _should_run_startup_db_init(app):
            try:
                db.create_all()
                run_startup_migrations()
                _ensure_default_admin(app)
                _DB_INIT_DONE = True
            except Exception:
                db.session.rollback()
                raise

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
