# Testing Guide for Placement Management System

## Overview

This project includes comprehensive test coverage for:
- **Student Module**: Registration, authentication, profile management, placement applications
- **Admin Module**: Placement management, student management, analytics
- **Fraud Detection**: Email validation, domain verification, organization checks

## Test Files

### 1. TEST_CHECKLIST.md
Complete manual testing checklist with:
- 100+ test cases organized by module
- Edge cases and expected outputs
- Integration test scenarios
- Regression test checklist
- Sample test data and expected outputs

### 2. tests_implementation.py
Automated test suite using pytest with:
- Unit tests for core functionality
- Edge case testing
- Integration tests
- Error handling tests

## Setup

### Install Testing Dependencies

```bash
pip install pytest pytest-cov
```

Or update your requirements.txt:
```bash
pip install -r requirements.txt
```

## Running Tests

### Run All Tests
```bash
pytest tests_implementation.py -v
```

### Run Specific Test Class
```bash
pytest tests_implementation.py::TestStudentRegistration -v
```

### Run Specific Test
```bash
pytest tests_implementation.py::TestStudentRegistration::test_valid_registration -v
```

### Run with Coverage Report
```bash
pytest tests_implementation.py --cov=app --cov=routes --cov=fraud_detector --cov-report=html
```

This generates an HTML coverage report in `htmlcov/index.html`

### Run Specific Module Tests

**Student Module:**
```bash
pytest tests_implementation.py -k "Student" -v
```

**Eligibility Checks:**
```bash
pytest tests_implementation.py::TestEligibilityCheck -v
```

**Fraud Detection:**
```bash
pytest tests_implementation.py::TestFraudDetection -v
```

## Test Organization

### Student Module Tests (TestStudentRegistration, TestProfileCompletion)
- ✓ Registration validation (format, length, duplicates)
- ✓ Profile completion checks
- ✓ Graceful handling of None values in eligibility

### Eligibility Check Tests (TestEligibilityCheck)
- ✓ CGPA validation
- ✓ Department matching
- ✓ Year verification
- ✓ Arrears checking
- ✓ Skill matching (60% threshold)
- ✓ None value handling

### Skill Parsing Tests (TestSkillTokenParsing)
- ✓ Multiple separator support (comma, newline, slash, semicolon)
- ✓ Whitespace handling
- ✓ None/empty string handling

### Fraud Detection Tests (TestFraudDetection)
- ✓ Company legitimacy scoring
- ✓ Disposable email detection
- ✓ GST format validation
- ✓ Missing field handling

### Integration Tests
- ✓ Full student workflow (register → complete profile → apply)
- ✓ Full fraud detection workflow

## Manual Testing (Checklist)

For features not covered by automated tests, use the `TEST_CHECKLIST.md`:

### Student Module Manual Tests
1. **Login/Logout** - Session management
2. **Password Reset** - Token expiry, email sending
3. **Resume Upload** - File validation, PDF/DOCX parsing
4. **Mock Test Taking** - Timer, score calculation
5. **Analytics Visualization** - Chart rendering, data accuracy

### Admin Module Manual Tests
1. **Placement CRUD** - Create, read, update, delete
2. **Company Fraud Check** - Risk scoring display
3. **System Analytics** - Dashboard metrics, export functionality
4. **Student Search/Filter** - Query performance

### Fraud Detection Manual Tests
1. **API Rate Limiting** - DNS, WHOIS, website checks
2. **Network Failures** - Timeout handling
3. **Missing Libraries** - Graceful degradation (phonenumbers, dns, whois)

## Expected Test Results

### Passing Tests Example
```
test_eligible_student PASSED
test_ineligible_low_cgpa PASSED
test_handle_none_cgpa PASSED
test_skill_matching_60_percent PASSED
test_duplicate_email PASSED
test_legitimate_company PASSED

========================= 45 passed in 3.21s =========================
```

## Key Test Scenarios

### ✅ Success Path
```
Student Registration → Complete Profile → Apply to Eligible Placement
→ Submit Mock Test → View Analytics
```

### ❌ Failure Paths
```
1. Register with invalid format → Validation error
2. Apply incomplete profile → Redirect to complete profile
3. Apply ineligible (low CGPA) → Eligibility check fails
4. Add suspicious company → Fraud detection flags
```

