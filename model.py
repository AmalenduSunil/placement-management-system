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


class Student(db.Model):
    __tablename__ = "students"
    register_number = db.Column(db.String(10), primary_key=True)
    st_id = db.Column(db.Integer, unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String, nullable=False)
    phone = db.Column(db.String(15))
    department = db.Column(db.String(80), nullable=True)
    year = db.Column(db.Integer, nullable=True)
    cgpa = db.Column(db.Float, nullable=True)
    tenth_percentage = db.Column(db.Float, nullable=True)
    twelfth_percentage = db.Column(db.Float, nullable=True)
    number_of_arrears = db.Column(db.Integer, default=0, nullable=True)
    technical_skills = db.Column(db.Text, nullable=True)
    programming_languages = db.Column(db.Text, nullable=True)
    tools_technologies = db.Column(db.Text, nullable=True)
    projects = db.Column(db.Text, nullable=True)
    internship_experience = db.Column(db.Text, nullable=True)
    certifications = db.Column(db.Text, nullable=True)
    resume_pdf_path = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.now())

    def missing_profile_fields(self):
        """Return a list of required profile fields that are missing for placement applications."""
        missing = []
        if self.cgpa is None:
            missing.append("CGPA")
        if not (self.department or "").strip():
            missing.append("Department")
        if self.year is None:
            missing.append("Year")
        return missing

    def has_complete_profile(self):
        """Check if student has completed their profile."""
        # Keep requirements minimal so students aren't blocked unnecessarily.
        # Eligibility rules (skills, resume, etc.) are enforced separately per-drive.
        return len(self.missing_profile_fields()) == 0


class Notification(db.Model):
    __tablename__ = "notification"
    nid = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, nullable=False)
    msgtext = db.Column(db.Text, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("students.st_id"), nullable=False)
    placement_id = db.Column(db.Integer, nullable=True, index=True)


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
    min_cgpa = db.Column(db.Float)
    department = db.Column(db.String(80))
    allowed_year = db.Column(db.Integer)
    max_arrears = db.Column(db.Integer)
    required_programming_languages = db.Column(db.Text)
    required_technical_skills = db.Column(db.Text)
    required_tools = db.Column(db.Text)
    admin_id = db.Column(db.Integer, db.ForeignKey("admin.ad_id"), nullable=False)

    applications = db.relationship("PlacementApplication", backref="placement", lazy=True)


class PlacementApplication(db.Model):
    __tablename__ = "placement_application"
    app_id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.st_id"), nullable=False)
    placement_id = db.Column(db.Integer, db.ForeignKey("placement.placeid"), nullable=False)
    status = db.Column(db.String(50), default="Pending")


class Company(db.Model):
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(200), unique=True, nullable=False, index=True)
    industry = db.Column(db.String(100))
    website = db.Column(db.String(200))
    contact_person = db.Column(db.String(100))
    contact_email = db.Column(db.String(120), index=True)
    contact_phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    description = db.Column(db.Text)
    registration_number = db.Column(db.String(50), index=True)
    gst_number = db.Column(db.String(15), index=True)
    status = db.Column(db.String(50), default="Pending")
    created_at = db.Column(db.DateTime, default=db.func.now(), nullable=False, index=True)


class FraudDetectionRecord(db.Model):
    __tablename__ = "fraud_detection_records"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)

    # Backward-compatible with earlier schema versions (0..1 risk score)
    risk_score = db.Column(db.Float, nullable=False, default=0.0)

    # Backward-compatible with earlier schema versions that stored feature payload as JSON
    # (SQLite stores it as TEXT here).
    features_used = db.Column(db.Text, nullable=False, default="{}")

    # Backward-compatible with earlier schema versions that used `status`/`fraud_reasons`.
    status = db.Column(db.String(20), nullable=False, default="pending")
    fraud_reasons = db.Column(db.Text, default="")

    classification = db.Column(db.String(20), nullable=False)  # legitimate/suspicious/fraud
    risk_score_pct = db.Column(db.Float, nullable=False, default=0.0)
    is_fraud = db.Column(db.Boolean, nullable=False, default=False)
    anomaly_score = db.Column(db.Float, nullable=False, default=0.0)

    reasons = db.Column(db.Text)
    scoring_breakdown_json = db.Column(db.Text)  # JSON string
    layer1_format_json = db.Column(db.Text)  # JSON string
    layer3_web_json = db.Column(db.Text)  # JSON string
    ml_score = db.Column(db.Float, nullable=True)
    details_json = db.Column(db.Text)  # JSON string for full analysis

    created_at = db.Column(db.DateTime, default=db.func.now(), nullable=False, index=True)

    company = db.relationship("Company", backref=db.backref("fraud_records", lazy=True))


