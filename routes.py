from app import app
from flask_sqlalchemy import SQLAlchemy
from model import *
from flask import Flask, render_template,redirect,request,url_for,flash

@app.route('/register', methods=['GET', 'POST'])
def student_signup():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')

        new_student = Student(name=name, email=email, password=password)
        db.session.add(new_student)
        db.session.commit()

        return redirect('/login')

    return render_template('register.html')

@app.route('/')
def home_v():  
    return render_template("studenthome.html")


@app.route('/login', methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        admin = db.session.query(Admin).filter_by(
            email=email, password=password).first()

        if admin:
            return redirect('/admindash')

        user = db.session.query(Student).filter_by(
            email=email, password=password).first()

        if user:
            return redirect('/studentdash')

        return "Invalid credentials"

    return render_template("login.html")


@app.route("/studentdash")
def studentdash():
    return render_template("studentdash.html")

@app.route('/studentdash/<int:student_id>')
def student_dashboard(student_id):
    student = db.session.query(Student).get(student_id)

    # Example mock test data (replace with DB query later)
    labels = ["Test 1", "Test 2", "Test 3", "Test 4"]
    scores = [65, 72, 80, 90]

    return render_template("studentdash.html", student=student, labels=labels, scores=scores)

@app.route('/resume_enhancer')
def resume_enhancer():
    return "Resume Enhancer AI Page (to be implemented)"


@app.route('/mock_test')
def mock_test():
    return "Mock Test Page (to be implemented)"


@app.route('/analytics_report')
def analytics_report():
    return "Analytics & Reports Page (to be implemented)"

@app.route('/edit_student/<int:student_id>', methods=["GET", "POST"])
def edit_student(student_id):
    student = db.session.query(Student).get(student_id)

    if request.method == "POST":
        student.name = request.form.get("name")
        student.email = request.form.get("email")
        student.phone = request.form.get("phone")
        student.department = request.form.get("department")
        student.year = request.form.get("year")
        student.cgpa = request.form.get("cgpa")

        db.session.commit()
        flash("Details updated successfully!", "success")
        return redirect(url_for('student_dashboard', student_id=student.student_id))

    return render_template("edit_student.html", student=student)