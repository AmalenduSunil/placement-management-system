from flask import Flask
from sqlalchemy import inspect, text

from model import *


def run_startup_migrations():
    """Lightweight schema sync for existing SQLite DBs without Alembic."""
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()

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


def create_app():
    app = Flask(__name__)

    app.config["SECRET_KEY"] = "my_super_secret_key"
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///placement_db_sqlite3.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    with app.app_context():
        db.create_all()
        run_startup_migrations()

    return app


app = create_app()

from routes import *


if __name__ == "__main__":
    app.run(debug=True)