class MockTest(db.Model):
    __tablename__ = "mock_tests"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100))
    description = db.Column(db.Text)
    duration = db.Column(db.Integer)  # minutes
    question_count = db.Column(db.Integer, default=10)
    is_published = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=db.func.now())


class Question(db.Model):
    __tablename__ = "questions"

    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("mock_tests.id"))
    section = db.Column(db.String(50), default="Aptitude")
    question_text = db.Column(db.Text)

    option_a = db.Column(db.String(255))
    option_b = db.Column(db.String(255))
    option_c = db.Column(db.String(255))
    option_d = db.Column(db.String(255))

    correct_answer = db.Column(db.String(1))


class QuestionBank(db.Model):
    __tablename__ = "question_bank"

    id = db.Column(db.Integer, primary_key=True)
    section = db.Column(db.String(50), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.String(255), nullable=False)
    option_b = db.Column(db.String(255), nullable=False)
    option_c = db.Column(db.String(255), nullable=False)
    option_d = db.Column(db.String(255), nullable=False)
    correct_answer = db.Column(db.String(1), nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.now())


class TestResult(db.Model):
    __tablename__ = "test_results"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer)
    test_id = db.Column(db.Integer)
    score = db.Column(db.Integer)
    total_questions = db.Column(db.Integer)
    aptitude_score = db.Column(db.Integer, default=0)
    aptitude_total = db.Column(db.Integer, default=0)
    technical_score = db.Column(db.Integer, default=0)
    technical_total = db.Column(db.Integer, default=0)
    submitted_at = db.Column(db.DateTime, default=db.func.now())


class CsvMockTestAttempt(db.Model):
    __tablename__ = "csv_mock_test_attempts"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.st_id"), nullable=False, index=True)

    created_at = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    submitted_at = db.Column(db.DateTime, nullable=True)

    total_questions = db.Column(db.Integer, nullable=False, default=30)
    section_plan_json = db.Column(db.Text, nullable=False)
    questions_json = db.Column(db.Text, nullable=False)
    answer_key_json = db.Column(db.Text, nullable=False)

    # Filled on submission
    answers_json = db.Column(db.Text, nullable=True)
    correct_count = db.Column(db.Integer, nullable=True)
    score_pct = db.Column(db.Float, nullable=True)
    section_breakdown_json = db.Column(db.Text, nullable=True)

    student = db.relationship("Student", backref=db.backref("csv_mock_attempts", lazy=True))


class CsvAdaptiveTestAttempt(db.Model):
    __tablename__ = "csv_adaptive_test_attempts"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.st_id"), nullable=False, index=True)

    created_at = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    submitted_at = db.Column(db.DateTime, nullable=True)

    total_questions = db.Column(db.Integer, nullable=False, default=30)
    section_plan_json = db.Column(db.Text, nullable=False)
    remaining_plan_json = db.Column(db.Text, nullable=False)
    difficulty_state_json = db.Column(db.Text, nullable=False)

    seed = db.Column(db.Integer, nullable=False)
    asked_keys_json = db.Column(db.Text, nullable=False)  # de-dup key list

    pending_question_id = db.Column(db.String(32), nullable=True, index=True)
    served_questions_json = db.Column(db.Text, nullable=False)  # list of questions incl answer
    answer_key_json = db.Column(db.Text, nullable=False)  # {qid: "A"/"B"/...}
    answers_json = db.Column(db.Text, nullable=False)  # {qid: "A"/...}

    correct_count = db.Column(db.Integer, nullable=False, default=0)
    weighted_score_pct = db.Column(db.Float, nullable=True)
    score_pct = db.Column(db.Float, nullable=True)
    section_breakdown_json = db.Column(db.Text, nullable=True)

    student = db.relationship("Student", backref=db.backref("csv_adaptive_attempts", lazy=True))


class StudentMockTestResult(db.Model):
    __tablename__ = "student_mock_test_results"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.st_id"), nullable=False, index=True)

    source = db.Column(db.String(40), nullable=False, index=True)  # csv_full, csv_practice, csv_adaptive, db_test
    attempt_id = db.Column(db.Integer, nullable=True, index=True)

    score = db.Column(db.Integer, nullable=False, default=0)  # correct count
    total_questions = db.Column(db.Integer, nullable=False, default=0)

    aptitude_score = db.Column(db.Integer, nullable=False, default=0)
    logical_score = db.Column(db.Integer, nullable=False, default=0)
    technical_score = db.Column(db.Integer, nullable=False, default=0)
    coding_score = db.Column(db.Integer, nullable=False, default=0)

    submitted_at = db.Column(db.DateTime, default=db.func.now(), nullable=False, index=True)

    student = db.relationship("Student", backref=db.backref("mock_results", lazy=True))


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
