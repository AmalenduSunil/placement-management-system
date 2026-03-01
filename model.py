from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Admin(db.Model):
    __tablename__ = "admin"
    ad_id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String, nullable=False)

    # Relationships
    placements = db.relationship("Placement", backref="admin", lazy=True)
    fraud_checks = db.relationship("AIFraud", backref="admin", lazy=True)


class Student(db.Model):
    __tablename__ = "students"
    register_number = db.Column(db.String(10), primary_key=True)
    st_id = db.Column(db.Integer, unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String, nullable=False)
    phone = db.Column(db.String(15))
    department = db.Column(db.String(80))
    year = db.Column(db.Integer)
    cgpa = db.Column(db.Float)
    number_of_arrears = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=db.func.now())


class Notification(db.Model):
    __tablename__ = "notification"
    nid = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, nullable=False)
    msgtext = db.Column(db.Text, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("students.st_id"), nullable=False)


class Resume(db.Model):
    __tablename__ = "resume"
    rid = db.Column(db.Integer, primary_key=True)
    skills = db.Column(db.Text)
    project = db.Column(db.Text)
    student_id = db.Column(
        db.Integer, db.ForeignKey("students.st_id"), unique=True, nullable=False
    )


class Placement(db.Model):
    __tablename__ = "placement"
    placeid = db.Column(db.Integer, primary_key=True)
    cmpname = db.Column(db.String(120), nullable=False)
    jobreq = db.Column(db.Text)
    package = db.Column(db.Float)
    eligicri = db.Column(db.Text)
    date = db.Column(db.Date)
    venue = db.Column(db.String(120))
    admin_id = db.Column(db.Integer, db.ForeignKey("admin.ad_id"), nullable=False)

    applications = db.relationship("PlacementApplication", backref="placement", lazy=True)
    fraud_checks = db.relationship("AIFraud", backref="placement", lazy=True)


class AIFraud(db.Model):
    __tablename__ = "aifraud"
    check_id = db.Column(db.Integer, primary_key=True)
    result = db.Column(db.String(50))
    admin_id = db.Column(db.Integer, db.ForeignKey("admin.ad_id"), nullable=False)
    placement_id = db.Column(db.Integer, db.ForeignKey("placement.placeid"), nullable=False)


class PlacementApplication(db.Model):
    __tablename__ = "placement_application"
    app_id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.st_id"), nullable=False)
    placement_id = db.Column(db.Integer, db.ForeignKey("placement.placeid"), nullable=False)
    status = db.Column(db.String(50), default="Pending")

# -------------------------------
# MOCK TEST SYSTEM
# -------------------------------

class MockTest(db.Model):
    __tablename__ = "mock_test"

    test_id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    topic = db.Column(db.String(50), default="General")
    description = db.Column(db.Text)
    duration = db.Column(db.Integer)  # in minutes

    admin_id = db.Column(db.Integer, db.ForeignKey("admin.ad_id"), nullable=False)

    questions = db.relationship("Question", backref="test", lazy=True)


class Question(db.Model):
    __tablename__ = "question"

    question_id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("mock_test.test_id"), nullable=False)

    question_type = db.Column(db.String(20), nullable=False)  
    # "MCQ" or "CODING"

    question_text = db.Column(db.Text, nullable=False)

    # -------- MCQ Fields --------
    option_a = db.Column(db.String(255))
    option_b = db.Column(db.String(255))
    option_c = db.Column(db.String(255))
    option_d = db.Column(db.String(255))
    correct_answer = db.Column(db.String(1))  # A, B, C, D

    # -------- Coding Fields --------
    expected_output = db.Column(db.Text)


class StudentTest(db.Model):
    __tablename__ = "student_test"

    attempt_id = db.Column(db.Integer, primary_key=True)

    student_id = db.Column(db.Integer, db.ForeignKey("students.st_id"), nullable=False)
    test_id = db.Column(db.Integer, db.ForeignKey("mock_test.test_id"), nullable=False)

    score = db.Column(db.Integer)
    started_at = db.Column(db.DateTime)
    submitted_at = db.Column(db.DateTime)


class StudentAnswer(db.Model):
    __tablename__ = "student_answer"

    answer_id = db.Column(db.Integer, primary_key=True)

    attempt_id = db.Column(db.Integer, db.ForeignKey("student_test.attempt_id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("question.question_id"), nullable=False)

    selected_option = db.Column(db.String(1))   # For MCQ
    coding_answer = db.Column(db.Text)         # For CODING
    is_correct = db.Column(db.Boolean)
    time_spent_sec = db.Column(db.Integer, default=0)


# -------------------------------
# FRAUD DETECTION SYSTEM
# -------------------------------

class Company(db.Model):
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(200), unique=True, nullable=False, index=True)
    industry = db.Column(db.String(100))
    website = db.Column(db.String(200))
    contact_person = db.Column(db.String(100))
    contact_email = db.Column(db.String(120))
    contact_phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    description = db.Column(db.Text)
    registration_number = db.Column(db.String(50))
    gst_number = db.Column(db.String(15))
    status = db.Column(db.String(50), default="Active")


class FraudDetectionRecord(db.Model):
    __tablename__ = "fraud_detection_records"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    risk_score = db.Column(db.Float, nullable=False)
    is_fraud = db.Column(db.Boolean, default=False)
    anomaly_score = db.Column(db.Float, nullable=False, default=0.0)
    features_used = db.Column(db.JSON, nullable=False, default=dict)
    analysis_timestamp = db.Column(db.DateTime, default=db.func.now())
    status = db.Column(db.String(50), default="Pending")
    fraud_reasons = db.Column(db.Text)
    override_by_id = db.Column(db.Integer, db.ForeignKey("admin.ad_id"))
    override_reason = db.Column(db.Text)

    company = db.relationship("Company", backref=db.backref("fraud_records", lazy=True))
    override_by = db.relationship("Admin", foreign_keys=[override_by_id])


class LoginEvent(db.Model):
    __tablename__ = "login_event"

    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False)  # student/admin
    identifier = db.Column(db.String(120))
    success = db.Column(db.Boolean, default=False, nullable=False)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=db.func.now(), nullable=False)


class SystemErrorLog(db.Model):
    __tablename__ = "system_error_log"

    id = db.Column(db.Integer, primary_key=True)
    endpoint = db.Column(db.String(200))
    method = db.Column(db.String(10))
    status_code = db.Column(db.Integer, nullable=False)
    message = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=db.func.now(), nullable=False)
