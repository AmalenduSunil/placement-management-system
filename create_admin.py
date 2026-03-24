import os

from werkzeug.security import generate_password_hash

from app import app
from model import Admin, db


def create_default_admin() -> bool:
    """
    Create a default admin account if one doesn't already exist.

    Returns True if a new admin was created, False if it already existed.
    """

    default_email = (os.getenv("DEFAULT_ADMIN_EMAIL") or "tpo@cec.ac.in").strip().lower()
    default_name = (os.getenv("DEFAULT_ADMIN_NAME") or "TPO_CEC").strip()
    default_password = os.getenv("DEFAULT_ADMIN_PASSWORD") or "admin123"

    if not default_email or "@" not in default_email:
        raise ValueError("DEFAULT_ADMIN_EMAIL must be a valid email address.")
    if not default_name:
        raise ValueError("DEFAULT_ADMIN_NAME is required.")
    if not default_password:
        raise ValueError("DEFAULT_ADMIN_PASSWORD is required.")

    existing_by_email = Admin.query.filter_by(email=default_email).first()
    if existing_by_email:
        return False

    existing_by_name = Admin.query.filter_by(name=default_name).first()
    if existing_by_name:
        return False

    admin = Admin(
        email=default_email,
        name=default_name,
        password=generate_password_hash(default_password),
    )

    db.session.add(admin)
    db.session.commit()
    return True


if __name__ == "__main__":
    with app.app_context():
        try:
            created = create_default_admin()
        except Exception as exc:
            db.session.rollback()
            raise SystemExit(f"Failed to create admin: {exc}") from exc
        finally:
            try:
                db.session.remove()
            except Exception:
                pass

    print("Admin created successfully!" if created else "Admin already exists.")
