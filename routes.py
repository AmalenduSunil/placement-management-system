import random
import re
import os
import json
import csv
import io
from datetime import datetime, timedelta
from collections import defaultdict

from flask import Response, flash, jsonify, make_response, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app import app, db, APP_START_TIME, RECENT_REQUEST_TIMESTAMPS, RECENT_RESPONSE_TIMES
from model import (
    AIFraud,
    Admin,
    Placement,
    PlacementApplication,
    Student,
    MockTest,
    Question,
    StudentTest,
    StudentAnswer,
    Company,
    FraudDetectionRecord,
    LoginEvent,
    SystemErrorLog,
)


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


def _build_ai_question_bank(topic, difficulty):
    topic_key = (topic or "aptitude").strip().lower()
    diff_key = (difficulty or "medium").strip().lower()

    mcq_bank = {
        "aptitude": [
            {
                "question_text": "If 12 workers complete a task in 15 days, how many days will 20 workers take (same efficiency)?",
                "options": ["9", "10", "11", "12"],
                "correct": "A",
            },
            {
                "question_text": "A train covers 240 km in 3 hours. What is its speed?",
                "options": ["60 km/h", "70 km/h", "80 km/h", "90 km/h"],
                "correct": "C",
            },
            {
                "question_text": "What is 25% of 480?",
                "options": ["100", "110", "120", "130"],
                "correct": "C",
            },
            {
                "question_text": "Find the next number: 2, 6, 12, 20, 30, ?",
                "options": ["40", "41", "42", "44"],
                "correct": "C",
            },
        ],
        "python": [
            {
                "question_text": "Which data type is immutable in Python?",
                "options": ["List", "Dictionary", "Set", "Tuple"],
                "correct": "D",
            },
            {
                "question_text": "What is the output type of len('CEC')?",
                "options": ["float", "int", "str", "bool"],
                "correct": "B",
            },
            {
                "question_text": "Which keyword is used to define a function in Python?",
                "options": ["func", "define", "def", "lambda"],
                "correct": "C",
            },
            {
                "question_text": "Which method adds an element to a list?",
                "options": ["add()", "append()", "insert_item()", "push()"],
                "correct": "B",
            },
        ],
        "sql": [
            {
                "question_text": "Which SQL statement is used to fetch data?",
                "options": ["GET", "SELECT", "FETCH ALL", "READ"],
                "correct": "B",
            },
            {
                "question_text": "Which clause is used to filter rows?",
                "options": ["GROUP BY", "ORDER BY", "WHERE", "LIMIT"],
                "correct": "C",
            },
            {
                "question_text": "Which keyword removes duplicate values?",
                "options": ["UNIQUE", "DISTINCT", "DIFFERENT", "FILTER"],
                "correct": "B",
            },
            {
                "question_text": "Which join returns matching rows from both tables?",
                "options": ["LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "FULL JOIN"],
                "correct": "C",
            },
        ],
    }

    coding_bank = {
        "aptitude": [
            {
                "question_text": "Write logic to print numbers from 1 to N in a single line separated by space.",
                "expected_output": "For input N=5, output should be: 1 2 3 4 5",
            },
            {
                "question_text": "Write a program to check whether a number is even or odd.",
                "expected_output": "For input 7, output should be: Odd",
            },
        ],
        "python": [
            {
                "question_text": "Write a function to reverse a string without using slicing.",
                "expected_output": "For input 'placement', output should be 'tnemecalp'",
            },
            {
                "question_text": "Write a function to return the second largest number in a list.",
                "expected_output": "For input [4, 9, 1, 7], output should be 7",
            },
            {
                "question_text": "Write code to count vowels in a string.",
                "expected_output": "For input 'college', output should be 3",
            },
        ],
        "sql": [
            {
                "question_text": "Write an SQL query to find the top 3 highest salaries from an Employee table.",
                "expected_output": "Should return 3 rows with highest salary values in descending order.",
            },
            {
                "question_text": "Write an SQL query to count students department-wise.",
                "expected_output": "Should return department and corresponding count.",
            },
        ],
    }

    base_mcq = mcq_bank.get(topic_key, mcq_bank["aptitude"])
    base_coding = coding_bank.get(topic_key, coding_bank["python"])

    if diff_key == "easy":
        return base_mcq[: max(2, len(base_mcq) - 1)], base_coding[:1]
    if diff_key == "hard":
        return base_mcq, base_coding
    return base_mcq, base_coding[: max(1, len(base_coding) - 1)]


def _generate_ai_questions(topic, difficulty, total_questions):
    mcq_pool, coding_pool = _build_ai_question_bank(topic, difficulty)

    mcq_count = max(1, int(total_questions * 0.7))
    coding_count = max(1, total_questions - mcq_count)

    generated = []
    for _ in range(mcq_count):
        q = random.choice(mcq_pool)
        generated.append(
            {
                "question_type": "MCQ",
                "question_text": q["question_text"],
                "option_a": q["options"][0],
                "option_b": q["options"][1],
                "option_c": q["options"][2],
                "option_d": q["options"][3],
                "correct_answer": q["correct"],
                "expected_output": None,
            }
        )

    for _ in range(coding_count):
        q = random.choice(coding_pool)
        generated.append(
            {
                "question_type": "CODING",
                "question_text": q["question_text"],
                "option_a": None,
                "option_b": None,
                "option_c": None,
                "option_d": None,
                "correct_answer": None,
                "expected_output": q["expected_output"],
            }
        )

    random.shuffle(generated)
    return generated


def _safe_float_range(value, default=0.0):
    val = _safe_float(value)
    return default if val is None else val


def _infer_topic_from_test(test_obj):
    stored_topic = (test_obj.topic or "").strip()
    if stored_topic and stored_topic.lower() != "general":
        return stored_topic

    text = f"{test_obj.title or ''} {test_obj.description or ''}".lower()
    if "aptitude" in text or "quant" in text or "reasoning" in text:
        return "Aptitude"
    if "python" in text:
        return "Python"
    if "sql" in text or "database" in text:
        return "SQL"
    if "coding" in text or "program" in text:
        return "Coding"
    return "General"


def _build_student_analytics(student_id):
    attempts = (
        StudentTest.query.filter_by(student_id=student_id)
        .order_by(StudentTest.submitted_at.asc())
        .all()
    )

    if not attempts:
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
            "suggestions": [
                "Start by taking at least 2 mock tests to unlock personalized insights."
            ],
        }

    labels = []
    scores = []
    topic_stats = defaultdict(lambda: {"correct": 0, "total": 0, "tests": 0})
    overall_correct = 0
    overall_total = 0
    pace_values = []
    coding_total = 0
    coding_answered = 0

    for idx, attempt in enumerate(attempts, start=1):
        test = MockTest.query.get(attempt.test_id)
        if not test:
            continue

        questions = Question.query.filter_by(test_id=test.test_id).all()
        answers = StudentAnswer.query.filter_by(attempt_id=attempt.attempt_id).all()
        answer_by_question = {a.question_id: a for a in answers}

        mcq_total = 0
        mcq_correct = 0
        test_topic = _infer_topic_from_test(test)

        for q in questions:
            ans = answer_by_question.get(q.question_id)
            if q.question_type == "MCQ":
                mcq_total += 1
                if ans and ans.selected_option == q.correct_answer:
                    mcq_correct += 1
            elif q.question_type == "CODING":
                coding_total += 1
                if ans and (ans.coding_answer or "").strip():
                    coding_answered += 1

        overall_correct += mcq_correct
        overall_total += mcq_total
        topic_stats[test_topic]["correct"] += mcq_correct
        topic_stats[test_topic]["total"] += mcq_total
        topic_stats[test_topic]["tests"] += 1

        effective_minutes = None
        if attempt.started_at and attempt.submitted_at:
            delta_sec = max(1, int((attempt.submitted_at - attempt.started_at).total_seconds()))
            effective_minutes = delta_sec / 60.0

        if len(questions) > 0:
            if effective_minutes and effective_minutes > 0:
                pace_values.append(len(questions) / effective_minutes)
            elif test.duration:
                pace_values.append(len(questions) / max(test.duration, 1))

        attempt_score_pct = round((mcq_correct / mcq_total) * 100, 1) if mcq_total else 0
        labels.append(f"{test.title[:14]} #{idx}")
        scores.append(attempt_score_pct)

    overall_accuracy = round((overall_correct / overall_total) * 100, 1) if overall_total else 0.0
    avg_qpm = round(sum(pace_values) / len(pace_values), 2) if pace_values else 0.0
    coding_completion = round((coding_answered / coding_total) * 100, 1) if coding_total else 0.0

    topic_labels = []
    topic_accuracy = []
    topic_rank = []
    for topic, stats in topic_stats.items():
        if stats["total"] <= 0:
            continue
        acc = round((stats["correct"] / stats["total"]) * 100, 1)
        topic_labels.append(topic)
        topic_accuracy.append(acc)
        topic_rank.append((topic, acc, stats["total"]))

    topic_rank.sort(key=lambda x: x[1], reverse=True)
    strong_topics = [t[0] for t in topic_rank[:2]]
    weak_topics = [t[0] for t in sorted(topic_rank, key=lambda x: x[1])[:2]]

    trend_delta = 0.0
    if len(scores) >= 2:
        window = min(3, len(scores) // 2 if len(scores) > 3 else 1)
        early_avg = sum(scores[:window]) / window
        late_avg = sum(scores[-window:]) / window
        trend_delta = round(late_avg - early_avg, 1)

    suggestions = []
    weak_set = set(weak_topics)

    if overall_accuracy < 55:
        suggestions.append("Build fundamentals first: revise concepts and do one timed test daily.")
    elif overall_accuracy < 75:
        suggestions.append("You are improving; target 80%+ by reviewing every wrong MCQ after each test.")
    else:
        suggestions.append("Strong overall performance. Focus on consistency and interview-level questions.")

    if "Aptitude" in weak_set:
        suggestions.append("Focus more on aptitude: practice quant and reasoning sets under time limits.")
    if "Python" in weak_set or "SQL" in weak_set or "Coding" in weak_set:
        suggestions.append("Practice coding speed: solve 2 medium problems daily with a timer.")

    if coding_total > 0 and coding_completion < 70:
        suggestions.append("Complete coding answers fully; partial or blank solutions reduce placement readiness.")

    if avg_qpm > 0.9:
        suggestions.append("Time pressure is high. Improve time management with sectional timed drills.")
    elif avg_qpm < 0.4:
        suggestions.append("You can increase pace gradually; aim for faster first-pass question solving.")

    if trend_delta < -5:
        suggestions.append("Recent trend dropped. Revisit weak topics before taking the next full test.")
    elif trend_delta > 5:
        suggestions.append("Progress trend is positive. Keep the same routine and raise difficulty level.")

    if not suggestions:
        suggestions.append("Keep practicing regularly and track topic-wise errors after every mock test.")

    return {
        "labels": labels,
        "scores": scores,
        "overall_accuracy": overall_accuracy,
        "overall_correct": overall_correct,
        "overall_total": overall_total,
        "attempts_count": len(scores),
        "topic_labels": topic_labels,
        "topic_accuracy": topic_accuracy,
        "weak_topics": weak_topics,
        "strong_topics": strong_topics,
        "avg_questions_per_minute": avg_qpm,
        "coding_completion": coding_completion,
        "trend_delta": trend_delta,
        "suggestions": suggestions[:5],
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


def predict_authenticity(company_data):
    """
    Lightweight fraud scoring (0-100):
    combines rule-based checks to avoid heavy runtime dependencies.
    """
    salary = _safe_float_range(company_data.get("salary_package"), 0.0)
    website = (company_data.get("website") or "").strip().lower()
    email = (company_data.get("contact_email") or "").strip().lower()
    reg_no = (company_data.get("registration_number") or "").strip()
    gst_no = (company_data.get("gst_number") or "").strip()
    history = _safe_int(company_data.get("previous_history")) or 0

    public_domains = ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com")
    website_valid = website.startswith("http://") or website.startswith("https://")
    domain_email = bool(email) and not any(d in email for d in public_domains)
    reg_present = bool(reg_no)
    gst_present = bool(gst_no)
    unrealistic_salary = salary > 20 and (not reg_present or not domain_email)

    score = 0
    reasons = []
    breakdown = []

    def add_check(label, passed, points, reason):
        nonlocal score
        if passed:
            score += points
            breakdown.append({"check": label, "status": "PASS", "points": points})
        else:
            breakdown.append({"check": label, "status": "FAIL", "points": 0})
            reasons.append(reason)

    add_check("Website format", website_valid, 20, "Invalid/Missing Website")
    add_check("Company email domain", domain_email, 20, "Public Email Domain")
    add_check("Registration number", reg_present, 20, "Missing Registration")
    add_check("GST availability", gst_present, 10, "Missing GST Number")
    add_check("Placement history", history >= 1, 10, "No Previous Drive History")
    add_check("Salary realism", not unrealistic_salary, 20, "Unrealistic Salary Offer")

    risk_score_pct = max(0.0, min(100.0, round(100 - score, 2)))

    if risk_score_pct > 70:
        classification = "fraud"
    elif risk_score_pct > 35:
        classification = "suspicious"
    else:
        classification = "legitimate"

    result = {
        "classification": classification,
        "risk_score_pct": risk_score_pct,
        "is_fraud": classification == "fraud",
        "anomaly_score": round((risk_score_pct / 100.0) - 0.5, 4),
        "reasons": "; ".join(reasons) if reasons else "Normal pattern",
        "scoring_breakdown": breakdown,
        "web_score": round(score, 2),
        "ml_score": round(max(0.0, 100.0 - risk_score_pct), 2),
    }

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
    )

@app.route('/resume_enhancer')
def resume_enhancer():
    return "Resume Enhancer AI Page (to be implemented)"



# =========================
# VIEW AVAILABLE MOCK TESTS
# =========================
@app.route("/mock_tests")
def mock_tests():
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    tests = MockTest.query.all()
    return render_template("mock_tests.html", tests=tests)


@app.route("/mock_test")
def mock_test():
    return redirect(url_for("mock_tests"))


@app.route("/view_mock_tests")
def view_mock_tests():
    return redirect(url_for("mock_tests"))


# =========================
# ATTEND TEST
# =========================
@app.route("/attend_test/<int:test_id>", methods=["GET", "POST"])
def attend_test(test_id):
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    test = MockTest.query.get_or_404(test_id)
    questions = Question.query.filter_by(test_id=test_id).all()

    if request.method == "POST":
        started_at = None
        started_at_raw = request.form.get("started_at_iso")
        if started_at_raw:
            try:
                started_at = datetime.fromisoformat(started_at_raw.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                started_at = None

        elapsed_seconds = _safe_int(request.form.get("elapsed_seconds")) or 0
        answered_keys = [q for q in questions if request.form.get(f"question_{q.question_id}")]
        per_question_seconds = int(elapsed_seconds / len(answered_keys)) if answered_keys else 0

        # Create test attempt
        attempt = StudentTest(
            student_id=session["student_id"],
            test_id=test_id,
            started_at=started_at,
            submitted_at=datetime.utcnow(),
        )
        db.session.add(attempt)
        db.session.commit()

        total_score = 0

        for question in questions:
            if question.question_type == "MCQ":
                selected = request.form.get(f"question_{question.question_id}")

                if selected == question.correct_answer:
                    total_score += 1

                answer = StudentAnswer(
                    attempt_id=attempt.attempt_id,
                    question_id=question.question_id,
                    selected_option=selected,
                    is_correct=(selected == question.correct_answer),
                    time_spent_sec=per_question_seconds,
                )

            else:  # CODING
                code = request.form.get(f"question_{question.question_id}")

                answer = StudentAnswer(
                    attempt_id=attempt.attempt_id,
                    question_id=question.question_id,
                    coding_answer=code,
                    is_correct=None,
                    time_spent_sec=per_question_seconds,
                )

            db.session.add(answer)

        attempt.score = total_score
        db.session.commit()

        flash(f"Test submitted! Your Score: {total_score}", "success")
        return redirect(url_for("mock_tests"))

    return render_template("attend_test.html", test=test, questions=questions)


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
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()

        if len(name) < 3:
            flash("Name must be at least 3 characters.", "danger")
            return render_template("edit_student.html", student=student)

        if "@" not in email:
            flash("Enter a valid email address.", "danger")
            return render_template("edit_student.html", student=student)

        email_owner = Student.query.filter_by(email=email).first()
        if email_owner and email_owner.st_id != student.st_id:
            flash("That email is already used by another account.", "danger")
            return render_template("edit_student.html", student=student)

        year = _safe_int(request.form.get("year"))
        cgpa = _safe_float(request.form.get("cgpa"))
        number_of_arrears = _safe_int(request.form.get("number_of_arrears"))

        if request.form.get("year") and year is None:
            flash("Year must be a valid number.", "danger")
            return render_template("edit_student.html", student=student)

        if request.form.get("cgpa") and cgpa is None:
            flash("CGPA must be a valid number.", "danger")
            return render_template("edit_student.html", student=student)

        if request.form.get("number_of_arrears") and number_of_arrears is None:
            flash("Number of arrears must be a valid number.", "danger")
            return render_template("edit_student.html", student=student)

        if cgpa is not None and (cgpa < 0 or cgpa > 10):
            flash("CGPA must be between 0 and 10.", "danger")
            return render_template("edit_student.html", student=student)

        if number_of_arrears is not None and number_of_arrears < 0:
            flash("Number of arrears cannot be negative.", "danger")
            return render_template("edit_student.html", student=student)

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

        student.name = name
        student.email = email
        student.phone = (request.form.get("phone") or "").strip() or None
        student.department = (request.form.get("department") or "").strip() or None
        student.year = year
        student.cgpa = cgpa
        student.number_of_arrears = number_of_arrears if number_of_arrears is not None else 0
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
    tests = MockTest.query.all()

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
    flagged_companies = FraudDetectionRecord.query.filter_by(is_fraud=True).count()
    verified_companies = FraudDetectionRecord.query.filter(
        FraudDetectionRecord.status.in_(["Verified", "Legitimate"])
    ).count()
    pending_companies = FraudDetectionRecord.query.filter_by(status="Pending").count()

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
        tests=tests,
        total_students=total_students,
        total_placements=total_placements,
        total_applications=total_applications,
        total_fraud_checks=total_fraud_checks,
        students=students,
        placements=placements,
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
        FraudDetectionRecord.status.in_(["Verified", "Legitimate"])
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
        FraudDetectionRecord.status.in_(["Verified", "Legitimate"])
    ).count()
    pending_companies = FraudDetectionRecord.query.filter_by(status="Pending").count()

    recent_fraud_records = (
        FraudDetectionRecord.query.order_by(FraudDetectionRecord.analysis_timestamp.desc())
        .limit(20)
        .all()
    )

    fraud_stats = {
        "total_companies": total_companies,
        "flagged_companies": flagged_companies,
        "verified_companies": verified_companies,
        "pending_companies": pending_companies,
        "safe_companies": max(0, total_companies - flagged_companies),
    }

    return render_template(
        "fraud_detection_dashboard.html",
        fraud_stats=fraud_stats,
        recent_fraud_records=recent_fraud_records,
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

    fraud_result = predict_authenticity(
        {
            "company_name": company_name,
            "website": data.get("website"),
            "contact_email": data.get("contact_email"),
            "registration_number": data.get("registration_number"),
            "gst_number": data.get("gst_number"),
            "salary_package": data.get("salary_package"),
            "previous_history": data.get("previous_history", 0),
        }
    )

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
        status="Active" if fraud_result["classification"] == "legitimate" else "Blocked",
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
            "contact_email": data.get("contact_email"),
            "website": data.get("website"),
            "scoring_breakdown": fraud_result.get("scoring_breakdown", []),
            "web_score": fraud_result.get("web_score"),
            "ml_score": fraud_result.get("ml_score"),
        },
        fraud_reasons=fraud_result.get("reasons", ""),
        status=fraud_result["classification"].capitalize(),
    )
    db.session.add(fraud_record)
    db.session.commit()

    if fraud_result["classification"] == "fraud":
        flash(
            f"Company {company.company_name} FLAGGED AS FRAUD! (Risk: {fraud_result['risk_score_pct']}%)",
            "danger",
        )
    elif fraud_result["classification"] == "suspicious":
        flash(
            f"Warning: Company {company.company_name} is SUSPICIOUS. (Risk: {fraud_result['risk_score_pct']}%)",
            "warning",
        )
    else:
        flash(
            f"Company {company.company_name} verified as Legitimate. (Risk: {fraud_result['risk_score_pct']}%)",
            "success",
        )

    return redirect(url_for("fraud_detection_dashboard"))


