# Placement Management System - Complete Test Checklist

## Table of Contents
1. [Student Module](#student-module)
2. [Admin Module](#admin-module)
3. [Fraud Detection Module](#fraud-detection-module)
4. [Integration Tests](#integration-tests)

---

## Student Module

### Authentication & Authorization

#### Registration
- [ ] **Valid Registration**
  - Input: Valid register number (CEC23CS027), name (3+ chars), valid email, strong password
  - Expected: Account created, success message, redirect to login
  - Data validation: Email unique, register number format validated

- [ ] **Invalid Register Number**
  - Input: "ABC123", "CEC2CS027", "cec23cs027" (lowercase)
  - Expected: Error message, form not submitted
  - Message: "Register number must be like CEC23CS027."

- [ ] **Short Name**
  - Input: Name with less than 3 characters (e.g., "Ab")
  - Expected: Validation error, form not submitted
  - Message: "Name must be at least 3 characters."

- [ ] **Invalid Email**
  - Input: "notanemail", "user@", "@domain.com"
  - Expected: Validation error
  - Message: "Enter a valid email address."

- [ ] **Weak Password**
  - Input: "123456", "password", "Password1" (no special char)
  - Expected: Validation error
  - Message: "Use 8+ chars with uppercase, lowercase, number, and special character."

- [ ] **Duplicate Email**
  - Setup: Register with email@example.com
  - Input: Register again with same email
  - Expected: Error message
  - Message: "Email is already registered. Please login instead."

- [ ] **Duplicate Register Number**
  - Setup: Register with CEC23CS001
  - Input: Register again with same CEC23CS001
  - Expected: Error message
  - Message: "Register number is already registered."

#### Login
- [ ] **Valid Credentials**
  - Input: Registered email and correct password
  - Expected: Session created, redirect to student dashboard
  - Verify: session["student_id"] set

- [ ] **Non-existent Email**
  - Input: Email that was never registered
  - Expected: Generic error message (for security)
  - Message: "Invalid email or password."

- [ ] **Wrong Password**
  - Input: Correct email, incorrect password
  - Expected: Generic error message
  - Message: "Invalid email or password."

- [ ] **Account Locked (Future Feature)**
  - Setup: 5+ failed login attempts
  - Input: Valid credentials
  - Expected: Account temporarily locked

#### Session Management
- [ ] **Session Persistence**
  - Login, navigate to dashboard, refresh page
  - Expected: Session maintained, user stays logged in

- [ ] **Session Expiry**
  - Login, wait > configured timeout, make request
  - Expected: Redirect to login page

- [ ] **Logout**
  - Click logout button
  - Expected: Session cleared, redirect to welcome page, back button doesn't return to dashboard

### Profile Management

#### Profile Completion
- [ ] **Default Incomplete Profile**
  - Register new student
  - Expected: has_complete_profile() returns False because fields are None

- [ ] **Partial Profile Update**
  - Update only CGPA and department
  - Expected: has_complete_profile() still returns False

- [ ] **Complete Profile Update**
  - Update all required fields: cgpa, department, year, programming_languages, technical_skills, tools_technologies, resume_pdf_path
  - Expected: has_complete_profile() returns True

- [ ] **Missing CGPA**
  - Input: All fields except CGPA is None
  - Expected: Profile incomplete, cannot apply to placements

- [ ] **Missing Skills**
  - Input: All fields except skills are set
  - Expected: Profile incomplete

- [ ] **Missing Resume**
  - Input: All fields updated but no resume uploaded
  - Expected: Profile incomplete

#### Profile Validation
- [ ] **CGPA Range**
  - Valid: 0.0-10.0
  - Invalid: Negative, >10, non-numeric
  - Expected: Form validation error

- [ ] **Year Range**
  - Valid: 1-4
  - Invalid: 0, 5+, negative
  - Expected: Validation error

- [ ] **Arrears**
  - Valid: 0-10
  - Invalid: Negative, non-numeric
  - Expected: Validation error

- [ ] **Skills Format**
  - Input: "Python, Java / C++" (various separators)
  - Expected: Properly parsed and stored
  - Verify: _split_skill_tokens correctly handles comma, newline, slash, semicolon

#### Resume Upload
- [ ] **Valid PDF**
  - Input: Valid .pdf file (max 5MB)
  - Expected: File saved, path stored in resume_pdf_path

- [ ] **Valid DOCX**
  - Input: Valid .docx file
  - Expected: Text extracted, file saved

- [ ] **Valid Text/Markdown**
  - Input: .txt or .md file
  - Expected: Content read directly

- [ ] **Oversized File**
  - Input: >5MB file
  - Expected: Error message, file rejected

- [ ] **Unsupported Format**
  - Input: .jpg, .exe, .zip
  - Expected: Error message
  - Message: "Supported formats: .txt, .md, .pdf, .docx"

- [ ] **Corrupt File**
  - Input: Fake PDF (actually text)
  - Expected: Graceful handling, error message if parsing fails

### Placement Applications

#### Eligibility Check
- [ ] **Eligible Student (Complete Profile)**
  - Setup: Student with CGPA 8.0, department CS, year 3
  - Placement: min_cgpa=7.5, department=CS, year=3
  - Input: Apply
  - Expected: check_eligibility() returns True, application created

- [ ] **Ineligible - Low CGPA**
  - Setup: Student CGPA 6.0
  - Placement: min_cgpa=7.5
  - Expected: Error message, denial of application

- [ ] **Ineligible - Wrong Department**
  - Setup: Student department=Civil
  - Placement: department=CS
  - Expected: Error message

- [ ] **Ineligible - Wrong Year**
  - Setup: Student year=2
  - Placement: year=3
  - Expected: Error message

- [ ] **Ineligible - Too Many Arrears**
  - Setup: Student number_of_arrears=3
  - Placement: max_arrears=2
  - Expected: Error message

- [ ] **Ineligible - Missing Skills (< 60% match)**
  - Setup: Student skills="Python, Java"
  - Placement: required="Python, Java, C++, JavaScript, React"
  - Expected: Failed skill check (2/5 = 40% < 60%)

- [ ] **Eligible - Sufficient Skills (≥ 60% match)**
  - Setup: Student skills="Python, Java, C++"
  - Placement: required="Python, Java, C++, JavaScript"
  - Expected: check_eligibility() returns True (3/4 = 75% ≥ 60%)

- [ ] **Incomplete Profile - Cannot Apply**
  - Setup: Student with CGPA=None
  - Input: Try to apply
  - Expected: Redirect to complete profile, error message

- [ ] **None Values Handled**
  - Setup: Student with CGPA=None, department=None
  - Expected: check_eligibility() returns False (not crash)

#### Application Workflow
- [ ] **First Application**
  - Setup: Eligible student, new placement
  - Input: Apply
  - Expected: Application created, status="Applied", confirmation message

- [ ] **Duplicate Application**
  - Setup: Student already applied to placement
  - Input: Try to apply again
  - Expected: Error message
  - Message: "Already Applied"

- [ ] **Duplicate Company Application**
  - Setup: Student applied to Company A (Placement 1)
  - Input: Apply to Company A (Placement 2)
  - Expected: Error message
  - Message: "Already Applied"

- [ ] **Multiple Applications**
  - Setup: Student eligible for 5 different placements
  - Input: Apply to all 5
  - Expected: All 5 applications created successfully

### Mock Tests & Analytics

#### Mock Test Taking
- [ ] **Start Mock Test**
  - Input: Click "Start Test" button
  - Expected: Questions loaded, timer started, questions randomized

- [ ] **Submit Incomplete Test**
  - Setup: Test with 30 questions, answered 20
  - Input: Submit
  - Expected: Score calculated for answered 20 questions

- [ ] **All Sections Present**
  - Take full mock test
  - Expected: Aptitude, Logical, Technical, Coding sections all represented

- [ ] **Score Calculation**
  - Setup: 25 correct out of 30
  - Expected: Score = 83.33%

- [ ] **Section-wise Scores**
  - Setup: 5/5 Aptitude, 4/5 Logical, 8/10 Technical, 8/10 Coding
  - Expected: 
    - aptitude_score = 5
    - logical_score = 4
    - technical_score = 8
    - coding_score = 8
    - total_score = 25/30

#### Analytics Report
- [ ] **No Attempts**
  - Setup: New student, 0 mock tests taken
  - View: Analytics Report
  - Expected: 
    - Latest Score: "—"
    - Tests Attempted: 0
    - All charts empty with message

- [ ] **Single Attempt**
  - Setup: Student with 1 mock test (80%)
  - Expected:
    - Latest Score: 80%
    - Best Score: 80%
    - Average Score: 80%
    - Chart shows 1 data point

- [ ] **Multiple Attempts - Trend**
  - Setup: Attempts with scores 60%, 70%, 75%, 80%
  - Expected:
    - Latest: 80%
    - Best: 80%
    - Average: 71.25%
    - Trend chart shows upward trend

- [ ] **Performance Chart**
  - Setup: Multiple section scores
  - Expected: Bar chart shows section accuracy averages

- [ ] **Last 30 Attempts**
  - Setup: 50 mock tests taken
  - View: Analytics
  - Expected: Only last 30 shown in charts

### Resume Enhancement (Bonus)

#### Resume Upload & Processing
- [ ] **Extract Text from PDF**
  - Input: Valid PDF resume
  - Expected: Text extracted successfully

- [ ] **Extract Text from DOCX**
  - Input: Valid DOCX resume
  - Expected: Text extracted successfully

- [ ] **Enhancement Suggestions**
  - Setup: Uploaded resume
  - Input: Select target role (Software Engineer)
  - Expected: AI suggestions to improve resume

---

## Admin Module

### Authentication & Authorization

#### Admin Login
- [ ] **Valid Admin Credentials**
  - Input: Admin email and correct password
  - Expected: session["admin_id"] set, redirect to admin dashboard

- [ ] **Non-existent Admin**
  - Input: Non-existent email
  - Expected: Error message

- [ ] **Wrong Password**
  - Input: Correct email, wrong password
  - Expected: Error message

- [ ] **Session Authorization**
  - Login as student, try to access /admin routes
  - Expected: Redirect to student login (403/401 error)

### Placement Management

#### Create Placement
- [ ] **Valid Placement Creation**
  - Input: All fields filled (company name, package, min_cgpa, department, allowed_year, required_skills)
  - Expected: Placement created, success message

- [ ] **Required Fields Missing**
  - Input: No company name
  - Expected: Validation error

- [ ] **Invalid Package**
  - Input: Package = -5, Package = "abc"
  - Expected: Validation error

- [ ] **Invalid Date**
  - Input: Past date, malformed date
  - Expected: Validation error or warning

- [ ] **Duplicate Placement**
  - Setup: Create placement for Company X on 2026-04-01
  - Input: Create another for Company X on 2026-04-01
  - Expected: Allowed (same company can have multiple drives)

#### View Placements
- [ ] **List All Placements**
  - Setup: 20 placements created
  - Expected: All visible, paginated/scrollable

- [ ] **Filter by Date**
  - Input: Filter for placements in April 2026
  - Expected: Only April placements shown

- [ ] **Filter by Company**
  - Input: Search for "Microsoft"
  - Expected: Only Microsoft placements shown

- [ ] **Sort by Package**
  - Input: Sort highest to lowest
  - Expected: Highest package first

#### Update Placement
- [ ] **Update Package**
  - Input: Change package from 8 LPA to 10 LPA
  - Expected: Updated successfully, history maintained

- [ ] **Update Eligibility Criteria**
  - Input: Change min_cgpa from 7.0 to 7.5
  - Expected: New criteria applied to future applications

- [ ] **Update Date**
  - Input: Change drive date to future date
  - Expected: Updated, notifications sent to applied students

#### Delete Placement
- [ ] **Delete Placement (No Applications)**
  - Setup: Placement with 0 applications
  - Input: Delete
  - Expected: Deleted successfully

- [ ] **Delete Placement (With Applications)**
  - Setup: Placement with 10 applications
  - Input: Delete
  - Expected: Either delete cascade or prevent deletion with error message

### Student Management

#### View Students
- [ ] **List All Students**
  - Expected: All registered students shown with pagination

- [ ] **Filter by Department**
  - Input: Filter "Computer Science"
  - Expected: Only CS students shown

- [ ] **Filter by Status**
  - Input: Filter "Placed"
  - Expected: Only placed students shown

- [ ] **Search by Name**
  - Input: Search "Raj"
  - Expected: Students with "Raj" in name shown

#### Student Details
- [ ] **View Student Profile**
  - Expected: All profile details visible (CGPA, skills, resume link, applications)

- [ ] **View Student Applications**
  - Expected: All applications with status shown

- [ ] **View Student Analytics**
  - Expected: Mock test scores, trends, placement success rate

- [ ] **Edit Student Details**
  - Input: Update CGPA from 8.0 to 8.5
  - Expected: Updated successfully, audit log created

### Mock Test Management

#### Create Mock Test
- [ ] **Create from CSV**
  - Input: Upload questions.csv
  - Expected: All questions parsed and test created

- [ ] **Validate Question Format**
  - Expected: Section, difficulty, topic fields validated

- [ ] **Set Test Metadata**
  - Input: Title, description, duration, max attempts
  - Expected: Stored and applied

#### View Test Results
- [ ] **See All Results**
  - Setup: 100 students took test
  - Expected: Results paginated, sortable by score

- [ ] **Filter by Score Range**
  - Input: Filter 70-100%
  - Expected: Only those within range shown

- [ ] **Export Results**
  - Input: Click export CSV
  - Expected: CSV file downloaded with all results

#### Test Analytics
- [ ] **Average Score**
  - Setup: Scores 60, 70, 80, 90
  - Expected: Average = 75%

- [ ] **Pass Rate**
  - Setup: Passing score = 70%, among 100 students, 75 scored ≥70%
  - Expected: Pass rate = 75%

- [ ] **Section Performance**
  - Expected: Average accuracy for each section calculated and compared

### Company Management

#### Add Company
- [ ] **Valid Company Registration**
  - Input: Company name, industry, website, GST, contact info
  - Expected: Company added, fraud check initiated

- [ ] **Duplicate Company**
  - Setup: Company "Microsoft" already registered
  - Input: Register "Microsoft" again
  - Expected: Error message or update existing

- [ ] **Missing Contact Info**
  - Input: No contact email
  - Expected: Validation warning (may proceed or block)

#### Fraud Detection Integration
- [ ] **Automatic Fraud Check**
  - Setup: Add new company
  - Expected: Fraud detection runs automatically
  - Results: Classification, risk score, reasons stored

- [ ] **View Fraud Results**
  - Setup: Company classified as suspicious
  - Expected: Risk score, reasons displayed
  - Example: "Domain not verified (70%), Email domain suspicious (50%)"

- [ ] **Manual Verification**
  - Setup: Company marked as "pending"
  - Input: Admin manually approves
  - Expected: Status changed to "verified"

### System Analytics

#### Dashboard Metrics
- [ ] **Total Students**
  - Expected: Count of all registered students shown

- [ ] **Total Placements**
  - Expected: Sum of all placement drives

- [ ] **Average Salary**
  - Expected: Average of all placement packages (or 0 if none)

- [ ] **Placement Rate**
  - Expected: (Placed students / Total students) * 100

- [ ] **Top Companies**
  - Expected: Companies sorted by number of placements

- [ ] **Trends Chart**
  - Expected: Placements over time shown as line chart

#### Export & Reporting
- [ ] **Export All Data**
  - Input: Export to CSV
  - Expected: All relevant data exported without sensitive information

- [ ] **Report Generation**
  - Expected: PDF report with all metrics generated

---

## Fraud Detection Module

### Email Validation

#### Disposable Email Detection
- [ ] **Disposable Domain**
  - Input: "user@mailinator.com", "user@tempmail.com", "user@10minutemail.com"
  - Expected: Flagged as disposable domain, risk increase

- [ ] **Legitimate Email**
  - Input: "user@gmail.com", "user@corporate.com"
  - Expected: No flag for disposability

- [ ] **Public Email Domain**
  - Input: "company@gmail.com"
  - Expected: Flagged as public domain (warning, not critical)

- [ ] **Corporate Domain**
  - Input: "hr@microsoft.com"
  - Expected: Not flagged as suspicious

#### Email Format Validation
- [ ] **Valid Format**
  - Input: "user@domain.com"
  - Expected: Valid

- [ ] **Invalid Format**
  - Input: "invalidemail", "@domain.com", "user@"
  - Expected: Invalid

- [ ] **Missing @ Symbol**
  - Input: "userdomain.com"
  - Expected: Invalid

### Domain Validation

#### DNS Verification
- [ ] **Valid Domain with MX Records**
  - Input: "microsoft.com"
  - Expected: MX records found, domain verified

- [ ] **Domain Without MX Records**
  - Input: "fakeddomain12345.com"
  - Expected: No MX records found, flagged as suspicious

- [ ] **Domain Not Found**
  - Input: "nonexistentdomain99999999.com"
  - Expected: Domain lookup fails, flagged as high risk

#### Website Verification
- [ ] **Valid Website URL**
  - Input: "https://www.microsoft.com"
  - Expected: HTTP request succeeds, website verified

- [ ] **Unreachable Website**
  - Input: "https://fakwebsite99999.com"
  - Expected: Connection fails, flagged as suspicious

- [ ] **SSL Certificate Check**
  - Input: "https://invalidcertificate.example.com"
  - Expected: SSL error detected, flagged

### Organization Verification

#### GST Number Validation (India)
- [ ] **Valid GST Format**
  - Input: "27AAPCT1234H1Z0"
  - Expected: Format valid (15 chars, correct structure)

- [ ] **Invalid GST Length**
  - Input: "27AAPCT1234H1Z" (14 chars)
  - Expected: Invalid

- [ ] **Matching Registration Number**
  - Input: GST "27AAPCT1234H1Z0", Registration "27AAPCT1234H1Z0"
  - Expected: Matches, increases credibility

- [ ] **Mismatched Registration**
  - Input: GST from Kerala, but company address in Maharashtra
  - Expected: Mismatch flagged

#### Company Name Verification
- [ ] **Legal Entity Indicators**
  - Input: "Microsoft Limited", "Apple Pvt Ltd"
  - Expected: Legal indicator found, increases credibility

- [ ] **No Legal Indicators**
  - Input: "Random Company Name"
  - Expected: Flagged as potentially suspicious

- [ ] **Name Too Generic**
  - Input: "Tech Company", "Business Solutions"
  - Expected: Generic name flagged

### Phone Number Validation

#### Number Format
- [ ] **Valid Indian Phone**
  - Input: "+919876543210", "9876543210"
  - Expected: Valid format

- [ ] **Invalid Length**
  - Input: "987654321" (9 digits)
  - Expected: Invalid

- [ ] **Invalid Format**
  - Input: "abc-def-ghij"
  - Expected: Invalid

#### Number Verification
- [ ] **Country Code Match**
  - Input: "+919876543210", company country India
  - Expected: Match, verified

- [ ] **Country Code Mismatch**
  - Input: "+441234567890" (UK), company in India
  - Expected: Mismatch flagged

### Risk Assessment

#### Risk Score Calculation
- [ ] **Legitimate Company (Low Risk)**
  - Input: 
    - Valid domain with MX records
    - Valid GST
    - Reachable website
    - Valid phone
  - Expected: Risk score < 30%

- [ ] **Suspicious Company (Medium Risk)**
  - Input:
    - Domain without MX records
    - No legal indicators
    - Invalid GST format
  - Expected: Risk score 30-70%

- [ ] **Fraudulent Company (High Risk)**
  - Input:
    - Non-existent domain
    - Disposable email domain
    - Conflicting information
  - Expected: Risk score > 70%

#### Classification
- [ ] **Legitimate Classification**
  - Expected: Risk < 30%, status "legitimate"

- [ ] **Suspicious Classification**
  - Expected: Risk 30-70%, status "suspicious"

- [ ] **Fraud Classification**
  - Expected: Risk > 70%, status "fraud"

### Reason Generation

#### Multiple Reasons
- [ ] **Composite Risk**
  - Setup: Domain unverified + No MX records + Generic name
  - Expected: output = "Domain unverified (40%), No MX records found (50%), Company name too generic (30%)"

- [ ] **Single Reason**
  - Setup: Only issue is invalid GST
  - Expected: output = "Invalid GST number format (60%)"

- [ ] **No Reasons**
  - Setup: All checks pass
  - Expected: output = "" or None

### Edge Cases

#### Optional Library Handling
- [ ] **Missing phonenumbers Library**
  - Expected: Phone validation skipped, no crash

- [ ] **Missing dns.resolver Library**
  - Expected: DNS check skipped gracefully

- [ ] **Missing whois Library**
  - Expected: WHOIS lookup skipped, uses cached data if available

#### Network Issues
- [ ] **DNS Timeout**
  - Setup: DNS server unreachable (simulated)
  - Expected: Timeout caught, domain flagged as unverifiable

- [ ] **Website Timeout**
  - Setup: Website takes >5s to respond
  - Expected: Timeout caught, website flagged as unresponsive

- [ ] **Connection Refused**
  - Setup: Domain exists but no web server
  - Expected: Connection error caught, flagged

#### Data Edge Cases
- [ ] **Null/None Company Name**
  - Input: None
  - Expected: Handled gracefully, flagged

- [ ] **Empty String Fields**
  - Input: "", "  "
  - Expected: Treated as missing, flagged

- [ ] **Very Long Strings**
  - Input: Company name 500+ chars
  - Expected: Truncated or rejected

---

## Integration Tests

### Full Student Workflow
```
1. Register student → verify account created
2. Complete profile → verify has_complete_profile() true
3. Apply to eligible placement → verify eligibility check passes
4. Take mock test → verify score saved
5. View analytics → verify all data displayed
6. Apply to ineligible placement → verify rejected
7. Logout → verify session cleared
```

### Full Admin Workflow
```
1. Login as admin → verify dashboard loaded
2. Add company → verify fraud detection runs
3. Create placement → verify stored
4. View student applications → verify listed
5. View system analytics → verify metrics calculated
6. Export data → verify CSV valid
7. Logout → verify cleaned up
```

### Fraud Detection Workflow
```
1. Register company A (legitimate) → verify low risk score
2. Register company B (suspicious) → verify medium risk score
3. Register company C (fraudulent) → verify high risk score
4. View fraud report → verify all scores and reasons shown
5. Admin approves company A → verify status changed
6. Admin blocks company C → verify applications rejected
```

### Edge Case Integration
```
1. Try to apply with incomplete profile → verify rejected
2. Try to apply to ineligible (CGPA too low) → verify rejected
3. Try to apply with None values in profile → verify rejected safely
4. Register disposable email + submit application → verify fraud flag
5. Create placement with invalid company → verify fraud check alerted admin
```

---

## Regression Test Checklist

### After Code Changes
- [ ] Run all authentication tests
- [ ] Run all eligibility check tests (especially None handling)
- [ ] Run fraud detection with all libraries present and missing
- [ ] Check database migrations completed
- [ ] Verify no SQL injection vulnerabilities
- [ ] Check all API endpoints return proper status codes
- [ ] Verify error messages don't leak sensitive info
- [ ] Test with SQLite and MySQL databases

### Performance Tests
- [ ] Load 10,000 students, verify dashboard loads < 2s
- [ ] Calculate analytics for student with 1000 mock test attempts
- [ ] Run fraud check on 500 companies simultaneously
- [ ] Test maximum concurrent login attempts

### Security Tests
- [ ] SQL injection attempts in search fields
- [ ] XSS attempts in resume upload
- [ ] CSRF token validation
- [ ] Password reset token expiry
- [ ] Session fixation prevention
- [ ] Rate limiting on failed logins

---

## Test Data Setup

### Student Test Accounts
```
1. Complete_Student (CGPA 9.0, all skills filled)
2. Incomplete_Student (No skills)
3. Low_CGPA (CGPA 5.0)
4. Many_Arrears (number_of_arrears = 5)
```

### Placement Test Data
```
1. Easy (min_cgpa=7.0, any department)
2. Difficult (min_cgpa=9.0, CS department, 60% skill match required)
3. Exclusive (year=4 only)
```

### Company Test Data
```
1. Legitimate_Corp (Google - low fraud risk)
2. Suspicious_Co (Unknown company - medium risk)
3. Fake_Co (Disposable email - high risk)
```

---

## Expected Output Examples

### Eligibility Check - Success
```python
Input:
  student.cgpa = 8.5
  student.department = "CS"
  student.year = 3
  student.programming_languages = "Python, Java, C++"
  placement.min_cgpa = 7.5
  placement.department = "CS"
  placement.required_programming_languages = "Python, Java"

Output: True
```

### Eligibility Check - Failure (None CGPA)
```python
Input:
  student.cgpa = None
  student.department = "CS"

Output: False (no crash)
```

### Fraud Detection - Legitimate
```python
Input:
  company_name = "Microsoft Inc"
  email = "hr@microsoft.com"
  domain = "microsoft.com"

Output:
  classification = "legitimate"
  risk_score_pct = 15.0
  reasons = ""
```

### Fraud Detection - Suspicious
```python
Input:
  company_name = "Tech Solutions"
  email = "admin@tempmail.com"
  domain = "unknowndomain123.com"

Output:
  classification = "suspicious"
  risk_score_pct = 55.0
  reasons = "Disposable email domain (50%), Domain unverified (40%), Generic company name (30%)"
```

### Analytics - Multiple Attempts
```python
Input: Student with 5 mock tests (scores: 60, 65, 70, 75, 80)

Output:
  latest_score = 80%
  average_score = 70%
  best_score = 80%
  attempts = 5
  trend = "Upward"
```

---

## Testing Tools Recommended

- **Unit Testing**: pytest, unittest
- **API Testing**: Postman, curl
- **Fraud Detection**: Mock DNS/WHOIS responses
- **Database**: SQLite for testing, MySQL for staging
- **Performance**: Apache JMeter, locust
- **Security**: OWASP ZAP, Burp Suite Community

---

## Sign-Off

- [ ] All student module tests passed
- [ ] All admin module tests passed
- [ ] All fraud detection tests passed
- [ ] All integration tests passed
- [ ] Security tests completed
- [ ] Performance tests completed
- [ ] Ready for production deployment

Test Date: ___________
Tester: ___________
Status: [ ] PASS [ ] FAIL
