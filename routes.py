import re
import os
import json
import csv
import io
from datetime import datetime, timedelta
from urllib.parse import urlparse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, text
from sqlalchemy.orm import joinedload

from flask import Response, flash, jsonify, make_response, redirect, render_template, request, session, url_for
from flask_mail import Message
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import StandardScaler
except Exception:
    RandomForestClassifier = None
    train_test_split = None
    StandardScaler = None

from app import app, db, mail, APP_START_TIME, RECENT_REQUEST_TIMESTAMPS, RECENT_RESPONSE_TIMES
from model import (
    AIFraud,
    Admin,
    MockTest,
    Question,
    QuestionBank,
    TestResult,
    Placement,
    PlacementApplication,
    Notification,
    Student,
    Company,
    FraudDetectionRecord,
    LoginEvent,
    SystemErrorLog,
)

FRAUD_MODEL_VERSION = "rf-binary-v3"
TRUSTED_SAFE_COMPANIES = {"novatech solutions"}
ALLOWED_RESUME_EXTENSIONS = {"pdf"}
ENABLE_ADMIN_MOCK_TEST_MANAGEMENT = False


# =========================
# HELPER FUNCTIONS
# =========================
def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_company_name(name):
    raw = (name or "").strip().lower()
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    tokens = [t for t in raw.split() if t not in {"pvt", "private", "ltd", "limited", "llp", "inc"}]
    return " ".join(tokens)


def _split_skill_tokens(raw_text):
    return {
        token.strip().lower()
        for token in re.split(r"[,\n/;|]", raw_text or "")
        if token and token.strip()
    }


def _extract_resume_text(upload_file):
    if not upload_file or not upload_file.filename:
        return ""
    filename = (upload_file.filename or "").lower()
    if not filename.endswith(".txt"):
        return ""
    try:
        return upload_file.stream.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _build_resume_enhancement(student, resume_text, target_role):
    text = (resume_text or "").strip()
    role = (target_role or "").strip() or "Software Engineer"

    skills = []
    for raw in [
        student.programming_languages,
        student.technical_skills,
        student.tools_technologies,
    ]:
        skills.extend([s.strip() for s in re.split(r"[,\n;/|]", raw or "") if s.strip()])
    unique_skills = []
    seen = set()
    for s in skills:
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_skills.append(s)

    has_projects = bool((student.projects or "").strip())
    has_internship = bool((student.internship_experience or "").strip())
    has_certs = bool((student.certifications or "").strip())

    checklist = [
        {"item": "Professional summary tailored to role", "ok": bool(text)},
        {"item": "Skills section with relevant keywords", "ok": len(unique_skills) >= 5},
        {"item": "Projects with measurable outcomes", "ok": has_projects},
        {"item": "Internship/experience section", "ok": has_internship},
        {"item": "Certifications section", "ok": has_certs},
    ]

    suggestions = []
    if not text:
        suggestions.append("Add a 3-4 line professional summary tailored to your target role.")
    suggestions.append("Use action verbs and quantify outcomes (e.g., improved performance by 20%).")
    if len(unique_skills) < 5:
        suggestions.append("Expand technical skills to include at least 5 role-relevant keywords.")
    if not has_projects:
        suggestions.append("Add at least 2 academic/personal projects with tech stack and results.")
    if not has_internship:
        suggestions.append("Include internship or practical training details, if available.")
    if student.cgpa is not None:
        suggestions.append(f"Highlight CGPA ({student.cgpa}) in Education section for screening visibility.")

    summary_lines = [
        f"A motivated {student.year or ''} year {student.department or ''} student targeting {role} roles.".strip(),
        "Skilled in " + (", ".join(unique_skills[:8]) if unique_skills else "software development fundamentals") + ".",
        "Strong problem-solving ability with hands-on project experience and placement-focused preparation.",
    ]
    enhanced_summary = " ".join([line for line in summary_lines if line])

    project_bullets = []
    raw_projects = [p.strip() for p in re.split(r"[\n;]+", student.projects or "") if p.strip()]
    for item in raw_projects[:4]:
        project_bullets.append(f"Built {item} using relevant technologies; improved functionality and user impact.")
    if not project_bullets:
        project_bullets.append("Built end-to-end academic projects with clear problem statements and measurable outcomes.")

    return {
        "target_role": role,
        "enhanced_summary": enhanced_summary,
        "keywords": unique_skills[:20],
        "project_bullets": project_bullets,
        "suggestions": suggestions[:8],
        "checklist": checklist,
    }


def _normalize_test_section(section):
    raw = (section or "").strip().lower()
    if raw == "aptitude":
        return "Aptitude"
    if raw == "logical":
        return "Logical"
    if raw == "technical":
        return "Technical"
    if raw == "coding":
        return "Coding"
    return ""


def _resolve_test_question_count(test):
    configured = _safe_int(getattr(test, "question_count", None))
    if configured in (10, 30):
        return configured

    text = f"{(test.title or '')} {(test.description or '')}".lower()
    if "30" in text:
        return 30
    return 10


def _build_section_plan(total_questions):
    if total_questions == 30:
        return {"Aptitude": 10, "Logical": 10, "Technical": 5, "Coding": 5}
    return {"Aptitude": 3, "Logical": 3, "Technical": 2, "Coding": 2}


def _coerce_correct_answer_letter(raw_value, option_a, option_b, option_c, option_d):
    raw = (raw_value or "").strip()
    if not raw:
        return ""

    upper = raw.upper()
    if upper in {"A", "B", "C", "D"}:
        return upper

    options = {
        "A": (option_a or "").strip(),
        "B": (option_b or "").strip(),
        "C": (option_c or "").strip(),
        "D": (option_d or "").strip(),
    }

    for letter, value in options.items():
        if raw == value:
            return letter

    raw_folded = raw.casefold()
    for letter, value in options.items():
        if raw_folded == value.casefold():
            return letter

    return ""


def _seed_question_bank_from_local_csv_if_needed(min_required):
    """Best-effort seed from questions.csv when admin upload is disabled."""
    try:
        existing_count = QuestionBank.query.filter(QuestionBank.correct_answer.in_(("A", "B", "C", "D"))).count()
    except Exception:
        return 0

    if existing_count >= min_required:
        return 0

    candidates = [
        os.path.join(app.root_path, "questions.csv"),
        os.path.join(os.getcwd(), "questions.csv"),
    ]
    csv_path = next((p for p in candidates if os.path.exists(p)), "")
    if not csv_path:
        return 0

    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
    except OSError:
        return 0

    required_columns = {
        "section",
        "question_text",
        "option_a",
        "option_b",
        "option_c",
        "option_d",
        "correct_answer",
    }
    file_columns = set(reader.fieldnames or [])
    if required_columns - file_columns:
        return 0

    try:
        existing_keys = {
            ((row.section or "").strip(), (row.question_text or "").strip())
            for row in QuestionBank.query.with_entities(QuestionBank.section, QuestionBank.question_text).all()
        }
    except Exception:
        existing_keys = set()

    allowed_sections = {"Aptitude", "Logical", "Technical", "Coding"}
    to_insert = []
    for row in rows:
        section = _normalize_test_section(row.get("section"))
        question_text = (row.get("question_text") or "").strip()
        option_a = (row.get("option_a") or "").strip()
        option_b = (row.get("option_b") or "").strip()
        option_c = (row.get("option_c") or "").strip()
        option_d = (row.get("option_d") or "").strip()
        correct_answer = _coerce_correct_answer_letter(row.get("correct_answer"), option_a, option_b, option_c, option_d)

        if section not in allowed_sections:
            continue
        if not question_text or not option_a or not option_b or not option_c or not option_d:
            continue
        if correct_answer not in {"A", "B", "C", "D"}:
            continue

        key = (section, question_text)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        to_insert.append(
            QuestionBank(
                section=section,
                question_text=question_text,
                option_a=option_a,
                option_b=option_b,
                option_c=option_c,
                option_d=option_d,
                correct_answer=correct_answer,
            )
        )

    if not to_insert:
        return 0

    try:
        db.session.bulk_save_objects(to_insert)
        db.session.commit()
        return len(to_insert)
    except Exception:
        db.session.rollback()
        return 0


def _pick_questions_from_bank(total_questions):
    plan = _build_section_plan(total_questions)
    selected = []
    selected_ids = set()
    missing_count = 0
    try:
        for section, required in plan.items():
            section_rows = (
                QuestionBank.query.filter_by(section=section).filter(
                    QuestionBank.correct_answer.in_(("A", "B", "C", "D"))
                )
                .order_by(func.random())
                .limit(required)
                .all()
            )
            selected.extend(section_rows)
            selected_ids.update(row.id for row in section_rows)
            missing_count += max(0, required - len(section_rows))

        if missing_count > 0:
            query = QuestionBank.query.filter(QuestionBank.correct_answer.in_(("A", "B", "C", "D")))
            if selected_ids:
                query = query.filter(~QuestionBank.id.in_(selected_ids))
            top_up = query.order_by(func.random()).limit(missing_count).all()
            selected.extend(top_up)
            selected_ids.update(row.id for row in top_up)
    except Exception:
        return [], plan

    if len(selected) < total_questions:
        return [], plan

    selected = selected[:total_questions]
    grouped = {key: [] for key in ("Aptitude", "Logical", "Technical", "Coding")}
    for row in selected:
        section_key = _normalize_test_section(row.section) or "Technical"
        grouped.setdefault(section_key, []).append(row)

    return grouped, plan


def _pick_questions_from_legacy(test_id, total_questions):
    plan = _build_section_plan(total_questions)
    selected = []
    selected_ids = set()
    missing_count = 0

    for section, required in plan.items():
        rows = (
            Question.query.filter_by(test_id=test_id, section=section)
            .order_by(func.random())
            .limit(required)
            .all()
        )
        selected.extend(rows)
        selected_ids.update(row.id for row in rows)
        missing_count += max(0, required - len(rows))

    if missing_count > 0:
        query = Question.query.filter_by(test_id=test_id)
        if selected_ids:
            query = query.filter(~Question.id.in_(selected_ids))
        top_up = query.order_by(func.random()).limit(missing_count).all()
        selected.extend(top_up)

    if len(selected) < total_questions:
        return {}, plan

    selected = selected[:total_questions]
    grouped = {key: [] for key in ("Aptitude", "Logical", "Technical", "Coding")}
    for row in selected:
        section_key = _normalize_test_section(row.section) or "Technical"
        grouped.setdefault(section_key, []).append(row)
    return grouped, plan


def check_eligibility_details(student, placement):
    min_cgpa = getattr(placement, "min_cgpa", None)
    required_department = (getattr(placement, "department", None) or "").strip()
    allowed_year = getattr(placement, "allowed_year", None)
    max_arrears = getattr(placement, "max_arrears", None)

    cgpa_ok = True if min_cgpa is None else (student.cgpa is not None and student.cgpa >= min_cgpa)
    department_ok = (
        True
        if not required_department or required_department.lower() == "all"
        else ((student.department or "").strip().lower() == required_department.lower())
    )
    year_ok = True if allowed_year is None else (student.year is not None and student.year == allowed_year)
    student_arrears = student.number_of_arrears if student.number_of_arrears is not None else 0
    arrears_ok = True if max_arrears is None else (student_arrears <= max_arrears)

    required_skills = set()
    required_skills.update(_split_skill_tokens(getattr(placement, "required_programming_languages", None)))
    required_skills.update(_split_skill_tokens(getattr(placement, "required_technical_skills", None)))
    required_skills.update(_split_skill_tokens(getattr(placement, "required_tools", None)))

    student_skills = set()
    student_skills.update(_split_skill_tokens(student.programming_languages))
    student_skills.update(_split_skill_tokens(student.technical_skills))
    student_skills.update(_split_skill_tokens(student.tools_technologies))

    matched = sorted(student_skills & required_skills)
    if required_skills:
        skill_match = (len(matched) / len(required_skills)) * 100.0
    else:
        skill_match = 100.0
    skills_ok = skill_match >= 60.0

    eligible = cgpa_ok and department_ok and year_ok and arrears_ok and skills_ok
    return {
        "eligible": eligible,
        "skill_match": round(skill_match, 2),
        "matched_skills": matched,
        "cgpa_ok": cgpa_ok,
        "department_ok": department_ok,
        "arrears_ok": arrears_ok,
        "year_ok": year_ok,
    }