@app.route("/admin/fraud-detection/override/<int:record_id>", methods=["POST"])
def override_fraud(record_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    record = FraudDetectionRecord.query.get_or_404(record_id)
    reason = (request.form.get("reason") or "Admin manually verified").strip()

    record.status = "Overridden"
    record.override_by_id = session["admin_id"]
    record.override_reason = reason
    record.is_fraud = False
    record.company.status = "Active"

    db.session.commit()
    flash(f"Fraud flag for {record.company.company_name} has been overridden.", "success")
    return redirect(url_for("fraud_detection_dashboard"))


@app.route("/admin/fraud-detection/api/check", methods=["POST"])
def api_check_company():
    if "admin_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "No data provided"}), 400

    return jsonify(predict_authenticity(data))


@app.route("/admin/fraud-detection/stats", methods=["GET"])
def get_fraud_stats():
    if "admin_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    total_companies = Company.query.count()
    flagged_companies = FraudDetectionRecord.query.filter_by(is_fraud=True).count()
    verified_companies = FraudDetectionRecord.query.filter(
        FraudDetectionRecord.status.in_(["Verified", "Legitimate"])
    ).count()
    pending_companies = FraudDetectionRecord.query.filter_by(status="Pending").count()

    stats = {
        "total_companies": total_companies,
        "flagged_companies": flagged_companies,
        "verified_companies": verified_companies,
        "pending_companies": pending_companies,
        "safe_companies": max(0, total_companies - flagged_companies),
        "fraud_rate": round(
            (flagged_companies / total_companies * 100) if total_companies > 0 else 0, 2
        ),
    }
    return jsonify(stats)


# =========================
# CREATE MOCK TEST
# =========================
@app.route("/create_mock_test", methods=["POST"])
def create_mock_test():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    title = request.form.get("title")
    topic = (request.form.get("topic") or "General").strip()
    description = request.form.get("description")
    duration = _safe_int(request.form.get("duration"))

    if not title:
        flash("Test title is required.", "danger")
        return redirect(url_for("admin_dashboard"))

    if duration is None or duration <= 0:
        flash("Duration must be a valid positive number.", "danger")
        return redirect(url_for("admin_dashboard"))

    new_test = MockTest(
        title=title,
        topic=topic,
        description=description,
        duration=duration,
        admin_id=session["admin_id"],
    )

    db.session.add(new_test)
    db.session.commit()

    flash("Mock Test Created Successfully!", "success")
    return redirect(url_for("admin_dashboard"))


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

    new_placement = Placement(
        cmpname=(request.form.get("cmpname") or "").strip(),
        jobreq=(request.form.get("jobreq") or "").strip(),
        package=package_value,
        eligicri=(request.form.get("eligicri") or "").strip(),
        admin_id=session["admin_id"],
    )
    db.session.add(new_placement)
    db.session.commit()

    flash("Placement drive added successfully.", "success")
    return redirect(url_for("admin_dashboard"))


# =========================
# ADD QUESTION TO TEST
# =========================
@app.route("/add_question/<int:test_id>", methods=["GET", "POST"])
def add_question(test_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    test = MockTest.query.get_or_404(test_id)

    if request.method == "POST":
        topic = (request.form.get("topic") or "aptitude").strip().lower()
        difficulty = (request.form.get("difficulty") or "medium").strip().lower()
        total_questions = _safe_int(request.form.get("total_questions"))

        if total_questions is None or total_questions < 1 or total_questions > 50:
            flash("Total questions must be between 1 and 50.", "danger")
            return render_template("add_question.html", test_id=test_id, test=test)

        existing_attempt = StudentTest.query.filter_by(test_id=test_id).first()
        if existing_attempt:
            flash(
                "Cannot regenerate questions. Students have already attempted this test.",
                "danger",
            )
            return render_template("add_question.html", test_id=test_id, test=test)

        Question.query.filter_by(test_id=test_id).delete()

        generated_questions = _generate_ai_questions(topic, difficulty, total_questions)
        for q in generated_questions:
            db.session.add(
                Question(
                    test_id=test_id,
                    question_type=q["question_type"],
                    question_text=q["question_text"],
                    option_a=q["option_a"],
                    option_b=q["option_b"],
                    option_c=q["option_c"],
                    option_d=q["option_d"],
                    correct_answer=q["correct_answer"],
                    expected_output=q["expected_output"],
                )
            )

        db.session.commit()

        flash(f"AI generated {len(generated_questions)} questions successfully!", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("add_question.html", test_id=test_id, test=test)


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