### Edge Cases
```
1. Student with None CGPA tries to apply → Fails safely (no crash)
2. Placement requires 60% skill match → Correctly calculated
3. Same student applies to same placement twice → Duplicate prevented
4. Company with disposable email → Fraud flag raised
```

## Debugging Failed Tests

### Common Issues

**1. Database Errors**
```python
# Solution: Ensure test uses in-memory SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
```

**2. Module Not Found**
```bash
# Solution: Install missing dependencies
pip install -r requirements.txt
```

**3. Fraud Detection Fails**
```python
# Solution: Mock external API calls or skip optional libraries
# Tests handle missing phonenumbers, dns.resolver, whois gracefully
```

**4. Date/Time Issues**
```python
# Solution: Use datetime.now() with timezone awareness
from datetime import datetime, timedelta
placement.date = datetime.now() + timedelta(days=30)
```

## Coverage Goals

| Module | Target | Current |
|--------|--------|---------|
| Student | 85%+ | TBD |
| Admin | 80%+ | TBD |
| Fraud Detection | 90%+ | TBD |
| Routes | 75%+ | TBD |
| Overall | 80%+ | TBD |

## Continuous Integration

### GitHub Actions Example
```yaml
name: Run Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: [3.8, 3.9, '3.10', '3.11']
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -r requirements.txt
      - run: pytest tests_implementation.py -v --tb=short
```

## Performance Testing

For load testing fraud detection with many companies:
```bash
# Using locust or Apache JMeter
locust -f locustfile.py --host=http://localhost:5000
```

## Security Testing

### OWASP Top 10 Checks
- [ ] SQL Injection (parameterized queries verified)
- [ ] XSS (input sanitization in templates)
- [ ] Session Fixation (session tokens)
- [ ] CSRF (token validation)
- [ ] Weak Password (strength requirements)
- [ ] Sensitive Data Exposure (HTTPS, password hashing)

### Run Security Scan
```bash
# Using bandit
bandit -r . -f json -o bandit-report.json

# Using safety for dependencies
safety check
```

## Test Maintenance

### When Adding New Features

1. Add test case to TEST_CHECKLIST.md (manual)
2. Add pytest implementation if automation needed
3. Update expected outputs
4. Run full test suite: `pytest tests_implementation.py -v`
5. Update coverage report

### When Modifying Existing Code

1. Run affected test class: `pytest tests_implementation.py::TestAffectedClass -v`
2. Check coverage doesn't decrease
3. Add new edge cases if applicable
4. Re-run integration tests

## Test Data Management

### Reset Database
```bash
# Delete all test data
sqlite3 placement_db_sqlite3.db "DELETE FROM students;"

# Or clear with Python
from app import db
db.session.query(Student).delete()
db.session.commit()
```

### Generate Test Data
```bash
python generate_test_data.py
```

## Troubleshooting

### Tests Pass Locally but Fail in CI
- Check Python version differences
- Verify database setup (SQLite vs MySQL)
- Check timezone settings
- Review environment variables in CI

### Flaky Tests (Intermittent Failures)
- Check tests aren't dependent on time-based operations
- Verify database state is reset between tests
- Look for race conditions in concurrent tests

### Slow Tests
- Profile with: `pytest --durations=10`
- Consider fixtures optimization
- Reduce number of database commits

## Sign-Off

- [ ] All unit tests passing
- [ ] Integration tests passing
- [ ] Coverage > 80%
- [ ] Manual test checklist completed
- [ ] Security tests passed
- [ ] Performance targets met
- [ ] Ready for deployment

---

## Quick Reference

```bash
# Run all tests with coverage
pytest tests_implementation.py --cov --cov-report=html -v

# Run only critical tests
pytest tests_implementation.py -k "Eligibility or Registration" -v

# Run and show output
pytest tests_implementation.py -v -s

# Stop on first failure
pytest tests_implementation.py -x

# Verbose output with full traces
pytest tests_implementation.py -vv --tb=long
```

## Questions?

Refer to:
- `TEST_CHECKLIST.md` - Complete test cases
- `tests_implementation.py` - Test implementation examples
- Individual test docstrings for expected behavior