def _is_strong_password(password):
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if not re.search(r"[A-Z]", password):
        return False, "Password must include at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return False, "Password must include at least one lowercase letter."
    if not re.search(r"\d", password):
        return False, "Password must include at least one number."
    if not re.search(r"[^A-Za-z0-9]", password):
        return False, "Password must include at least one special character."
    return True, ""


def _is_allowed_resume_file(filename):
    return (
        "." in (filename or "")
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_RESUME_EXTENSIONS
    )


def _next_student_id():
    latest_student_id = db.session.query(db.func.max(Student.st_id)).scalar()
    return (latest_student_id or 0) + 1


def _log_login_event(role, identifier, success):
    try:
        db.session.add(
            LoginEvent(
                role=role,
                identifier=identifier,
                success=success,
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string or "")[:255],
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


def _send_html_email(recipient, subject, html_body, text_body):
    mail_server = (app.config.get("MAIL_SERVER") or "").strip()
    mail_port = int(app.config.get("MAIL_PORT") or 0)
    default_sender = (app.config.get("MAIL_DEFAULT_SENDER") or "").strip()
    mail_username = (app.config.get("MAIL_USERNAME") or "").strip()
    mail_password = (app.config.get("MAIL_PASSWORD") or "").strip()
    sender = default_sender or mail_username

    if not mail_server or not mail_port:
        return False, "Mail server is not configured."

    if not sender:
        return False, "Mail sender is not configured."

    if (
        "your-email@" in mail_username.lower()
        or "your-email@" in sender.lower()
        or "your-16-char" in mail_password.lower()
    ):
        return False, "Mail credentials are placeholders."

    message = Message(
        subject=subject,
        recipients=[recipient],
        body=text_body or "Please view this email in an HTML-supported client.",
        html=html_body,
        sender=sender,
    )

    try:
        mail.send(message)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _send_placement_result_email(student, placement, status, custom_message):
    status_label = (status or "Selected").strip().title()
    html_body = render_template(
        "emails/placement_result_notification.html",
        student=student,
        placement=placement,
        status_label=status_label,
        custom_message=custom_message,
        config=app.config,
    )

    text_body = (
        f"Hello {student.name},\n\n"
        f"Your application status for {placement.cmpname} is now: {status_label}.\n"
        f"{custom_message or ''}\n\n"
        "Please login to the student portal for details."
    )

    subject = f"Placement Update: {placement.cmpname} - {status_label}"
    return _send_html_email(student.email, subject, html_body, text_body)


def _send_placement_drive_email(student, placement):
    html_body = render_template(
        "emails/placement_drive_scheduled.html",
        student=student,
        drive=placement,
        config=app.config,
    )

    drive_date_text = (
        placement.date.strftime("%B %d, %Y") if placement.date else "To be announced"
    )
    text_body = (
        f"Hello {student.name},\n\n"
        f"A new placement drive has been scheduled for {placement.cmpname}.\n"
        f"Role: {placement.jobreq or 'Not specified'}\n"
        f"Date: {drive_date_text}\n"
        f"Venue: {placement.venue or 'To be announced'}\n\n"
        "Please login to the student portal and register."
    )
    subject = f"Placement Drive Scheduled: {placement.cmpname}"
    return _send_html_email(student.email, subject, html_body, text_body)


def _password_reset_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="student-password-reset")


def _generate_password_reset_token(student):
    serializer = _password_reset_serializer()
    return serializer.dumps({"st_id": student.st_id, "email": student.email})


def _verify_password_reset_token(token, max_age_seconds=1800):
    serializer = _password_reset_serializer()
    try:
        payload = serializer.loads(token, max_age=max_age_seconds)
    except SignatureExpired:
        return None, "expired"
    except BadSignature:
        return None, "invalid"

    email = (payload.get("email") or "").strip().lower()
    st_id = _safe_int(payload.get("st_id"))
    if not email or st_id is None:
        return None, "invalid"

    student = Student.query.filter_by(st_id=st_id, email=email).first()
    if not student:
        return None, "invalid"

    return student, ""


def _safe_float_range(value, default=0.0):
    val = _safe_float(value)
    return default if val is None else val


def _build_student_analytics(student_id):
    _ensure_mock_test_schema()
    results = (
        TestResult.query.filter_by(student_id=student_id)
        .order_by(TestResult.submitted_at.asc(), TestResult.id.asc())
        .all()
    )

    if not results:
        return {
            "labels": [],
            "scores": [],
            "overall_accuracy": 0.0,
            "overall_correct": 0,
            "overall_total": 0,
            "attempts_count": 0,
            "topic_labels": [],
            "topic_accuracy": [],
            "weak_topics": [],
            "strong_topics": [],
            "avg_questions_per_minute": 0.0,
            "coding_completion": 0.0,
            "trend_delta": 0.0,
            "suggestions": ["No test attempts yet. Start with an Aptitude mock test."],
        }

    test_ids = sorted({r.test_id for r in results if r.test_id is not None})
    tests = MockTest.query.filter(MockTest.id.in_(test_ids)).all() if test_ids else []
    tests_by_id = {t.id: t for t in tests}

    section_counts_by_test = {}
    if test_ids:
        rows = (
            db.session.query(
                Question.test_id,
                Question.section,
                func.count(Question.id),
            )
            .filter(Question.test_id.in_(test_ids))
            .group_by(Question.test_id, Question.section)
            .all()
        )
        for test_id, section, count in rows:
            section_key = (section or "General").strip().title()
            section_counts_by_test.setdefault(test_id, {})[section_key] = count

    def _infer_test_topic(test_obj, test_id):
        section_counts = section_counts_by_test.get(test_id, {})
        if section_counts:
            return max(section_counts.items(), key=lambda item: item[1])[0]
        text = f"{(test_obj.title if test_obj else '')} {(test_obj.description if test_obj else '')}".lower()
        if "technical" in text:
            return "Technical"
        if "aptitude" in text:
            return "Aptitude"
        return "General"

    labels = []
    scores = []
    overall_correct = 0
    overall_total = 0
    total_minutes = 0
    topic_values = {}

    for idx, r in enumerate(results, start=1):
        test_obj = tests_by_id.get(r.test_id)
        label = (test_obj.title if test_obj and test_obj.title else f"Test {idx}")[:25]
        percent = (r.score / r.total_questions * 100.0) if r.total_questions else 0.0
        labels.append(label)
        scores.append(round(percent, 2))
        overall_correct += (r.score or 0)
        overall_total += (r.total_questions or 0)
        if test_obj and test_obj.duration:
            total_minutes += test_obj.duration
        topic = _infer_test_topic(test_obj, r.test_id)
        topic_values.setdefault(topic, []).append(percent)

    aptitude_correct = sum((r.aptitude_score or 0) for r in results)
    aptitude_total = sum((r.aptitude_total or 0) for r in results)
    technical_correct = sum((r.technical_score or 0) for r in results)
    technical_total = sum((r.technical_total or 0) for r in results)

    topic_labels = []
    topic_accuracy = []
    if aptitude_total > 0:
        topic_labels.append("Aptitude")
        topic_accuracy.append(round((aptitude_correct / aptitude_total) * 100.0, 2))
    if technical_total > 0:
        topic_labels.append("Technical")
        topic_accuracy.append(round((technical_correct / technical_total) * 100.0, 2))
    if not topic_labels:
        topic_labels = list(topic_values.keys())
        topic_accuracy = [round(sum(vals) / len(vals), 2) for vals in topic_values.values()]
    weak_topics = [t for t, acc in zip(topic_labels, topic_accuracy) if acc < 60]
    strong_topics = [t for t, acc in zip(topic_labels, topic_accuracy) if acc >= 75]

    overall_accuracy = round((overall_correct / overall_total * 100.0), 2) if overall_total else 0.0
    avg_questions_per_minute = round((overall_total / total_minutes), 2) if total_minutes else 0.0
    trend_delta = round(scores[-1] - scores[0], 2) if len(scores) > 1 else 0.0

    suggestions = []
    if weak_topics:
        suggestions.append(f"Focus on weak sections: {', '.join(weak_topics)}.")
    if avg_questions_per_minute < 1.0:
        suggestions.append("Improve speed with timed quizzes.")
    if trend_delta < 0:
        suggestions.append("Recent score trend is down; revise fundamentals and retest.")
    if not suggestions:
        suggestions.append("Keep practicing consistently to sustain performance.")

    return {
        "labels": labels,
        "scores": scores,
        "overall_accuracy": overall_accuracy,
        "overall_correct": overall_correct,
        "overall_total": overall_total,
        "attempts_count": len(results),
        "topic_labels": topic_labels,
        "topic_accuracy": topic_accuracy,
        "weak_topics": weak_topics,
        "strong_topics": strong_topics,
        "avg_questions_per_minute": avg_questions_per_minute,
        "coding_completion": 0.0,
        "trend_delta": trend_delta,
        "suggestions": suggestions,
    }


def log_fraud_result(payload):
    """Persist fraud detection events in local JSONL log."""
    log_dir = os.path.join(app.root_path, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "fraud_detection.json")
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "data": payload,
    }
    try:
        with open(log_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(log_entry) + "\n")
    except OSError:
        # Logging failure should not block the request lifecycle.
        pass


def _ensure_mock_test_schema():
    # Runtime compatibility for existing SQLite DBs without latest columns.
    engine_name = (db.engine.url.drivername or "").lower()
    if "sqlite" not in engine_name:
        return

    table_columns = {}
    for table_name in ("test_results", "questions", "mock_tests"):
        rows = db.session.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
        table_columns[table_name] = {row[1] for row in rows} if rows else set()

    alter_statements = []
    if "aptitude_score" not in table_columns.get("test_results", set()):
        alter_statements.append("ALTER TABLE test_results ADD COLUMN aptitude_score INTEGER DEFAULT 0")
    if "aptitude_total" not in table_columns.get("test_results", set()):
        alter_statements.append("ALTER TABLE test_results ADD COLUMN aptitude_total INTEGER DEFAULT 0")
    if "technical_score" not in table_columns.get("test_results", set()):
        alter_statements.append("ALTER TABLE test_results ADD COLUMN technical_score INTEGER DEFAULT 0")
    if "technical_total" not in table_columns.get("test_results", set()):
        alter_statements.append("ALTER TABLE test_results ADD COLUMN technical_total INTEGER DEFAULT 0")
    if "section" not in table_columns.get("questions", set()):
        alter_statements.append("ALTER TABLE questions ADD COLUMN section VARCHAR(50) DEFAULT 'Aptitude'")
    if "is_published" not in table_columns.get("mock_tests", set()):
        alter_statements.append("ALTER TABLE mock_tests ADD COLUMN is_published BOOLEAN DEFAULT 0")
    if "question_count" not in table_columns.get("mock_tests", set()):
        alter_statements.append("ALTER TABLE mock_tests ADD COLUMN question_count INTEGER DEFAULT 10")

    if alter_statements:
        for statement in alter_statements:
            db.session.execute(text(statement))
        db.session.commit()


