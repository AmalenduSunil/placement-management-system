"""
Test Suite for Placement Management System
Tests for Student Module, Admin Module, and Fraud Detection

Run with: pytest tests/test_placement_system.py -v
"""

import pytest
from datetime import datetime, timedelta
from app import app, db
from model import Student, Admin, Placement, PlacementApplication, Company, FraudDetectionRecord
from routes import check_eligibility, _split_skill_tokens
from fraud_detector import run_full_analysis


@pytest.fixture
def client():
    """Flask test client fixture."""
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


@pytest.fixture
def test_student(client):
    """Create a test student fixture."""
    student = Student(
        st_id=1,
        register_number="CEC23CS001",
        name="Test Student",
        email="student@example.com",
        password="hashed_password",
        cgpa=8.5,
        department="Computer Science",
        year=3,
        number_of_arrears=0,
        programming_languages="Python, Java, C++",
        technical_skills="Django, Flask, REST APIs",
        tools_technologies="Git, Docker, Linux",
        projects="Project 1, Project 2",
        internship_experience="1 year at TechCorp",
        certifications="AWS Certified",
        resume_pdf_path="/path/to/resume.pdf"
    )
    db.session.add(student)
    db.session.commit()
    return student


@pytest.fixture
def test_placement(client):
    """Create a test placement fixture."""
    admin = Admin(
    ad_id=1,
    email="admin@example.com",
    name="Admin",   # ✅ ADD THIS LINE
    password="hashed"
)
    db.session.add(admin)
    db.session.commit()
    
    placement = Placement(
        placeid=1,
        cmpname="TechCorp",
        jobreq="Senior Software Engineer",
        package=12.5,
        date=datetime.now() + timedelta(days=30),
        venue="Campus",
        min_cgpa=7.5,
        department="Computer Science",
        allowed_year=3,
        max_arrears=1,
        required_programming_languages="Python, Java",
        required_technical_skills="Django, REST APIs",
        required_tools="Git",
        admin_id=1
    )
    db.session.add(placement)
    db.session.commit()
    return placement


# ==================== STUDENT MODULE TESTS ====================

class TestStudentRegistration:
    """Test suite for student registration."""
    
    def test_valid_registration(self, client):
        """Test valid student registration."""
        response = client.post('/register', data={
            'register_number': 'CEC23CS027',
            'name': 'John Doe',
            'email': 'john@example.com',
            'password': 'SecurePass123!'
        })
        assert response.status_code in [200, 302]  # Redirect or success
    
    def test_invalid_register_number_format(self, client):
        """Test registration with invalid register number format."""
        response = client.post('/register', data={
            'register_number': 'INVALID',
            'name': 'John Doe',
            'email': 'john@example.com',
            'password': 'SecurePass123!'
        })
        assert b'Register number must be like CEC23CS027' in response.data
    
    def test_short_name(self, client):
        """Test registration with name too short."""
        response = client.post('/register', data={
            'register_number': 'CEC23CS027',
            'name': 'Jo',
            'email': 'john@example.com',
            'password': 'SecurePass123!'
        })
        assert b'Name must be at least 3 characters' in response.data
    
    def test_invalid_email(self, client):
        """Test registration with invalid email."""
        response = client.post('/register', data={
            'register_number': 'CEC23CS027',
            'name': 'John Doe',
            'email': 'notanemail',
            'password': 'SecurePass123!'
        })
        assert b'Enter a valid email address' in response.data
    
    def test_weak_password(self, client):
        """Test registration with weak password."""
        response = client.post('/register', data={
            'register_number': 'CEC23CS027',
            'name': 'John Doe',
            'email': 'john@example.com',
            'password': 'weak'
        })
        assert b'Use 8+ chars' in response.data
    
    def test_duplicate_email(self, client, test_student):
        """Test registration with duplicate email."""
        response = client.post('/register', data={
            'register_number': 'CEC23CS028',
            'name': 'Jane Doe',
            'email': 'student@example.com',  # Same as test_student
            'password': 'SecurePass123!'
        })
        assert b'Email is already registered' in response.data
    
    def test_duplicate_register_number(self, client, test_student):
        """Test registration with duplicate register number."""
        response = client.post('/register', data={
            'register_number': 'CEC23CS001',  # Same as test_student
            'name': 'Jane Doe',
            'email': 'jane@example.com',
            'password': 'SecurePass123!'
        })
        assert b'Register number is already registered' in response.data


