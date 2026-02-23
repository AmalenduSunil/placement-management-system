from app import app
from model import db, Admin
from werkzeug.security import generate_password_hash

with app.app_context():
    # Check if admin already exists
    existing_admin = Admin.query.filter_by(email="tpo@cec.ac.in").first()

    if not existing_admin:
        admin = Admin(
            name="TPO_CEC",
            email="tpo@cec.ac.in",
            password=generate_password_hash("admin123")
        )
        db.session.add(admin)
        db.session.commit()
        print("Admin created successfully!")
    else:
        print("Admin already exists!")