def _extract_company_signals(company_data):
    company_name = (company_data.get("company_name") or "").strip().lower()
    normalized_company_name = _normalize_company_name(company_name)
    salary = _safe_float_range(company_data.get("salary_package"), 0.0)
    website = (company_data.get("website") or "").strip().lower()
    email = (company_data.get("contact_email") or "").strip().lower()
    contact_phone = (company_data.get("contact_phone") or "").strip()
    reg_no = (company_data.get("registration_number") or "").strip()
    gst_no = (company_data.get("gst_number") or "").strip().upper()
    description = (company_data.get("description") or "").strip().lower()
    history = _safe_int(company_data.get("previous_history")) or 0

    public_domains = (
        "gmail.com",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "proton.me",
        "mailinator.com",
        "tempmail.com",
        "guerrillamail.com",
        "yopmail.com",
    )
    website_present = bool(website)
    website_valid = website.startswith("http://") or website.startswith("https://")
    website_https = website.startswith("https://")
    domain_email = bool(email) and not any(email.endswith(f"@{d}") for d in public_domains)
    reg_present = bool(reg_no)
    gst_present = bool(gst_no)
    phone_present = bool(contact_phone)
    reg_format_valid = bool(re.fullmatch(r"[A-Za-z0-9/\-]{6,30}", reg_no)) and bool(
        re.search(r"[A-Za-z]", reg_no)
    ) and bool(re.search(r"\d", reg_no))
    gst_format_valid = bool(
        re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", gst_no)
    )

    website_domain = ""
    email_domain = ""
    if website_valid:
        parsed = urlparse(website)
        website_domain = (parsed.netloc or "").split(":")[0].lower()
    if website_domain.startswith("www."):
        website_domain = website_domain[4:]
    if "@" in email:
        email_domain = email.rsplit("@", 1)[1]

    domain_match = bool(website_domain and email_domain) and (
        email_domain == website_domain or email_domain.endswith("." + website_domain)
    )

    risk_keywords = (
        "quick hire",
        "instant job",
        "no interview",
        "registration fee",
        "earn money",
        "work from home guaranteed",
        "telegram",
        "whatsapp only",
        "pay and join",
        "fake",
        "dummy",
        "test company",
    )
    risk_text = " ".join([company_name, website, email, description])
    risk_keyword_hits = sum(1 for k in risk_keywords if k in risk_text)
    has_risk_keywords = risk_keyword_hits > 0
    is_trusted_safe_company = normalized_company_name in TRUSTED_SAFE_COMPANIES

    trust_pass_count = sum(
        int(v)
        for v in (
            website_present,
            website_valid,
            website_https,
            domain_email,
            domain_match,
            reg_present,
            reg_format_valid or (not reg_present),
            gst_present,
            gst_format_valid or (not gst_present),
            phone_present,
        )
    )

    feature_vector = [
        float(website_present),
        float(website_valid),
        float(website_https),
        float(domain_email),
        float(domain_match),
        float(reg_present),
        float(reg_format_valid if reg_present else 0.0),
        float(gst_present),
        float(gst_format_valid if gst_present else 0.0),
        float(phone_present),
        float(min(max(history, 0), 10)) / 10.0,
        float(min(max(salary, 0.0), 40.0)) / 40.0,
        float(risk_keyword_hits),
        float(trust_pass_count) / 10.0,
        float(is_trusted_safe_company),
    ]

    return {
        "company_name": company_name,
        "salary": salary,
        "website": website,
        "email": email,
        "contact_phone": contact_phone,
        "reg_no": reg_no,
        "gst_no": gst_no,
        "description": description,
        "history": history,
        "website_present": website_present,
        "website_valid": website_valid,
        "website_https": website_https,
        "domain_email": domain_email,
        "reg_present": reg_present,
        "gst_present": gst_present,
        "phone_present": phone_present,
        "reg_format_valid": reg_format_valid,
        "gst_format_valid": gst_format_valid,
        "domain_match": domain_match,
        "has_risk_keywords": has_risk_keywords,
        "risk_keyword_hits": risk_keyword_hits,
        "normalized_company_name": normalized_company_name,
        "is_trusted_safe_company": is_trusted_safe_company,
        "feature_vector": feature_vector,
    }


_FRAUD_MODEL_CACHE = {"model": None, "scaler": None, "version": None}


def _build_fraud_training_dataset():
    features = []
    labels = []

    # Synthetic safe profiles.
    safe_payloads = []
    for history in (2, 3, 4, 5, 6):
        for salary in (4, 6, 8, 10, 12):
            safe_payloads.append(
                {
                    "company_name": f"safe_company_{history}_{salary}",
                    "website": "https://www.examplecorp.com",
                    "contact_email": "hr@examplecorp.com",
                    "contact_phone": "+91-9876543210",
                    "registration_number": "U12345KA2018PLC123456",
                    "gst_number": "29ABCDE1234F1Z5",
                    "salary_package": str(salary),
                    "previous_history": history,
                    "description": "trusted enterprise recruitment",
                }
            )
    for payload in safe_payloads:
        features.append(_extract_company_signals(payload)["feature_vector"])
        labels.append(0)

    # Seed trusted-safe company patterns even with weak history fields.
    trusted_safe_payloads = [
        {
            "company_name": "NovaTech Solutions",
            "website": "https://www.novatech.com",
            "contact_email": "priya.menon@novatech.com",
            "contact_phone": "+91-9876543210",
            "registration_number": "10234",
            "gst_number": "27ABCDE1234F1Z5",
            "salary_package": "5",
            "previous_history": 0,
            "description": "IT services and consulting",
        }
    ]
    for payload in trusted_safe_payloads:
        features.append(_extract_company_signals(payload)["feature_vector"])
        labels.append(0)

    # Synthetic fake profiles.
    fake_payloads = [
        {
            "company_name": "quick hire instant job",
            "website": "",
            "contact_email": "jobs@gmail.com",
            "contact_phone": "",
            "registration_number": "",
            "gst_number": "",
            "salary_package": "35",
            "previous_history": 0,
            "description": "no interview pay and join whatsapp only",
        },
        {
            "company_name": "dummy test company",
            "website": "http://job-alerts.biz",
            "contact_email": "workdesk@yahoo.com",
            "contact_phone": "",
            "registration_number": "123",
            "gst_number": "ABC",
            "salary_package": "28",
            "previous_history": 0,
            "description": "registration fee required",
        },
        {
            "company_name": "agrobloom enterprises",
            "website": "https://www.agrobloom.org",
            "contact_email": "kiran@agrobloom.org",
            "contact_phone": "+91-9123456789",
            "registration_number": "123",
            "gst_number": "ABC",
            "salary_package": "5",
            "previous_history": 0,
            "description": "new hiring drive",
        },
        {
            "company_name": "greennova imports",
            "website": "https://www.greennova.in",
            "contact_email": "hr@greennova.in",
            "contact_phone": "+91-9988776655",
            "registration_number": "REG12",
            "gst_number": "22AAAAA0000A1Z",
            "salary_package": "6",
            "previous_history": 0,
            "description": "walkin drive",
        },
        {
            "company_name": "nextphase logistics",
            "website": "https://www.nextphase.co",
            "contact_email": "careers@nextphase.co",
            "contact_phone": "+91-9000011111",
            "registration_number": "XYZ",
            "gst_number": "INVALIDGST",
            "salary_package": "7",
            "previous_history": 0,
            "description": "entry level role",
        },
    ]
    for payload in fake_payloads:
        features.append(_extract_company_signals(payload)["feature_vector"])
        labels.append(1)

    # Add historical records.
    records = FraudDetectionRecord.query.all()
    for record in records:
        company = record.company
        if not company:
            continue
        features_used = record.features_used or {}
        payload = {
            "company_name": company.company_name,
            "website": company.website,
            "contact_email": company.contact_email,
            "contact_phone": company.contact_phone,
            "registration_number": company.registration_number,
            "gst_number": company.gst_number,
            "salary_package": features_used.get("salary_package"),
            "previous_history": features_used.get("previous_history", 0),
            "description": company.description,
        }

        status = (record.status or "").strip().lower()
        if record.is_fraud or status == "fake":
            label = 1
        elif status in ("safe", "verified", "overridden", "legitimate"):
            label = 0
        else:
            continue

        features.append(_extract_company_signals(payload)["feature_vector"])
        labels.append(label)

    if len(features) < 10 or len(set(labels)) < 2:
        raise Exception(
            "Model not loaded. Fraud detection requires trained ML model. "
            "Install scikit-learn with: pip install scikit-learn"
        )

    return features, labels


def _load_fraud_model():
    if (
        _FRAUD_MODEL_CACHE["model"] is not None
        and _FRAUD_MODEL_CACHE["scaler"] is not None
        and _FRAUD_MODEL_CACHE["version"] == FRAUD_MODEL_VERSION
    ):
        return _FRAUD_MODEL_CACHE["model"], _FRAUD_MODEL_CACHE["scaler"]

    if RandomForestClassifier is None or train_test_split is None or StandardScaler is None:
        raise Exception(
            "Model not loaded. Fraud detection requires trained ML model. "
            "Install scikit-learn with: pip install scikit-learn"
        )

    features, labels = _build_fraud_training_dataset()
    X_train, X_test, y_train, y_test = train_test_split(
        features,
        labels,
        test_size=0.2,
        random_state=42,
        stratify=labels,
    )
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    _ = scaler.transform(X_test)

    model = RandomForestClassifier(
        n_estimators=500,
        random_state=42,
        class_weight="balanced",
        min_samples_leaf=1,
    )
    model.fit(X_train_scaled, y_train)
    _ = model.predict_proba(X_train_scaled)

    _FRAUD_MODEL_CACHE["model"] = model
    _FRAUD_MODEL_CACHE["scaler"] = scaler
    _FRAUD_MODEL_CACHE["version"] = FRAUD_MODEL_VERSION
    return model, scaler


def _predict_fraud_probability(input_data):
    model, scaler = _load_fraud_model()
    signals = _extract_company_signals(input_data)
    scaled = scaler.transform([signals["feature_vector"]])
    probability = float(model.predict_proba(scaled)[0][1])
    # Keep AI-first behavior but calibrate trusted-safe seeds to avoid false fake labels.
    if (
        signals["is_trusted_safe_company"]
        and signals["website_valid"]
        and signals["domain_match"]
        and signals["gst_present"]
    ):
        probability = min(probability, 0.20)
    return probability, signals


def detect_fraud(input_data):
    probability, _ = _predict_fraud_probability(input_data)
    return "Fake" if probability >= 0.30 else "Safe"


def predict_authenticity(company_data, persist_log=True):
    probability, signals = _predict_fraud_probability(company_data)
    label = "Fake" if probability >= 0.30 else "Safe"

    # Keep rule checks for explainability in admin UI.
    reasons = []
    breakdown = []

    def add_check(label_name, passed, reason):
        breakdown.append({"check": label_name, "status": "PASS" if passed else "FAIL"})
        if not passed:
            reasons.append(reason)

    add_check("Website provided", signals["website_present"], "Missing Website")
    add_check("Website format", signals["website_valid"], "Invalid Website URL")
    add_check("HTTPS website", signals["website_https"], "Website not using HTTPS")
    add_check("Company email domain", signals["domain_email"], "Public Email Domain")
    add_check("Email/Website domain match", signals["domain_match"], "Email domain does not match website")
    add_check("Registration number", signals["reg_present"], "Missing Registration Number")
    add_check(
        "Registration format",
        (not signals["reg_present"]) or signals["reg_format_valid"],
        "Registration Number format looks invalid",
    )
    add_check("GST availability", signals["gst_present"], "Missing GST Number")
    add_check(
        "GST format",
        (not signals["gst_present"]) or signals["gst_format_valid"],
        "GST Number format looks invalid",
    )
    add_check("Contact phone", signals["phone_present"], "Missing Contact Phone")
    add_check("Placement history", signals["history"] >= 1, "No Previous Drive History")

    risk_score_pct = round(probability * 100.0, 2)
    result = {
        "classification": label,
        "risk_score_pct": risk_score_pct,
        "is_fraud": label == "Fake",
        "anomaly_score": round(probability - 0.5, 4),
        "reasons": "; ".join(dict.fromkeys(reasons)) if reasons else "Normal pattern",
        "scoring_breakdown": breakdown,
        "web_score": round(max(0.0, 100.0 - risk_score_pct), 2),
        "ml_score": round(max(0.0, 100.0 - risk_score_pct), 2),
        "rule_risk_pct": risk_score_pct,
        "ai_risk_pct": risk_score_pct,
        "model_backend": "random_forest",
        "model_version": FRAUD_MODEL_VERSION,
    }

    if persist_log:
        log_fraud_result({**company_data, **result})
    return result