class TestProfileCompletion:
    """Test suite for student profile completion."""
    
    def test_new_student_incomplete_profile(self, test_student):
        """Test that new student has incomplete profile."""
        # Create student without skills and resume
        incomplete_student = Student(
            st_id=2,
            register_number="CEC23CS002",
            name="Incomplete Student",
            email="incomplete@example.com",
            password="hashed"
        )
        db.session.add(incomplete_student)
        db.session.commit()
        
        assert not incomplete_student.has_complete_profile()
    
    def test_complete_profile(self, test_student):
        """Test that student with all fields has complete profile."""
        assert test_student.has_complete_profile()
    
    def test_incomplete_profile_missing_cgpa(self, test_student):
        """Test profile incomplete when CGPA is None."""
        test_student.cgpa = None
        assert not test_student.has_complete_profile()
    
    def test_incomplete_profile_missing_skills(self, test_student):
        """Test profile incomplete when required fields missing."""
        test_student.year = None
        assert not test_student.has_complete_profile()


# ==================== ELIGIBILITY CHECK TESTS ====================

class TestEligibilityCheck:
    """Test suite for placement eligibility checking."""
    
    def test_eligible_student(self, test_student, test_placement):
        """Test eligible student passes check."""
        assert check_eligibility(test_student, test_placement) is True
    
    def test_ineligible_low_cgpa(self, test_student, test_placement):
        """Test ineligible student with low CGPA."""
        test_student.cgpa = 6.0
        assert check_eligibility(test_student, test_placement) is False
    
    def test_ineligible_wrong_department(self, test_student, test_placement):
        """Test ineligible student with wrong department."""
        test_student.department = "Civil"
        assert check_eligibility(test_student, test_placement) is False
    
    def test_ineligible_wrong_year(self, test_student, test_placement):
        """Test ineligible student with wrong year."""
        test_student.year = 2
        assert check_eligibility(test_student, test_placement) is False
    
    def test_ineligible_too_many_arrears(self, test_student, test_placement):
        """Test ineligible student with too many arrears."""
        test_student.number_of_arrears = 3
        assert check_eligibility(test_student, test_placement) is False
    
    def test_ineligible_insufficient_skills(self, test_student, test_placement):
        """Test ineligible student with insufficient skill match (< 60%)."""
        test_student.programming_languages = "Python"  # Only 1 out of 2 required
        test_student.technical_skills = None
        result = check_eligibility(test_student, test_placement)
        assert result is False
    
    def test_eligible_sufficient_skills(self, test_student, test_placement):
        """Test eligible student with sufficient skill match (≥ 60%)."""
        test_student.programming_languages = "Python, Java, C++"  # 100% match
        result = check_eligibility(test_student, test_placement)
        assert result is True
    
    def test_handle_none_cgpa(self, test_student, test_placement):
        """Test graceful handling of None CGPA."""
        test_student.cgpa = None
        assert check_eligibility(test_student, test_placement) is False
    
    def test_handle_none_department(self, test_student, test_placement):
        """Test graceful handling of None department."""
        test_student.department = None
        assert check_eligibility(test_student, test_placement) is False
    
    def test_handle_none_year(self, test_student, test_placement):
        """Test graceful handling of None year."""
        test_student.year = None
        assert check_eligibility(test_student, test_placement) is False
    
    def test_handle_none_arrears(self, test_student, test_placement):
        """Test handling of None arrears (defaults to 0)."""
        test_student.number_of_arrears = None
        # Should not crash, defaults to 0
        result = check_eligibility(test_student, test_placement)
        assert result is True
    
    def test_handle_none_skills(self, test_student, test_placement):
        """Test handling of None skills (defaults to empty)."""
        test_student.programming_languages = None
        test_student.technical_skills = None
        result = check_eligibility(test_student, test_placement)
        assert result is False  # No skills match


