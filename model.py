from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Admin(db.Model):
    __tablename__ = 'admin'
    ad_id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(80), unique=True, nullable=False)
    password=db.Column(db.String, nullable=False)
    # Relationships
    placements = db.relationship('Placement', backref='admin', lazy=True)
    fraud_checks = db.relationship('AIFraud', backref='admin', lazy=True)


class Student(db.Model):
    __tablename__ = 'students'
    st_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password=db.Column(db.String, nullable=False)
    # phoneno = db.Column(db.String(15))
    # depmnt = db.Column(db.String(80))
    # year = db.Column(db.Integer)
    # cgpa = db.Column(db.Float)
    
    # One-to-one resume
    resume = db.relationship('Resume', backref='student', uselist=False)
    
    # Notifications
    notifications = db.relationship('Notification', backref='student', lazy=True)
    
    # Applica
    applications = db.relationship('PlacementApplication', backref='student', lazy=True)


class Notification(db.Model):
    __tablename__ = 'notification'
    nid = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, nullable=False)
    msgtext = db.Column(db.Text, nullable=False)

    student_id = db.Column(db.Integer, db.ForeignKey('students.st_id'), nullable=False)

class Resume(db.Model):
    __tablename__ = 'resume'
    rid = db.Column(db.Integer, primary_key=True)
    skills = db.Column(db.Text)
    project = db.Column(db.Text)

    student_id = db.Column(db.Integer, db.ForeignKey('students.st_id'), unique=True, nullable=False)


class Placement(db.Model):
    __tablename__ = 'placement'
    placeid = db.Column(db.Integer, primary_key=True)
    cmpname = db.Column(db.String(120), nullable=False)
    jobreq = db.Column(db.Text)
    package = db.Column(db.Float)
    eligicri = db.Column(db.Text)

    date = db.Column(db.Date)
    venue = db.Column(db.String(120))
    
    admin_id = db.Column(db.Integer, db.ForeignKey('admin.ad_id'), nullable=False)

    # Applications
    applications = db.relationship('PlacementApplication', backref='placement', lazy=True)

    # Fraud checks
    fraud_checks = db.relationship('AIFraud', backref='placement', lazy=True)

class AIFraud(db.Model):
    __tablename__ = 'aifraud'
    check_id = db.Column(db.Integer, primary_key=True)
    result = db.Column(db.String(50))

    admin_id = db.Column(db.Integer, db.ForeignKey('admin.ad_id'), nullable=False)
    placement_id = db.Column(db.Integer, db.ForeignKey('placement.placeid'), nullable=False)


class PlacementApplication(db.Model):
    __tablename__ = 'placement_application'
    app_id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.st_id'), nullable=False)
    placement_id = db.Column(db.Integer, db.ForeignKey('placement.placeid'), nullable=False)
    status = db.Column(db.String(50), default="Pending")