# =========================
# HOME / LANDING ROUTES
# =========================
@app.route("/")
def welcome_page():
    return render_template("welcome.html")


@app.route("/studenthome")
def student_home():
    return render_template("studenthome.html")


# =========================
# STUDENT LOGIN
# =========================
@app.route("/student/login", methods=["GET", "POST"])
def student_login():
    if request.method == "POST":
        email = request.form.get("email").strip().lower()
        password = request.form.get("password")

        student = Student.query.filter_by(email=email).first()

        if student and check_password_hash(student.password, password):
            _log_login_event("student", email, True)
            session["student_id"] = student.st_id
            return redirect(url_for("student_dashboard"))

        _log_login_event("student", email, False)
        flash("Invalid credentials", "danger")

    return render_template("login.html", logged_out=request.args.get("logged_out") == "1")


@app.route("/login", methods=["GET", "POST"])
def login_page():
    return student_login()


@app.route("/student/forgot-password", methods=["GET", "POST"])
def student_forgot_password():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()

        if "@" not in email:
            flash("Enter a valid email address.", "danger")
            return render_template("forgot_password.html")

        student = Student.query.filter_by(email=email).first()
        if student:
            token = _generate_password_reset_token(student)
            reset_link = url_for("student_reset_password", token=token, _external=True)
            subject = "Reset Your Student Portal Password"
            html_body = render_template(
                "emails/student_password_reset.html",
                student=student,
                reset_link=reset_link,
                expires_minutes=30,
                config=app.config,
            )
            text_body = (
                f"Hello {student.name},\n\n"
                "We received a request to reset your student portal password.\n"
                f"Reset link: {reset_link}\n\n"
                "This link will expire in 30 minutes."
            )
            sent, error_msg = _send_html_email(student.email, subject, html_body, text_body)
            if not sent:
                app.logger.error(
                    "Password reset email send failed for %s: %s",
                    student.email,
                    error_msg or "unknown error",
                )
                flash(
                    "Unable to send reset email right now. Please try again later.",
                    "danger",
                )
                return render_template("forgot_password.html")

        flash(
            "If an account exists for that email, a password reset link has been sent.",
            "success",
        )
        return redirect(url_for("login_page"))

    return render_template("forgot_password.html")


@app.route("/student/reset-password/<token>", methods=["GET", "POST"])
def student_reset_password(token):
    student, token_status = _verify_password_reset_token(token)
    if not student:
        if token_status == "expired":
            flash("This reset link has expired. Please request a new one.", "danger")
        else:
            flash("This reset link is invalid. Please request a new one.", "danger")
        return redirect(url_for("student_forgot_password"))

    if request.method == "POST":
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        is_strong, password_message = _is_strong_password(password)
        if not is_strong:
            flash(password_message, "danger")
            return render_template("reset_password.html", token=token)

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("reset_password.html", token=token)

        student.password = generate_password_hash(password)
        db.session.commit()

        if session.get("student_id") == student.st_id:
            session.pop("student_id", None)

        flash("Password reset successful. Please login with your new password.", "success")
        return redirect(url_for("login_page"))

    return render_template("reset_password.html", token=token)


@app.route("/register", methods=["GET", "POST"])
def student_signup():
    if request.method == "POST":
        register_number = (request.form.get("register_number") or "").strip().upper()
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        raw_password = request.form.get("password") or ""

        if not re.fullmatch(r"CEC\d{2}[A-Z]{2}\d{3}", register_number):
            flash("Register number must be like CEC23CS027.", "danger")
            return render_template("register.html")

        if len(name) < 3:
            flash("Name must be at least 3 characters.", "danger")
            return render_template("register.html")

        if "@" not in email:
            flash("Enter a valid email address.", "danger")
            return render_template("register.html")

        is_strong, password_message = _is_strong_password(raw_password)
        if not is_strong:
            flash(password_message, "danger")
            return render_template("register.html")

        if Student.query.filter_by(email=email).first():
            flash("Email is already registered. Please login instead.", "danger")
            return render_template("register.html")

        if Student.query.filter_by(register_number=register_number).first():
            flash("Register number is already registered.", "danger")
            return render_template("register.html")

        new_student = Student(
            st_id=_next_student_id(),
            register_number=register_number,
            name=name,
            email=email,
            password=generate_password_hash(raw_password),
        )
        db.session.add(new_student)
        db.session.commit()

        flash("Registration successful. Please login.", "success")
        return redirect(url_for("login_page"))

    return render_template("register.html")


# =========================
# STUDENT DASHBOARD
# =========================
@app.route("/studentdash")
def student_dashboard():
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    student = Student.query.filter_by(st_id=session["student_id"]).first()
    if not student:
        flash("Student profile not found.", "danger")
        return redirect(url_for("student_login"))

    # Eligibility filtering is based only on student profile fields and company criteria.
    raw_placements = Placement.query.order_by(Placement.cmpname.asc(), Placement.placeid.desc()).all()
    placements = []
    seen_company_keys = set()
    for placement in raw_placements:
        company_key = (placement.cmpname or "").strip().lower() or f"placeid:{placement.placeid}"
        if company_key in seen_company_keys:
            continue
        seen_company_keys.add(company_key)
        placements.append(placement)
    eligibility_results = {placement.placeid: check_eligibility_details(student, placement) for placement in placements}
    eligible_drives = [
        drive for drive in placements if eligibility_results.get(drive.placeid, {}).get("eligible")
    ]

    # Modified: joinedload to avoid N+1 when accessing placement relation from applications.
    student_applications = (
        PlacementApplication.query.options(joinedload(PlacementApplication.placement))
        .filter_by(student_id=student.st_id)
        .all()
    )
    applied_company_keys = {
        ((app_item.placement.cmpname or "").strip().lower())
        for app_item in student_applications
        if app_item.placement
    }
    total_applied = sum(1 for app_item in student_applications if app_item.status == "Applied")
    total_selected = sum(1 for app_item in student_applications if app_item.status == "Selected")
    total_rejected = sum(1 for app_item in student_applications if app_item.status == "Rejected")
    total_upcoming = len(placements)

    notifications = (
        Notification.query.filter_by(student_id=session["student_id"])
        .order_by(Notification.date.desc())
        .limit(10)
        .all()
    )
    analytics = _build_student_analytics(session["student_id"])
    labels = analytics["labels"][-6:]
    scores = analytics["scores"][-6:]

    if not labels:
        labels = ["No Attempts Yet"]
        scores = [0]
    return render_template(
        "studentdash.html",
        student=student,
        labels=labels,
        scores=scores,
        notifications=notifications,
        placements=placements,
        upcoming_drives=placements,
        past_drives=[],
        eligible_drives=eligible_drives,
        eligibility_results=eligibility_results,
        eligibility_map=eligibility_results,
        student_applications=student_applications,
        applied_company_keys=applied_company_keys,
        total_applied=total_applied,
        total_selected=total_selected,
        total_rejected=total_rejected,
        total_upcoming=total_upcoming,
    )


def check_eligibility(student, placement):

    # CGPA check
    if placement.min_cgpa and student.cgpa < placement.min_cgpa:
        return False

    # Department check
    if placement.department and student.department != placement.department:
        return False

    # Year check
    if placement.allowed_year and student.year != placement.allowed_year:
        return False

    # Arrears check
    if placement.max_arrears is not None and student.number_of_arrears > placement.max_arrears:
        return False

    # Skills check (minimum 60% match of required skills)
    required_skills = set()
    required_skills.update(_split_skill_tokens(getattr(placement, "required_programming_languages", None)))
    required_skills.update(_split_skill_tokens(getattr(placement, "required_technical_skills", None)))
    required_skills.update(_split_skill_tokens(getattr(placement, "required_tools", None)))

    student_skills = set()
    student_skills.update(_split_skill_tokens(student.programming_languages))
    student_skills.update(_split_skill_tokens(student.technical_skills))
    student_skills.update(_split_skill_tokens(student.tools_technologies))

    if required_skills:
        matched_count = len(student_skills & required_skills)
        if (matched_count / len(required_skills)) * 100.0 < 60.0:
            return False

    return True


@app.route("/apply/<int:placement_id>", methods=["POST"])
def apply_placement(placement_id):
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    student = Student.query.filter_by(st_id=session["student_id"]).first()
    placement = Placement.query.filter_by(placeid=placement_id).first()
    if not student or not placement:
        flash("Placement drive not found.", "danger")
        return redirect(url_for("student_dashboard"))

    if not check_eligibility(student, placement):
        flash("You are not eligible for this drive.", "danger")
        return redirect(url_for("student_dashboard"))

    # Existing duplicate-check logic retained.
    existing_application = PlacementApplication.query.filter_by(
        student_id=session["student_id"], placement_id=placement_id
    ).first()
    existing_company_application = (
        PlacementApplication.query.join(Placement, Placement.placeid == PlacementApplication.placement_id)
        .filter(
            PlacementApplication.student_id == session["student_id"],
            Placement.cmpname == placement.cmpname,
        )
        .first()
    )
    if existing_application or existing_company_application:
        flash("Already Applied", "warning")
        return redirect(url_for("student_dashboard"))

    new_application = PlacementApplication(
        student_id=session["student_id"],
        placement_id=placement_id,
        status="Applied",
    )
    db.session.add(new_application)
    db.session.commit()
    flash("Application submitted successfully.", "success")
    return redirect(url_for("student_dashboard"))

@app.route("/resume_enhancer", methods=["GET", "POST"])
def resume_enhancer():
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    student = Student.query.filter_by(st_id=session["student_id"]).first()
    if not student:
        flash("Student profile not found.", "danger")
        return redirect(url_for("student_login"))

    enhancement = None
    resume_text = ""
    target_role = ""

    if request.method == "POST":
        resume_text = (request.form.get("resume_text") or "").strip()
        target_role = (request.form.get("target_role") or "").strip()
        uploaded_text = _extract_resume_text(request.files.get("resume_file"))
        if uploaded_text:
            resume_text = uploaded_text

        enhancement = _build_resume_enhancement(student, resume_text, target_role)
        flash("Resume enhancement generated.", "success")

    return render_template(
        "resume_enhancer.html",
        student=student,
        enhancement=enhancement,
        resume_text=resume_text,
        target_role=target_role,
    )


@app.route("/mock_tests")
def mock_tests():
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    _ensure_mock_test_schema()
    tests = MockTest.query.filter_by(is_published=True).order_by(MockTest.created_at.desc()).all()
    return render_template(
        "mock_tests.html",
        tests=tests,
    )


@app.route("/start_test/<int:test_id>")
def start_test(test_id):
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    _ensure_mock_test_schema()
    test = MockTest.query.get_or_404(test_id)
    if not test.is_published:
        flash("This mock test is not published yet.", "warning")
        return redirect(url_for("mock_tests"))

    total_questions = _resolve_test_question_count(test)
    _seed_question_bank_from_local_csv_if_needed(total_questions)
    grouped_questions, plan = _pick_questions_from_bank(total_questions)
    selected_source = "bank"

    if not grouped_questions:
        # Backward-compatible fallback: use existing per-test linked questions only if full set exists.
        grouped_questions, plan = _pick_questions_from_legacy(test_id, total_questions)
        if not grouped_questions:
            available_bank = (
                QuestionBank.query.filter(QuestionBank.correct_answer.in_(("A", "B", "C", "D"))).count()
            )
            available_legacy = Question.query.filter_by(test_id=test_id).filter(
                Question.correct_answer.in_(("A", "B", "C", "D"))
            ).count()
            available = max(available_bank, available_legacy)
            flash(
                f"Insufficient questions for this test. Required: {total_questions}, available: {available}.",
                "warning",
            )
            return redirect(url_for("mock_tests"))
        selected_source = "legacy"

    selected_ids = []
    for section_name in ("Aptitude", "Logical", "Technical", "Coding"):
        for q in grouped_questions.get(section_name, []):
            selected_ids.append(q.id)

    if len(selected_ids) < total_questions:
        flash(
            f"Insufficient questions available for this test. Required: {total_questions}, available: {len(selected_ids)}.",
            "warning",
        )
        return redirect(url_for("mock_tests"))

    session[f"mock_attempt_{test_id}"] = {
        "source": selected_source,
        "question_ids": selected_ids,
    }
    return render_template(
        "test_page.html",
        test=test,
        grouped_questions=grouped_questions,
        section_order=["Aptitude", "Logical", "Technical", "Coding"],
        expected_total=total_questions,
        section_plan=plan,
        attempt_question_ids=selected_ids,
    )