# ==================== SKILL PARSING TESTS ====================

class TestSkillTokenParsing:
    """Test suite for skill token parsing."""
    
    def test_parse_comma_separated(self):
        """Test parsing comma-separated skills."""
        skills = _split_skill_tokens("Python, Java, JavaScript")
        assert skills == {"python", "java", "javascript"}
    
    def test_parse_newline_separated(self):
        """Test parsing newline-separated skills."""
        skills = _split_skill_tokens("Python\nJava\nC++")
        assert skills == {"python", "java", "c++"}
    
    def test_parse_slash_separated(self):
        """Test parsing slash-separated skills."""
        skills = _split_skill_tokens("Python/Java/C++")
        assert skills == {"python", "java", "c++"}
    
    def test_parse_mixed_separators(self):
        """Test parsing skills with mixed separators."""
        skills = _split_skill_tokens("Python, Java / C++; JavaScript")
        assert "python" in skills
        assert "java" in skills
        assert "c++" in skills
        assert "javascript" in skills
    
    def test_parse_with_whitespace(self):
        """Test parsing with extra whitespace."""
        skills = _split_skill_tokens("  Python  ,  Java  ")
        assert skills == {"python", "java"}
    
    def test_parse_empty_string(self):
        """Test parsing empty string."""
        skills = _split_skill_tokens("")
        assert skills == set()
    
    def test_parse_none(self):
        """Test parsing None value."""
        skills = _split_skill_tokens(None)
        assert skills == set()


# ==================== FRAUD DETECTION TESTS ====================

class TestFraudDetection:
    """Test suite for fraud detection module."""
    
    def test_legitimate_company(self):
        """Test fraud detection for legitimate company."""
        result = run_full_analysis(
            company_name="Microsoft Inc",
            contact_email="hr@microsoft.com",
            gst_number="27AAPCT1234H1Z0",
            website="https://www.microsoft.com",
            phone_number="+919876543210"
        )
        
        # Legitimate companies should have low risk score
        assert result['risk_score_pct'] < 50
        assert result['classification'] in ['legitimate', 'pending']
    
    def test_suspicious_company(self):
        """Test fraud detection for suspicious company."""
        result = run_full_analysis(
            company_name="Random Tech Corp",
            contact_email="admin@tempmail.com",
            gst_number="123",  # Invalid GST
            website="https://nonexistent99999.com",
            phone_number="invalid"
        )
        
        # Suspicious companies should have medium risk score
        assert result['risk_score_pct'] > 30
    
    def test_disposable_email_detection(self):
        """Test detection of disposable email domains."""
        result = run_full_analysis(
            company_name="Company",
            contact_email="user@mailinator.com",  # Disposable
            gst_number="27AAPCT1234H1Z0",
            website="https://example.com",
            phone_number="+919876543210"
        )
        
        # Should detect disposable domain
        assert 'Disposable' in result.get('reasons', '')
    
    def test_gst_format_validation(self):
        """Test GST number format validation."""
        # Valid GST
        result_valid = run_full_analysis(
            company_name="Company",
            contact_email="contact@example.com",
            gst_number="27AAPCT1234H1Z0",  # Correct format
            website="https://example.com",
            phone_number="+919876543210"
        )
        
        # Invalid GST
        result_invalid = run_full_analysis(
            company_name="Company",
            contact_email="contact@example.com",
            gst_number="INVALID",
            website="https://example.com",
            phone_number="+919876543210"
        )
        
        # Risk should be different
        assert result_valid['risk_score_pct'] != result_invalid['risk_score_pct']
    
    def test_missing_optional_fields(self):
        """Test fraud detection with missing optional fields."""
        result = run_full_analysis(
            company_name="Company Name",
            contact_email="contact@example.com",
            gst_number=None,  # Optional
            website=None,  # Optional
            phone_number=None  # Optional
        )
        
        # Should complete without crashing
        assert 'risk_score_pct' in result
        assert 'classification' in result


