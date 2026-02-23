from app import app, db
from model import *
from flask import render_template, redirect, request, url_for, flash, session
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime
@app.route("/")
def welcome_page():
    return render_template("welcome.html")

@app.route('/studenthome')
def home_v():
    return render_template("studenthome.html")
@app.route('/register', methods=['GET', 'POST'])
def student_signup():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = generate_password_hash(request.form.get('password'))

        new_student = Student(name=name, email=email, password=password)

        db.session.add(new_student)
        db.session.commit()

        return redirect(url_for('student_login'))

    return render_template('register.html')
@app.route('/student/login', methods=["GET", "POST"])
def student_login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        user = Student.query.filter_by(email=email).first()

        if user and verify_student_password(user, password):
            session["student_id"] = user.st_id
            return redirect(url_for('student_dashboard'))

        flash("Invalid Student Credentials", "danger")

    return render_template("login.html")


@app.route('/login', methods=["GET", "POST"])
def login_page():
    return student_login()


def verify_student_password(user, password):
    stored_password = user.password or ""

    try:
        if check_password_hash(stored_password, password):
            return True
    except ValueError:
        pass

    # Backward compatibility for old plain-text student passwords.
    if stored_password == password:
        user.password = generate_password_hash(password)
        db.session.commit()
        return True

    return False
@app.route('/studentdash')
def student_dashboard():
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    student = db.session.get(Student, session["student_id"])
    labels = ["Test 1", "Test 2", "Test 3", "Test 4"]
    scores = [65, 72, 80, 90]
    return render_template("studentdash.html", student=student, labels=labels, scores=scores)


@app.route('/mock_test')
def mock_test():
    if "student_id" not in session:
        return redirect(url_for("student_login"))
    return "Mock Test page coming soon."


@app.route('/analytics_report')
def analytics_report():
    if "student_id" not in session:
        return redirect(url_for("student_login"))
    return "Analytics report page coming soon."
@app.route('/edit_student/<int:student_id>', methods=["GET", "POST"])
def edit_student(student_id):

    if "student_id" not in session:
        return redirect(url_for("student_login"))

    student = db.session.get(Student, student_id)

    if request.method == "POST":
        student.name = request.form.get("name")
        student.email = request.form.get("email")
        student.phone = request.form.get("phone")
        student.department = request.form.get("department")
        student.year = request.form.get("year")
        student.cgpa = request.form.get("cgpa")

        db.session.commit()
        flash("Details updated successfully!", "success")

        return redirect(url_for('student_dashboard'))

    return render_template("edit_student.html", student=student)
@app.route('/admin/login', methods=["GET", "POST"])
def admin_login():

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        admin = Admin.query.filter_by(email=email).first()

        if admin and check_password_hash(admin.password, password):
            session["admin_id"] = admin.ad_id
            return redirect(url_for("admin_dashboard"))

        flash("Invalid Admin Credentials", "danger")

    return render_template("adlogin.html")
@app.route('/admin/logout')
def admin_logout():
    session.pop("admin_id", None)
    return redirect(url_for("admin_login"))
@app.route('/admindash')
def admin_dashboard():

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    total_students = Student.query.count()
    total_placements = Placement.query.count()
    total_applications = PlacementApplication.query.count()
    total_fraud_checks = AIFraud.query.count()

    students = Student.query.all()
    placements = Placement.query.all()

    return render_template(
        "admindash.html",
        total_students=total_students,
        total_placements=total_placements,
        total_applications=total_applications,
        total_fraud_checks=total_fraud_checks,
        students=students,
        placements=placements
    )
@app.route('/delete_student/<int:student_id>')
def delete_student(student_id):

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    student = db.session.get(Student, student_id)

    if student:
        db.session.delete(student)
        db.session.commit()

    return redirect(url_for("admin_dashboard"))
@app.route('/add_placement', methods=["POST"])
def add_placement():

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    new_placement = Placement(
        cmpname=request.form.get("cmpname"),
        jobreq=request.form.get("jobreq"),
        package=request.form.get("package"),
        eligicri=request.form.get("eligicri"),
        admin_id=session["admin_id"]
    )

    db.session.add(new_placement)
    db.session.commit()

    return redirect(url_for("admin_dashboard"))
@app.route('/resume_enhancer')
def resume_enhancer():
    return "Resume Enhancer AI Page (to be implemented)"