@app.route("/submit_test/<int:test_id>", methods=["POST"])
def submit_test(test_id):
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    _ensure_mock_test_schema()
    attempt_key = f"mock_attempt_{test_id}"
    attempt = session.pop(attempt_key, None) or {}
    source = (attempt.get("source") or "").strip().lower()
    selected_ids = [qid for qid in (attempt.get("question_ids") or []) if isinstance(qid, int)]

    # Primary source: form payload, so submit works even if session attempt expires.
    form_ids_raw = (request.form.get("attempt_question_ids") or "").strip()
    if form_ids_raw:
        parsed_form_ids = []
        for token in form_ids_raw.split(","):
            token = token.strip()
            if token.isdigit():
                parsed_form_ids.append(int(token))
        if parsed_form_ids:
            selected_ids = parsed_form_ids

    if not selected_ids:
        flash("Test session expired. Please restart the test.", "warning")
        return redirect(url_for("start_test", test_id=test_id))

    questions = []
    if source == "bank":
        fetched = QuestionBank.query.filter(QuestionBank.id.in_(selected_ids)).all()
    elif source == "legacy":
        fetched = Question.query.filter(Question.id.in_(selected_ids)).all()
    else:
        fetched = QuestionBank.query.filter(QuestionBank.id.in_(selected_ids)).all()
        source = "bank"
        if len(fetched) != len(set(selected_ids)):
            fetched = Question.query.filter(Question.id.in_(selected_ids)).all()
            source = "legacy"

    by_id = {q.id: q for q in fetched}
    for qid in selected_ids:
        q = by_id.get(qid)
        if q is not None:
            questions.append(q)

    if not questions or len(questions) != len(set(selected_ids)):
        flash("Unable to evaluate this attempt reliably. Please retake the test.", "warning")
        return redirect(url_for("start_test", test_id=test_id))

    score = 0
    section_totals = {key: 0 for key in ("Aptitude", "Logical", "Technical", "Coding")}
    section_scores = {key: 0 for key in ("Aptitude", "Logical", "Technical", "Coding")}

    for q in questions:
        student_answer = request.form.get(f"q{q.id}")
        section = _normalize_test_section(getattr(q, "section", None)) or "Technical"
        if section not in section_totals:
            section = "Technical"
        section_totals[section] += 1

        if student_answer == q.correct_answer:
            score += 1
            section_scores[section] += 1

    aptitude_total = section_totals["Aptitude"] + section_totals["Logical"]
    aptitude_score = section_scores["Aptitude"] + section_scores["Logical"]
    technical_total = section_totals["Technical"] + section_totals["Coding"]
    technical_score = section_scores["Technical"] + section_scores["Coding"]

    result = TestResult(
        student_id=session["student_id"],
        test_id=test_id,
        score=score,
        total_questions=len(questions),
        aptitude_score=aptitude_score,
        aptitude_total=aptitude_total,
        technical_score=technical_score,
        technical_total=technical_total,
    )

    db.session.add(result)
    db.session.commit()

    flash(f"Your Score: {score}/{len(questions)}", "success")

    return render_template(
        "test_result.html",
        score=score,
        total_questions=len(questions),
        aptitude_score=aptitude_score,
        aptitude_total=aptitude_total,
        technical_score=technical_score,
        technical_total=technical_total,
        logical_score=section_scores["Logical"],
        logical_total=section_totals["Logical"],
        coding_score=section_scores["Coding"],
        coding_total=section_totals["Coding"],
    )


@app.route("/test_history")
def test_history():
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    _ensure_mock_test_schema()
    rows = (
        db.session.query(TestResult, MockTest)
        .outerjoin(MockTest, MockTest.id == TestResult.test_id)
        .filter(TestResult.student_id == session["student_id"])
        .order_by(TestResult.submitted_at.desc(), TestResult.id.desc())
        .all()
    )
    return render_template("test_history.html", rows=rows)



@app.route("/analytics_report")
def analytics_report():
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    student = Student.query.filter_by(st_id=session["student_id"]).first()
    analytics = _build_student_analytics(session["student_id"])

    return render_template(
        "analytics_report.html",
        student=student,
        analytics=analytics,
    )


@app.route("/edit_student/<int:student_id>", methods=["GET", "POST"])
def edit_student(student_id):
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    if session["student_id"] != student_id:
        flash("You can only edit your own profile.", "danger")
        return redirect(url_for("student_dashboard"))

    student = Student.query.filter_by(st_id=student_id).first()
    if not student:
        flash("Student profile not found.", "danger")
        return redirect(url_for("student_dashboard"))

    if request.method == "POST":
        register_number = (request.form.get("register_number") or "").strip().upper()
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        technical_skills = (request.form.get("technical_skills") or "").strip()
        programming_languages = (request.form.get("programming_languages") or "").strip()
        tools_technologies = (request.form.get("tools_technologies") or "").strip()
        projects = (request.form.get("projects") or "").strip()
        internship_experience = (request.form.get("internship_experience") or "").strip() or None
        certifications = (request.form.get("certifications") or "").strip()
        resume_file = request.files.get("resume_pdf")

        if len(name) < 3:
            flash("Name must be at least 3 characters.", "danger")
            return render_template("edit_student.html", student=student)

        if not re.fullmatch(r"CEC\d{2}[A-Z]{2}\d{3}", register_number):
            flash("Register number must be like CEC23CS027.", "danger")
            return render_template("edit_student.html", student=student)

        if "@" not in email:
            flash("Enter a valid email address.", "danger")
            return render_template("edit_student.html", student=student)

        register_owner = Student.query.filter_by(register_number=register_number).first()
        if register_owner and register_owner.st_id != student.st_id:
            flash("That register number is already used by another account.", "danger")
            return render_template("edit_student.html", student=student)

        email_owner = Student.query.filter_by(email=email).first()
        if email_owner and email_owner.st_id != student.st_id:
            flash("That email is already used by another account.", "danger")
            return render_template("edit_student.html", student=student)

        year = _safe_int(request.form.get("year"))
        cgpa = _safe_float(request.form.get("cgpa"))
        tenth_percentage = _safe_float(request.form.get("tenth_percentage"))
        twelfth_percentage = _safe_float(request.form.get("twelfth_percentage"))
        number_of_arrears = _safe_int(request.form.get("number_of_arrears"))

        if request.form.get("year") and year is None:
            flash("Year must be a valid number.", "danger")
            return render_template("edit_student.html", student=student)

        if request.form.get("cgpa") and cgpa is None:
            flash("CGPA must be a valid number.", "danger")
            return render_template("edit_student.html", student=student)

        if not request.form.get("tenth_percentage"):
            flash("10th percentage is required.", "danger")
            return render_template("edit_student.html", student=student)

        if tenth_percentage is None:
            flash("10th percentage must be a valid number.", "danger")
            return render_template("edit_student.html", student=student)

        if not request.form.get("twelfth_percentage"):
            flash("12th percentage is required.", "danger")
            return render_template("edit_student.html", student=student)

        if twelfth_percentage is None:
            flash("12th percentage must be a valid number.", "danger")
            return render_template("edit_student.html", student=student)

        if request.form.get("number_of_arrears") and number_of_arrears is None:
            flash("Number of arrears must be a valid number.", "danger")
            return render_template("edit_student.html", student=student)

        if cgpa is not None and (cgpa < 0 or cgpa > 10):
            flash("CGPA must be between 0 and 10.", "danger")
            return render_template("edit_student.html", student=student)

        if not (0 <= tenth_percentage <= 100):
            flash("10th percentage must be between 0 and 100.", "danger")
            return render_template("edit_student.html", student=student)

        if not (0 <= twelfth_percentage <= 100):
            flash("12th percentage must be between 0 and 100.", "danger")
            return render_template("edit_student.html", student=student)

        if number_of_arrears is not None and number_of_arrears < 0:
            flash("Number of arrears cannot be negative.", "danger")
            return render_template("edit_student.html", student=student)

        if not technical_skills:
            flash("Technical skills are required.", "danger")
            return render_template("edit_student.html", student=student)

        if not programming_languages:
            flash("Programming languages are required.", "danger")
            return render_template("edit_student.html", student=student)

        if not tools_technologies:
            flash("Tools & technologies are required.", "danger")
            return render_template("edit_student.html", student=student)

        if not projects:
            flash("Projects field is required.", "danger")
            return render_template("edit_student.html", student=student)

        if not certifications:
            flash("Certifications field is required.", "danger")
            return render_template("edit_student.html", student=student)

        if resume_file and resume_file.filename and not _is_allowed_resume_file(resume_file.filename):
            flash("Resume must be a PDF file.", "danger")
            return render_template("edit_student.html", student=student)

        if (not student.resume_pdf_path) and (not resume_file or not resume_file.filename):
            flash("Resume PDF upload is required.", "danger")
            return render_template("edit_student.html", student=student)

        resume_rel_path = None
        if resume_file and resume_file.filename:
            original_name = secure_filename(resume_file.filename)
            extension = original_name.rsplit(".", 1)[1].lower()
            final_name = f"{student.st_id}_{int(datetime.utcnow().timestamp())}.{extension}"
            upload_dir = os.path.join(app.static_folder, "uploads", "resumes")
            os.makedirs(upload_dir, exist_ok=True)
            file_path = os.path.join(upload_dir, final_name)
            resume_file.save(file_path)
            resume_rel_path = f"uploads/resumes/{final_name}"

        current_password = request.form.get("current_password") or ""
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if current_password or new_password or confirm_password:
            if not current_password or not new_password or not confirm_password:
                flash("To change password, fill current, new, and confirm password.", "danger")
                return render_template("edit_student.html", student=student)

            if not check_password_hash(student.password, current_password):
                flash("Current password is incorrect.", "danger")
                return render_template("edit_student.html", student=student)

            if new_password != confirm_password:
                flash("New password and confirm password do not match.", "danger")
                return render_template("edit_student.html", student=student)

            is_strong, password_message = _is_strong_password(new_password)
            if not is_strong:
                flash(password_message, "danger")
                return render_template("edit_student.html", student=student)

        student.register_number = register_number
        student.name = name
        student.email = email
        student.phone = (request.form.get("phone") or "").strip() or None
        student.department = (request.form.get("department") or "").strip() or None
        student.year = year
        student.cgpa = cgpa
        student.tenth_percentage = tenth_percentage
        student.twelfth_percentage = twelfth_percentage
        student.number_of_arrears = number_of_arrears if number_of_arrears is not None else 0
        student.technical_skills = technical_skills
        student.programming_languages = programming_languages
        student.tools_technologies = tools_technologies
        student.projects = projects
        student.internship_experience = internship_experience
        student.certifications = certifications
        if resume_rel_path:
            student.resume_pdf_path = resume_rel_path
        if new_password:
            student.password = generate_password_hash(new_password)

        db.session.commit()
        flash("Details updated successfully!", "success")
        return redirect(url_for("student_dashboard"))

    return render_template("edit_student.html", student=student)