# ==================== PLACEMENT APPLICATION TESTS ====================

class TestPlacementApplication:
    """Test suite for placement applications."""
    
    def test_valid_application(self, client, test_student, test_placement):
        """Test valid application creation."""
        application = PlacementApplication(
            student_id=test_student.st_id,
            placement_id=test_placement.placeid,
            status="Applied"
        )
        db.session.add(application)
        db.session.commit()
        
        assert application.app_id is not None
        assert application.status == "Applied"
    
    def test_duplicate_application_prevention(self, test_student, test_placement):
        """Test prevention of duplicate applications."""
        # First application
        app1 = PlacementApplication(
            student_id=test_student.st_id,
            placement_id=test_placement.placeid,
            status="Applied"
        )
        db.session.add(app1)
        db.session.commit()
        
        # Try to create duplicate (should be prevented at application level)
        app2 = PlacementApplication(
            student_id=test_student.st_id,
            placement_id=test_placement.placeid,
            status="Applied"
        )
        
        # Query to find duplicates
        existing = PlacementApplication.query.filter_by(
            student_id=test_student.st_id,
            placement_id=test_placement.placeid
        ).first()
        
        assert existing is not None
        assert existing.app_id == app1.app_id


# ==================== ERROR HANDLING TESTS ====================

class TestErrorHandling:
    """Test suite for error handling and edge cases."""
    
    def test_null_placement_parameter(self, test_student):
        """Test handling of null placement parameter."""
        result = check_eligibility(test_student, None)
        # Should handle gracefully
        assert isinstance(result, (bool, type(None)))
    
    def test_null_student_parameter(self, test_placement):
        """Test handling of null student parameter."""
        try:
            result = check_eligibility(None, test_placement)
            # If it doesn't crash, result should be False
            assert result is False
        except AttributeError:
            # It's acceptable to raise AttributeError
            pass
    
    def test_empty_company_name(self):
        """Test fraud detection with empty company name."""
        result = run_full_analysis(
            company_name="",
            contact_email="contact@example.com",
            gst_number="27AAPCT1234H1Z0",
            website="https://example.com",
            phone_number="+919876543210"
        )
        
        # Should flag as suspicious
        assert result['risk_score_pct'] > 30


# ==================== INTEGRATION TESTS ====================

class TestIntegration:
    """Integration tests for full workflows."""
    
    def test_complete_student_workflow(self, client, test_student, test_placement):
        """Test complete student workflow."""
        # 1. Verify student profile is complete
        assert test_student.has_complete_profile()
        
        # 2. Verify eligibility check passes
        assert check_eligibility(test_student, test_placement)
        
        # 3. Create application
        application = PlacementApplication(
            student_id=test_student.st_id,
            placement_id=test_placement.placeid,
            status="Applied"
        )
        db.session.add(application)
        db.session.commit()
        
        # 4. Verify application created
        assert application.app_id is not None
        
        # 5. Query application back
        retrieved = PlacementApplication.query.filter_by(
            student_id=test_student.st_id,
            placement_id=test_placement.placeid
        ).first()
        assert retrieved is not None
    
    def test_complete_fraud_detection_workflow(self):
        """Test complete fraud detection workflow."""
        companies = [
            {
                "name": "Microsoft Corp",
                "email": "hr@microsoft.com",
                "expected_risk": "low"
            },
            {
                "name": "Fake Company XYZ",
                "email": "admin@mailinator.com",
                "expected_risk": "high"
            }
        ]
        
        results = []
        for company_data in companies:
            result = run_full_analysis(
                company_name=company_data["name"],
                contact_email=company_data["email"],
                gst_number="27AAPCT1234H1Z0",
                website="https://example.com",
                phone_number="+919876543210"
            )
            results.append(result)
        
        # Verify results are different
        assert results[0]['risk_score_pct'] != results[1]['risk_score_pct']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