# =========================
# ADMIN LOGIN
# =========================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email").strip().lower()
        password = request.form.get("password")

        admin = Admin.query.filter_by(email=email).first()

        if admin and check_password_hash(admin.password, password):
            _log_login_event("admin", email, True)
            session["admin_id"] = admin.ad_id
            return redirect(url_for("admin_dashboard"))

        _log_login_event("admin", email, False)
        flash("Invalid admin credentials", "danger")

    return render_template("admin_login.html")


# =========================
# ADMIN DASHBOARD
# =========================
@app.route("/admin_dashboard")
def admin_dashboard():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    now = datetime.utcnow()
    seven_days_ago = now - timedelta(days=6)
    one_day_ago = now - timedelta(days=1)

    total_students = Student.query.count()
    total_placements = Placement.query.count()
    total_applications = PlacementApplication.query.count()
    total_fraud_checks = AIFraud.query.count() + FraudDetectionRecord.query.count()
    students = Student.query.all()
    placements = Placement.query.all()
    placement_applications = (
        PlacementApplication.query.order_by(PlacementApplication.app_id.desc()).limit(50).all()
    )

    # ---------------- User Activity (Daily) ----------------
    days = [seven_days_ago + timedelta(days=i) for i in range(7)]
    day_labels = [d.strftime("%d %b") for d in days]
    signup_map = {d.date(): 0 for d in days}
    login_map = {d.date(): 0 for d in days}

    recent_students = Student.query.filter(Student.created_at >= seven_days_ago).all()
    for st in recent_students:
        if st.created_at:
            key = st.created_at.date()
            if key in signup_map:
                signup_map[key] += 1

    recent_logins = LoginEvent.query.filter(
        LoginEvent.created_at >= seven_days_ago,
        LoginEvent.success.is_(True),
    ).all()
    for ev in recent_logins:
        key = ev.created_at.date()
        if key in login_map:
            login_map[key] += 1

    signup_series = [signup_map[d.date()] for d in days]
    login_series = [login_map[d.date()] for d in days]

    # ---------------- User Activity (Weekly) ----------------
    week_starts = []
    today = now.date()
    current_week_start = today - timedelta(days=today.weekday())
    for i in range(7, -1, -1):
        week_starts.append(current_week_start - timedelta(weeks=i))

    week_labels = [f"Wk {d.strftime('%d %b')}" for d in week_starts]
    weekly_signup_map = {d: 0 for d in week_starts}
    weekly_login_map = {d: 0 for d in week_starts}

    for st in Student.query.filter(Student.created_at >= datetime.combine(week_starts[0], datetime.min.time())).all():
        if st.created_at:
            d = st.created_at.date()
            w = d - timedelta(days=d.weekday())
            if w in weekly_signup_map:
                weekly_signup_map[w] += 1

    for ev in LoginEvent.query.filter(
        LoginEvent.created_at >= datetime.combine(week_starts[0], datetime.min.time()),
        LoginEvent.success.is_(True),
    ).all():
        d = ev.created_at.date()
        w = d - timedelta(days=d.weekday())
        if w in weekly_login_map:
            weekly_login_map[w] += 1

    weekly_signup_series = [weekly_signup_map[d] for d in week_starts]
    weekly_login_series = [weekly_login_map[d] for d in week_starts]

    active_users_24h = (
        db.session.query(LoginEvent.identifier)
        .filter(LoginEvent.created_at >= one_day_ago, LoginEvent.success.is_(True))
        .distinct()
        .count()
    )
    failed_logins_24h = LoginEvent.query.filter(
        LoginEvent.created_at >= one_day_ago,
        LoginEvent.success.is_(False),
    ).count()

    # ---------------- System Load ----------------
    avg_response_ms = round(sum(RECENT_RESPONSE_TIMES) / len(RECENT_RESPONSE_TIMES), 2) if RECENT_RESPONSE_TIMES else 0.0
    requests_last_5m = len(RECENT_REQUEST_TIMESTAMPS)
    uptime_hours = round((now - APP_START_TIME).total_seconds() / 3600.0, 2)
    uptime_display = f"{uptime_hours} hrs"

    # ---------------- Error Tracking ----------------
    errors_24h = SystemErrorLog.query.filter(SystemErrorLog.created_at >= one_day_ago).all()
    crash_count_24h = sum(1 for e in errors_24h if e.status_code >= 500)
    api_error_count_24h = sum(1 for e in errors_24h if "/api/" in (e.endpoint or ""))

    # ---------------- Data Insights ----------------
    total_companies = Company.query.count()
    latest_by_company = {}
    ordered_records = FraudDetectionRecord.query.order_by(
        FraudDetectionRecord.analysis_timestamp.desc()
    ).all()
    for record in ordered_records:
        if record.company_id not in latest_by_company:
            latest_by_company[record.company_id] = record

    latest_records = list(latest_by_company.values())

    # Re-score old records if they were produced by an older detector version.
    reanalysis_changed = False
    for record in latest_records:
        features_used = record.features_used or {}
        if features_used.get("model_version") == FRAUD_MODEL_VERSION:
            continue
        company = record.company
        if not company:
            continue

        payload = {
            "company_name": company.company_name,
            "website": company.website,
            "contact_email": company.contact_email,
            "contact_phone": company.contact_phone,
            "registration_number": company.registration_number,
            "gst_number": company.gst_number,
            "salary_package": features_used.get("salary_package"),
            "previous_history": features_used.get("previous_history", 0),
            "description": company.description,
        }
        refreshed = predict_authenticity(payload, persist_log=False)
        record.risk_score = refreshed["risk_score_pct"] / 100.0
        record.is_fraud = refreshed["is_fraud"]
        record.anomaly_score = refreshed["anomaly_score"]
        record.status = refreshed["classification"].capitalize()
        record.fraud_reasons = refreshed.get("reasons", "")
        merged_features = dict(features_used)
        merged_features.update(
            {
                "scoring_breakdown": refreshed.get("scoring_breakdown", []),
                "web_score": refreshed.get("web_score"),
                "ml_score": refreshed.get("ml_score"),
                "rule_risk_pct": refreshed.get("rule_risk_pct"),
                "ai_risk_pct": refreshed.get("ai_risk_pct"),
                "model_backend": refreshed.get("model_backend"),
                "model_version": refreshed.get("model_version"),
            }
        )
        record.features_used = merged_features
        company.status = "Active" if refreshed["classification"] == "Safe" else "Blocked"
        reanalysis_changed = True

    if reanalysis_changed:
        db.session.commit()
    flagged_companies = len(
        [record for record in latest_records if record.is_fraud or record.status in ("Fake", "Fraud")]
    )
    verified_companies = len(
        [
            record
            for record in latest_records
            if (not record.is_fraud) and record.status in ("Safe", "Verified", "Overridden", "Legitimate")
        ]
    )
    pending_companies = len([record for record in latest_records if record.status == "Pending"])

    fraud_rate = round((flagged_companies / total_companies * 100), 2) if total_companies else 0.0

    # ---------------- Alerts ----------------
    system_alerts = []
    if failed_logins_24h >= 10:
        system_alerts.append({"level": "warning", "message": f"High failed logins in last 24h: {failed_logins_24h}"})
    if crash_count_24h > 0:
        system_alerts.append({"level": "danger", "message": f"System crashes detected in last 24h: {crash_count_24h}"})
    if avg_response_ms >= 800:
        system_alerts.append({"level": "warning", "message": f"High average response time: {avg_response_ms} ms"})
    if fraud_rate >= 30:
        system_alerts.append({"level": "danger", "message": f"High flagged fraud ratio: {fraud_rate}% of reviewed companies"})
    if not system_alerts:
        system_alerts.append({"level": "success", "message": "System health looks stable. No high-risk alerts right now."})

    system_metrics = {
        "signups_7d_total": sum(signup_series),
        "logins_7d_total": sum(login_series),
        "active_users_24h": active_users_24h,
        "avg_response_ms": avg_response_ms,
        "requests_last_5m": requests_last_5m,
        "uptime_display": uptime_display,
        "failed_logins_24h": failed_logins_24h,
        "crash_count_24h": crash_count_24h,
        "api_error_count_24h": api_error_count_24h,
        "verified_companies": verified_companies,
        "flagged_companies": flagged_companies,
        "pending_companies": pending_companies,
        "total_companies": total_companies,
        "fraud_rate": fraud_rate,
    }

    chart_data = {
        "daily_activity_labels": day_labels,
        "daily_signup_series": signup_series,
        "daily_login_series": login_series,
        "weekly_activity_labels": week_labels,
        "weekly_signup_series": weekly_signup_series,
        "weekly_login_series": weekly_login_series,
        "error_labels": ["Failed Logins (24h)", "Crashes (24h)", "API Errors (24h)"],
        "error_series": [failed_logins_24h, crash_count_24h, api_error_count_24h],
        "data_labels": ["Verified", "Flagged", "Pending"],
        "data_series": [verified_companies, flagged_companies, pending_companies],
    }

    return render_template(
        "admindash.html",
        total_students=total_students,
        total_placements=total_placements,
        total_applications=total_applications,
        total_fraud_checks=total_fraud_checks,
        students=students,
        placements=placements,
        placement_applications=placement_applications,
        system_metrics=system_metrics,
        chart_data=chart_data,
        system_alerts=system_alerts,
    )


@app.route("/admin/system-analytics/export.csv")
def export_system_analytics_csv():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    now = datetime.utcnow()
    one_day_ago = now - timedelta(days=1)
    seven_days_ago = now - timedelta(days=6)

    signups_7d = Student.query.filter(Student.created_at >= seven_days_ago).count()
    logins_7d = LoginEvent.query.filter(
        LoginEvent.created_at >= seven_days_ago, LoginEvent.success.is_(True)
    ).count()
    active_users_24h = (
        db.session.query(LoginEvent.identifier)
        .filter(LoginEvent.created_at >= one_day_ago, LoginEvent.success.is_(True))
        .distinct()
        .count()
    )
    failed_logins_24h = LoginEvent.query.filter(
        LoginEvent.created_at >= one_day_ago, LoginEvent.success.is_(False)
    ).count()
    errors_24h = SystemErrorLog.query.filter(SystemErrorLog.created_at >= one_day_ago).all()
    crashes_24h = sum(1 for e in errors_24h if e.status_code >= 500)
    api_errors_24h = sum(1 for e in errors_24h if "/api/" in (e.endpoint or ""))
    avg_response_ms = round(sum(RECENT_RESPONSE_TIMES) / len(RECENT_RESPONSE_TIMES), 2) if RECENT_RESPONSE_TIMES else 0.0
    requests_last_5m = len(RECENT_REQUEST_TIMESTAMPS)
    uptime_hours = round((now - APP_START_TIME).total_seconds() / 3600.0, 2)

    verified = FraudDetectionRecord.query.filter(
        FraudDetectionRecord.status.in_(["Safe", "Verified", "Overridden", "Legitimate"])
    ).count()
    flagged = FraudDetectionRecord.query.filter_by(is_fraud=True).count()
    pending = FraudDetectionRecord.query.filter_by(status="Pending").count()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Metric", "Value"])
    writer.writerow(["Signups (7d)", signups_7d])
    writer.writerow(["Successful Logins (7d)", logins_7d])
    writer.writerow(["Active Users (24h)", active_users_24h])
    writer.writerow(["Failed Logins (24h)", failed_logins_24h])
    writer.writerow(["Crashes (24h)", crashes_24h])
    writer.writerow(["API Errors (24h)", api_errors_24h])
    writer.writerow(["Average Response (ms)", avg_response_ms])
    writer.writerow(["Requests (last 5m)", requests_last_5m])
    writer.writerow(["Uptime (hours)", uptime_hours])
    writer.writerow(["Fraud Verified", verified])
    writer.writerow(["Fraud Flagged", flagged])
    writer.writerow(["Fraud Pending", pending])
    writer.writerow(["Generated At (UTC)", now.isoformat()])

    csv_data = buffer.getvalue()
    buffer.close()

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=system_analytics_{now.strftime('%Y%m%d_%H%M%S')}.csv"},
    )


# =========================
# FRAUD DETECTION
# =========================
@app.route("/admin/fraud-detection", methods=["GET"])
def fraud_detection_dashboard():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    total_companies = Company.query.count()
    flagged_companies = FraudDetectionRecord.query.filter_by(is_fraud=True).count()
    verified_companies = FraudDetectionRecord.query.filter(
        FraudDetectionRecord.status.in_(["Safe", "Verified", "Overridden", "Legitimate"])
    ).count()
    pending_companies = FraudDetectionRecord.query.filter_by(status="Pending").count()

    recent_fraud_records = (
        FraudDetectionRecord.query.order_by(FraudDetectionRecord.analysis_timestamp.desc())
        .limit(20)
        .all()
    )

    # Keep latest fraud analysis per company for management sections.
    latest_by_company = {}
    ordered_records = FraudDetectionRecord.query.order_by(
        FraudDetectionRecord.analysis_timestamp.desc()
    ).all()
    for record in ordered_records:
        if record.company_id not in latest_by_company:
            latest_by_company[record.company_id] = record

    latest_records = list(latest_by_company.values())
    safe_company_records = [
        record
        for record in latest_records
        if (not record.is_fraud) and record.status in ("Safe", "Verified", "Overridden", "Legitimate")
    ]
    fake_company_records = [
        record
        for record in latest_records
        if record.is_fraud or record.status in ("Fake", "Fraud")
    ]

    flagged_companies = len(fake_company_records)
    verified_companies = len(safe_company_records)
    pending_companies = len([record for record in latest_records if record.status == "Pending"])

    fraud_stats = {
        "total_companies": total_companies,
        "flagged_companies": flagged_companies,
        "verified_companies": verified_companies,
        "pending_companies": pending_companies,
        "safe_companies": len(safe_company_records),
    }

    return render_template(
        "fraud_detection_dashboard.html",
        fraud_stats=fraud_stats,
        recent_fraud_records=recent_fraud_records,
        safe_company_records=safe_company_records,
        fake_company_records=fake_company_records,
    )


@app.route("/admin/fraud-detection/verify", methods=["POST"])
def verify_company():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    data = request.form
    company_name = (data.get("company_name") or "").strip()
    if not company_name:
        flash("Company name is required.", "danger")
        return redirect(url_for("fraud_detection_dashboard"))

    existing_company = Company.query.filter_by(company_name=company_name).first()
    if existing_company:
        flash(f"Company {company_name} already exists in the database.", "warning")
        return redirect(url_for("fraud_detection_dashboard"))

    try:
        fraud_result = predict_authenticity(
            {
                "company_name": company_name,
                "website": data.get("website"),
                "contact_email": data.get("contact_email"),
                "contact_phone": data.get("contact_phone"),
                "registration_number": data.get("registration_number"),
                "gst_number": data.get("gst_number"),
                "salary_package": data.get("salary_package"),
                "previous_history": data.get("previous_history", 0),
                "description": data.get("description"),
            }
        )
    except Exception as exc:
        flash(str(exc), "danger")
        return redirect(url_for("fraud_detection_dashboard"))

    company = Company(
        company_name=company_name,
        industry=(data.get("industry") or "").strip() or None,
        website=(data.get("website") or "").strip() or None,
        contact_person=(data.get("contact_person") or "").strip() or None,
        contact_email=(data.get("contact_email") or "").strip() or None,
        contact_phone=(data.get("contact_phone") or "").strip() or None,
        address=(data.get("address") or "").strip() or None,
        description=(data.get("description") or "").strip() or None,
        registration_number=(data.get("registration_number") or "").strip() or None,
        gst_number=(data.get("gst_number") or "").strip() or None,
        status="Active" if fraud_result["classification"] == "Safe" else "Blocked",
    )
    db.session.add(company)
    db.session.commit()

    fraud_record = FraudDetectionRecord(
        company_id=company.id,
        risk_score=fraud_result["risk_score_pct"] / 100.0,
        is_fraud=fraud_result["is_fraud"],
        anomaly_score=fraud_result["anomaly_score"],
        features_used={
            "registration_number": data.get("registration_number"),
            "salary_package": data.get("salary_package"),
            "previous_history": data.get("previous_history", 0),
            "contact_email": data.get("contact_email"),
            "website": data.get("website"),
            "scoring_breakdown": fraud_result.get("scoring_breakdown", []),
            "web_score": fraud_result.get("web_score"),
            "ml_score": fraud_result.get("ml_score"),
            "rule_risk_pct": fraud_result.get("rule_risk_pct"),
            "ai_risk_pct": fraud_result.get("ai_risk_pct"),
            "model_backend": fraud_result.get("model_backend"),
            "model_version": fraud_result.get("model_version"),
        },
        fraud_reasons=fraud_result.get("reasons", ""),
        status=fraud_result["classification"].capitalize(),
    )
    db.session.add(fraud_record)
    db.session.commit()

    if fraud_result["classification"] == "Fake":
        flash(
            f"Company {company.company_name} FLAGGED AS FRAUD! (Risk: {fraud_result['risk_score_pct']}%)",
            "danger",
        )
    else:
        flash(
            f"Company {company.company_name} verified as Safe. (Risk: {fraud_result['risk_score_pct']}%)",
            "success",
        )

    return redirect(url_for("fraud_detection_dashboard"))


@app.route("/admin/fraud-detection/override/<int:record_id>", methods=["POST"])
def override_fraud(record_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))
    flash("Manual override is disabled. Company status is AI-only and immutable from UI.", "warning")
    return redirect(url_for("fraud_detection_dashboard"))


@app.route("/admin/fraud-detection/mark-fake/<int:company_id>", methods=["POST"])
def mark_company_as_fake(company_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))
    flash("Manual status changes are disabled. AI classification cannot be changed from UI.", "warning")
    return redirect(url_for("fraud_detection_dashboard"))


@app.route("/admin/fraud-detection/delete-fake/<int:company_id>", methods=["POST"])
def delete_fake_company(company_id):
    # Backward-compatible endpoint: now deletes any checked company.
    return delete_checked_company(company_id)


@app.route("/admin/fraud-detection/delete-company/<int:company_id>", methods=["POST"])
def delete_checked_company(company_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    company = Company.query.get(company_id)
    if not company:
        flash("Company not found.", "danger")
        return redirect(url_for("fraud_detection_dashboard"))

    has_checked_record = FraudDetectionRecord.query.filter_by(company_id=company_id).first()
    if not has_checked_record:
        flash("Only analyzed companies can be deleted from this panel.", "warning")
        return redirect(url_for("fraud_detection_dashboard"))

    FraudDetectionRecord.query.filter_by(company_id=company_id).delete()
    db.session.delete(company)
    db.session.commit()

    flash(f"Deleted checked company {company.company_name}.", "success")
    return redirect(url_for("fraud_detection_dashboard"))


@app.route("/admin/fraud-detection/api/check", methods=["POST"])
def api_check_company():
    if "admin_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "No data provided"}), 400

    try:
        return jsonify(predict_authenticity(data))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/admin/fraud-detection/stats", methods=["GET"])
def get_fraud_stats():
    if "admin_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    total_companies = Company.query.count()
    latest_by_company = {}
    ordered_records = FraudDetectionRecord.query.order_by(
        FraudDetectionRecord.analysis_timestamp.desc()
    ).all()
    for record in ordered_records:
        if record.company_id not in latest_by_company:
            latest_by_company[record.company_id] = record

    latest_records = list(latest_by_company.values())
    flagged_companies = len(
        [record for record in latest_records if record.is_fraud or record.status in ("Fake", "Fraud")]
    )
    verified_companies = len(
        [
            record
            for record in latest_records
            if (not record.is_fraud) and record.status in ("Safe", "Verified", "Overridden", "Legitimate")
        ]
    )
    pending_companies = len([record for record in latest_records if record.status == "Pending"])
    safe_companies = verified_companies

    stats = {
        "total_companies": total_companies,
        "flagged_companies": flagged_companies,
        "verified_companies": verified_companies,
        "pending_companies": pending_companies,
        "safe_companies": safe_companies,
        "fraud_rate": round(
            (flagged_companies / total_companies * 100) if total_companies > 0 else 0, 2
        ),
    }
    return jsonify(stats)


@app.route("/admin/create_test", methods=["GET", "POST"])
def create_test():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))
    if not ENABLE_ADMIN_MOCK_TEST_MANAGEMENT:
        flash("Admin mock test provisioning is disabled.", "warning")
        return redirect(url_for("admin_dashboard"))

    _ensure_mock_test_schema()
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        duration = _safe_int(request.form.get("duration"))
        question_count = _safe_int(request.form.get("question_count"))

        if not title:
            flash("Test title is required.", "danger")
            return redirect(url_for("create_test"))

        if duration is None or duration <= 0:
            flash("Duration must be a positive number of minutes.", "danger")
            return redirect(url_for("create_test"))
        if question_count is None:
            question_count = 10
        if question_count not in {10, 30}:
            flash("Question count must be 10 or 30.", "danger")
            return redirect(url_for("create_test"))

        new_test = MockTest(
            title=title,
            description=description,
            duration=duration,
            question_count=question_count,
        )
        db.session.add(new_test)
        db.session.commit()
        flash("Mock test created successfully.", "success")
        return redirect(url_for("add_question", test_id=new_test.id))

    tests = MockTest.query.order_by(MockTest.created_at.desc()).all()
    return render_template("create_test.html", tests=tests)


@app.route("/admin/question_bank/upload", methods=["GET", "POST"])
def upload_question_bank():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))
    if not ENABLE_ADMIN_MOCK_TEST_MANAGEMENT:
        flash("Admin mock test provisioning is disabled.", "warning")
        return redirect(url_for("admin_dashboard"))

    _ensure_mock_test_schema()
    import_errors = []
    imported_count = 0
    skipped_count = 0

    if request.method == "POST":
        csv_file = request.files.get("csv_file")
        if not csv_file or not (csv_file.filename or "").strip():
            flash("Please choose a CSV file to upload.", "danger")
            return redirect(url_for("upload_question_bank"))

        try:
            raw_text = csv_file.stream.read().decode("utf-8-sig")
        except Exception:
            flash("Unable to read the uploaded file. Use UTF-8 CSV.", "danger")
            return redirect(url_for("upload_question_bank"))

        reader = csv.DictReader(io.StringIO(raw_text))
        required_columns = {
            "section",
            "question_text",
            "option_a",
            "option_b",
            "option_c",
            "option_d",
            "correct_answer",
        }
        file_columns = set(reader.fieldnames or [])
        missing_columns = sorted(required_columns - file_columns)
        if missing_columns:
            flash(f"Missing required CSV columns: {', '.join(missing_columns)}", "danger")
            return redirect(url_for("upload_question_bank"))

        allowed_sections = {"Aptitude", "Logical", "Technical", "Coding"}
        to_insert = []
        for row_no, row in enumerate(reader, start=2):
            section = _normalize_test_section(row.get("section"))
            question_text = (row.get("question_text") or "").strip()
            option_a = (row.get("option_a") or "").strip()
            option_b = (row.get("option_b") or "").strip()
            option_c = (row.get("option_c") or "").strip()
            option_d = (row.get("option_d") or "").strip()
            correct_answer = _coerce_correct_answer_letter(
                row.get("correct_answer"), option_a, option_b, option_c, option_d
            )

            if section not in allowed_sections:
                skipped_count += 1
                import_errors.append(f"Row {row_no}: Invalid section '{row.get('section')}'.")
                continue
            if not question_text or not option_a or not option_b or not option_c or not option_d:
                skipped_count += 1
                import_errors.append(f"Row {row_no}: Question/options cannot be empty.")
                continue
            if correct_answer not in {"A", "B", "C", "D"}:
                skipped_count += 1
                import_errors.append(
                    f"Row {row_no}: correct_answer must be A/B/C/D or match one of the option values."
                )
                continue

            to_insert.append(
                QuestionBank(
                    section=section,
                    question_text=question_text,
                    option_a=option_a,
                    option_b=option_b,
                    option_c=option_c,
                    option_d=option_d,
                    correct_answer=correct_answer,
                )
            )

        if to_insert:
            db.session.bulk_save_objects(to_insert)
            db.session.commit()
            imported_count = len(to_insert)

        if imported_count:
            flash(
                f"Imported {imported_count} question(s). Skipped {skipped_count} row(s).",
                "success" if skipped_count == 0 else "warning",
            )
        else:
            flash("No valid rows found in CSV. Nothing imported.", "warning")

    section_counts_raw = (
        db.session.query(QuestionBank.section, func.count(QuestionBank.id))
        .group_by(QuestionBank.section)
        .all()
    )
    counts = {key: 0 for key in ("Aptitude", "Logical", "Technical", "Coding")}
    for section, count in section_counts_raw:
        normalized = _normalize_test_section(section)
        if normalized:
            counts[normalized] = count

    return render_template(
        "question_bank_upload.html",
        counts=counts,
        import_errors=import_errors[:50],
    )


@app.route("/admin/publish_test/<int:test_id>", methods=["POST"])
def publish_test(test_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))
    if not ENABLE_ADMIN_MOCK_TEST_MANAGEMENT:
        flash("Admin mock test provisioning is disabled.", "warning")
        return redirect(url_for("admin_dashboard"))

    _ensure_mock_test_schema()
    test = MockTest.query.get_or_404(test_id)
    question_count = Question.query.filter_by(test_id=test.id).count()
    if question_count == 0:
        flash("Add at least one question before publishing.", "warning")
        return redirect(url_for("create_test"))

    test.is_published = True
    db.session.commit()
    flash("Mock test published successfully.", "success")
    return redirect(url_for("create_test"))


@app.route("/admin/reset_mock_tests", methods=["POST"])
def reset_mock_tests():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))
    if not ENABLE_ADMIN_MOCK_TEST_MANAGEMENT:
        flash("Admin mock test provisioning is disabled.", "warning")
        return redirect(url_for("admin_dashboard"))

    _ensure_mock_test_schema()
    try:
        deleted_results = TestResult.query.delete(synchronize_session=False)
        deleted_questions = Question.query.delete(synchronize_session=False)
        deleted_tests = MockTest.query.delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to reset mock tests data")
        flash("Unable to reset mock tests right now. Please try again.", "danger")
        return redirect(url_for("create_test"))

    flash(
        f"Mock tests reset complete: {deleted_tests} test(s), {deleted_questions} question(s), {deleted_results} submission(s) removed.",
        "success",
    )
    return redirect(url_for("create_test"))


@app.route("/admin/add_question/<int:test_id>", methods=["GET", "POST"])
def add_question(test_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))
    if not ENABLE_ADMIN_MOCK_TEST_MANAGEMENT:
        flash("Admin mock test provisioning is disabled.", "warning")
        return redirect(url_for("admin_dashboard"))

    _ensure_mock_test_schema()
    test = MockTest.query.get_or_404(test_id)

    if request.method == "POST":
        question_text = (request.form.get("question_text") or "").strip()
        option_a = (request.form.get("option_a") or "").strip()
        option_b = (request.form.get("option_b") or "").strip()
        option_c = (request.form.get("option_c") or "").strip()
        option_d = (request.form.get("option_d") or "").strip()
        section = (request.form.get("section") or "Aptitude").strip().title()
        correct_answer = (request.form.get("correct_answer") or "").strip().upper()

        if not question_text:
            flash("Question text is required.", "danger")
            return redirect(url_for("add_question", test_id=test_id))

        if not option_a or not option_b or not option_c or not option_d:
            flash("All options (A, B, C, D) are required.", "danger")
            return redirect(url_for("add_question", test_id=test_id))

        if correct_answer not in {"A", "B", "C", "D"}:
            flash("Correct answer must be one of A, B, C, or D.", "danger")
            return redirect(url_for("add_question", test_id=test_id))
        if section not in {"Aptitude", "Technical"}:
            flash("Section must be Aptitude or Technical.", "danger")
            return redirect(url_for("add_question", test_id=test_id))

        question = Question(
            test_id=test.id,
            section=section,
            question_text=question_text,
            option_a=option_a,
            option_b=option_b,
            option_c=option_c,
            option_d=option_d,
            correct_answer=correct_answer,
        )
        db.session.add(question)
        db.session.commit()
        flash("Question added successfully.", "success")
        return redirect(url_for("add_question", test_id=test_id))

    questions = Question.query.filter_by(test_id=test.id).order_by(Question.id.asc()).all()
    return render_template("add_question.html", test=test, questions=questions)


@app.route("/admin/test_results", methods=["GET"])
def view_test_results():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    _ensure_mock_test_schema()
    results = (
        db.session.query(TestResult, Student, MockTest)
        .outerjoin(Student, Student.st_id == TestResult.student_id)
        .outerjoin(MockTest, MockTest.id == TestResult.test_id)
        .order_by(TestResult.submitted_at.desc())
        .all()
    )
    return render_template("admin_test_results.html", results=results)


@app.route("/add_placement", methods=["POST"])
def add_placement():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    package = request.form.get("package")
    try:
        package_value = float(package) if package else None
    except ValueError:
        flash("Package must be a valid number.", "danger")
        return redirect(url_for("admin_dashboard"))

    drive_date = None
    drive_date_raw = (request.form.get("date") or "").strip()
    if drive_date_raw:
        try:
            drive_date = datetime.strptime(drive_date_raw, "%Y-%m-%d").date()
        except ValueError:
            flash("Date must be in YYYY-MM-DD format.", "danger")
            return redirect(url_for("admin_dashboard"))

    new_placement = Placement(
        cmpname=(request.form.get("cmpname") or "").strip(),
        jobreq=(request.form.get("jobreq") or "").strip(),
        package=package_value,
        eligicri=(request.form.get("eligicri") or "").strip(),
        date=drive_date,
        venue=(request.form.get("venue") or "").strip() or None,
        min_cgpa=_safe_float(request.form.get("min_cgpa")),
        department=(request.form.get("department") or "").strip() or None,
        allowed_year=_safe_int(request.form.get("allowed_year")),
        max_arrears=_safe_int(request.form.get("max_arrears")),
        required_programming_languages=(request.form.get("required_programming_languages") or "").strip() or None,
        required_technical_skills=(request.form.get("required_technical_skills") or "").strip() or None,
        required_tools=(request.form.get("required_tools") or "").strip() or None,
        admin_id=session["admin_id"],
    )
    db.session.add(new_placement)
    db.session.commit()

    students = Student.query.all()
    for student in students:
        msg = (
            f"New placement drive scheduled: {new_placement.cmpname} "
            f"({new_placement.jobreq or 'Role not specified'})."
        )
        db.session.add(
            Notification(
                date=datetime.utcnow(),
                msgtext=msg,
                student_id=student.st_id,
            )
        )
    db.session.commit()

    email_success_count = 0
    email_failure_count = 0
    first_email_error = ""
    for student in students:
        sent, error_msg = _send_placement_drive_email(student, new_placement)
        if sent:
            email_success_count += 1
        else:
            email_failure_count += 1
            if not first_email_error:
                first_email_error = error_msg or "Unknown SMTP error."

    if email_success_count and not email_failure_count:
        flash(
            f"Placement drive added and emailed to {email_success_count} student(s).",
            "success",
        )
    elif email_success_count and email_failure_count:
        flash(
            f"Placement drive added. Emails sent: {email_success_count}, failed: {email_failure_count}.",
            "warning",
        )
    else:
        flash(
            f"Placement drive added. Email sending failed for all students. First error: {first_email_error}",
            "warning",
        )
    return redirect(url_for("admin_dashboard"))


@app.route("/delete_placement/<int:placement_id>", methods=["POST"])
def delete_placement(placement_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    placement = Placement.query.get(placement_id)
    if not placement:
        flash("Placement drive not found.", "danger")
        return redirect(url_for("admin_dashboard"))

    # Remove dependent rows first to avoid FK conflicts.
    PlacementApplication.query.filter_by(placement_id=placement.placeid).delete()
    AIFraud.query.filter_by(placement_id=placement.placeid).delete()

    db.session.delete(placement)
    db.session.commit()
    flash("Placement drive deleted successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/notify-placed-students", methods=["POST"])
def notify_placed_students():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    placement_id = _safe_int(request.form.get("placement_id"))
    student_ids_raw = request.form.getlist("student_ids")
    status = (request.form.get("status") or "Selected").strip().title()
    custom_message = (request.form.get("custom_message") or "").strip()

    if placement_id is None:
        flash("Please select a placement drive.", "danger")
        return redirect(url_for("admin_dashboard"))

    placement = Placement.query.get(placement_id)
    if not placement:
        flash("Selected placement drive not found.", "danger")
        return redirect(url_for("admin_dashboard"))

    student_ids = []
    for raw_id in student_ids_raw:
        parsed = _safe_int(raw_id)
        if parsed is not None:
            student_ids.append(parsed)

    if not student_ids:
        flash("Please select at least one student.", "danger")
        return redirect(url_for("admin_dashboard"))

    selected_students = Student.query.filter(Student.st_id.in_(student_ids)).all()
    if not selected_students:
        flash("No valid students found for notification.", "danger")
        return redirect(url_for("admin_dashboard"))

    skipped_students = 0
    for student in selected_students:
        application = PlacementApplication.query.filter_by(
            student_id=student.st_id,
            placement_id=placement.placeid,
        ).first()

        if application:
            application.status = status
        else:
            # Do not create implicit applications from admin actions.
            skipped_students += 1
            continue

        in_app_message = (
            f"Your application status for {placement.cmpname} has been updated to {status}."
        )
        if custom_message:
            in_app_message = f"{in_app_message} {custom_message}"

        db.session.add(
            Notification(
                date=datetime.utcnow(),
                msgtext=in_app_message,
                student_id=student.st_id,
            )
        )

    db.session.commit()

    processed_students = len(selected_students) - skipped_students

    if skipped_students:
        flash(
            f"Skipped {skipped_students} student(s) who have not applied to this drive.",
            "warning",
        )

    if processed_students == 0:
        flash("No application statuses were updated.", "warning")
        return redirect(url_for("admin_dashboard"))

    email_success_count = 0
    email_failure_count = 0
    first_email_error = ""
    for student in selected_students:
        sent, error_msg = _send_placement_result_email(student, placement, status, custom_message)
        if sent:
            email_success_count += 1
        else:
            email_failure_count += 1
            if not first_email_error:
                first_email_error = error_msg or "Unknown SMTP error."

    if email_success_count and not email_failure_count:
        flash(
            f"Updated status and sent email notifications to {email_success_count} student(s).",
            "success",
        )
    elif email_success_count and email_failure_count:
        flash(
            f"Status updated for {processed_students} student(s). Emails sent: {email_success_count}, failed: {email_failure_count}. First error: {first_email_error}",
            "warning",
        )
    else:
        flash(
            f"Status updated for {processed_students} student(s), but no emails were sent. First error: {first_email_error}",
            "warning",
        )

    return redirect(url_for("admin_dashboard"))


@app.route("/delete_student/<int:student_id>", methods=["POST"])
def delete_student(student_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    student = Student.query.filter_by(st_id=student_id).first()
    if student:
        db.session.delete(student)
        db.session.commit()
        flash("Student deleted successfully.", "success")
    else:
        flash("Student not found.", "danger")

    return redirect(url_for("admin_dashboard"))


# =========================
# LOGOUT
# =========================
@app.route("/student/logout")
def student_logout():
    session.clear()
    response = make_response(redirect(url_for("student_login", logged_out=1)))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
@app.route("/admin/logout")
def admin_logout():
    session.clear()
    response = make_response(redirect(url_for("admin_login")))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
