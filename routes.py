import re
import os
import json
import csv
import io
import random
import secrets
import threading
import copy
from datetime import datetime, timedelta
from urllib.parse import urlparse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, or_, text
from sqlalchemy.orm import joinedload

from flask import Response, flash, jsonify, make_response, redirect, render_template, request, session, url_for
from flask_mail import Message
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

def _try_get_pdf_reader():
    try:
        from PyPDF2 import PdfReader as _PdfReader

        return _PdfReader
    except Exception:
        return None


def _try_get_docx_document():
    try:
        from docx import Document as _Document

        return _Document
    except Exception:
        return None

from app import app, db, mail, APP_START_TIME, RECENT_REQUEST_TIMESTAMPS, RECENT_RESPONSE_TIMES
from fraud_detector import run_full_analysis, run_quick_analysis
from model import (
    Admin,
    CsvMockTestAttempt,
    CsvAdaptiveTestAttempt,
    MockTest,
    Question,
    QuestionBank,
    StudentMockTestResult,
    TestResult,
    Placement,
    PlacementApplication,
    Company,
    FraudDetectionRecord,
    Notification,
    Resume,
    Student,
    LoginEvent,
    SystemErrorLog,
)
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


def _admin_login_redirect():
    if "admin_id" not in session:
        flash("Admin login required.", "warning")
        return redirect(url_for("admin_login"))
    return None


def _split_skill_tokens(raw_text):
    return {
        token.strip().lower()
        for token in re.split(r"[,\n/;|]", raw_text or "")
        if token and token.strip()
    }


def _extract_resume_text(upload_file):
    if not upload_file or not upload_file.filename:
        return None, "Please upload a resume file."

    filename = (upload_file.filename or "").lower()
    if filename.endswith((".txt", ".md")):
        try:
            resume_text = upload_file.read().decode("utf-8", errors="ignore").strip()
        except Exception:
            return None, "Could not read text file. Please upload a valid TXT/MD file."
    elif filename.endswith(".pdf"):
        PdfReader = _try_get_pdf_reader()
        if PdfReader is None:
            return None, "PDF support is not installed on the server. Install PyPDF2 and try again."
        try:
            reader = PdfReader(upload_file)
            pages = [(page.extract_text() or "") for page in reader.pages]
            resume_text = "\n".join(pages).strip()
        except Exception:
            return None, "Could not read PDF file. Please upload a valid PDF."
    elif filename.endswith(".docx"):
        Document = _try_get_docx_document()
        if Document is None:
            return None, "DOCX support is not installed on the server. Install python-docx and try again."
        try:
            document = Document(upload_file)
            paragraphs = [paragraph.text for paragraph in document.paragraphs]
            resume_text = "\n".join(paragraphs).strip()
        except Exception:
            return None, "Could not read DOCX file. Please upload a valid DOCX."
    else:
        return None, "Supported formats: .txt, .md, .pdf, .docx"

    if not resume_text:
        return None, "Uploaded file is empty."

    return resume_text, None


def _to_title_case(text):
    return " ".join(word.capitalize() for word in (text or "").split())


def _clean_line(line):
    line = re.sub(r"\s+", " ", (line or "")).strip()
    line = re.sub(r"^[\-\*\u2022]\s*", "", line)
    return line


def _is_probable_heading(line):
    check = (line or "").strip().lower().replace(":", "")
    heading_words = {
        "education",
        "experience",
        "work experience",
        "internship",
        "internships",
        "projects",
        "project",
        "skills",
        "technical skills",
        "certifications",
        "achievements",
        "summary",
        "professional summary",
        "objective",
    }
    return check in heading_words


def _split_resume_sections(lines):
    sections = {"general": []}
    current = "general"

    for line in lines:
        cleaned = _clean_line(line)
        if not cleaned:
            continue
        if _is_probable_heading(cleaned):
            current = cleaned.lower().replace(":", "")
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(cleaned)

    return sections


def _rewrite_bullet(line, index):
    action_verbs = (
        "Developed",
        "Designed",
        "Implemented",
        "Optimized",
        "Automated",
        "Led",
        "Built",
        "Improved",
        "Deployed",
        "Delivered",
    )
    verb = action_verbs[index % len(action_verbs)]
    cleaned = _clean_line(line)
    lowered = cleaned.lower()

    intern_match = re.match(r"intern(?:ed)? at\s+([a-zA-Z0-9&\-\.\s]+?)(?:\s+on\s+(.+))?$", lowered)
    if intern_match:
        company = (intern_match.group(1) or "").strip().title()
        topic = ((intern_match.group(2) or "business-critical tasks").strip()) or "business-critical tasks"
        cleaned = f"as an intern at {company}, worked on {topic}"
    else:
        cleaned = re.sub(
            r"^(worked on|created|built|developed|designed|implemented|responsible for)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

    cleaned = cleaned[0].lower() + cleaned[1:] if len(cleaned) > 1 else cleaned.lower()

    if any(char.isdigit() for char in cleaned):
        return f"- {verb} {cleaned}, improving measurable outcomes."
    return f"- {verb} {cleaned} with clear ownership and business impact."


def _role_keywords(target_role):
    role = (target_role or "").lower()
    bank = {
        "data": [
            "SQL",
            "Python",
            "Pandas",
            "NumPy",
            "Power BI",
            "Tableau",
            "ETL",
            "Statistics",
            "Data Wrangling",
            "Visualization",
        ],
        "analyst": ["SQL", "Excel", "Power BI", "Tableau", "A/B Testing", "Data Cleaning", "Pivot Tables", "KPI"],
        "python": ["Python", "Flask", "FastAPI", "REST APIs", "PostgreSQL", "Git", "Docker", "Unit Testing", "OOP"],
        "web": [
            "HTML",
            "CSS",
            "JavaScript",
            "React",
            "Node.js",
            "REST APIs",
            "Git",
            "Responsive Design",
            "TypeScript",
        ],
        "java": ["Java", "Spring Boot", "OOP", "MySQL", "REST APIs", "JUnit", "Maven", "Git", "Microservices"],
        "ml": [
            "Python",
            "scikit-learn",
            "TensorFlow",
            "PyTorch",
            "Model Evaluation",
            "Feature Engineering",
            "Cross-validation",
            "NLP",
        ],
        "ai": [
            "Python",
            "LLMs",
            "Prompt Engineering",
            "RAG",
            "Vector Databases",
            "Model Evaluation",
            "HuggingFace",
            "LangChain",
        ],
        "cloud": ["AWS", "Azure", "GCP", "Docker", "Kubernetes", "CI/CD", "Terraform", "DevOps"],
        "fullstack": ["React", "Node.js", "MongoDB", "REST APIs", "Git", "HTML", "CSS", "Docker"],
        "backend": ["Python", "Node.js", "PostgreSQL", "REST APIs", "Docker", "Redis", "Git", "Microservices"],
    }
    found = []
    for key, values in bank.items():
        if key in role:
            found.extend(values)

    seen = []
    for item in found:
        if item not in seen:
            seen.append(item)
    return (
        seen
        or [
            "Python",
            "SQL",
            "Git",
            "Data Structures",
            "Problem Solving",
            "Communication",
            "Teamwork",
            "REST APIs",
        ]
    )


def _extract_existing_skills(resume_text):
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\+\#\.\-]{1,24}", resume_text or "")
    normalized = {token.lower() for token in tokens}
    return normalized


def _compute_ats_score(resume_text, sections, role_keywords, existing_tokens):
    score = 0
    breakdown = {}

    essential = {"education", "skills", "experience", "projects", "summary"}
    found_sections = set()
    for sec in sections.keys():
        for e in essential:
            if e in sec:
                found_sections.add(e)
    section_score = int((len(found_sections) / len(essential)) * 30)
    score += section_score
    breakdown["Sections Completeness"] = {
        "score": section_score,
        "max": 30,
        "detail": f"{len(found_sections)}/{len(essential)} key sections found",
    }

    matched = [kw for kw in role_keywords if kw.lower() in existing_tokens]
    kw_score = int((len(matched) / max(len(role_keywords), 1)) * 30)
    score += kw_score
    breakdown["Keyword Match"] = {
        "score": kw_score,
        "max": 30,
        "detail": f"{len(matched)}/{len(role_keywords)} role keywords present",
    }

    all_lines = [line for lines in sections.values() for line in lines]
    quantified = sum(1 for line in all_lines if re.search(r"\d+", line))
    quant_score = min(int((quantified / max(len(all_lines), 1)) * 40), 20)
    score += quant_score
    breakdown["Quantified Impact"] = {"score": quant_score, "max": 20, "detail": f"{quantified} bullets with measurable data"}

    word_count = len((resume_text or "").split())
    if 300 <= word_count <= 700:
        len_score = 10
        len_detail = f"{word_count} words (ideal range)"
    elif 200 <= word_count < 300 or 700 < word_count <= 900:
        len_score = 6
        len_detail = f"{word_count} words (slightly outside ideal 300–700)"
    else:
        len_score = 3
        len_detail = f"{word_count} words (aim for 300–700)"
    score += len_score
    breakdown["Resume Length"] = {"score": len_score, "max": 10, "detail": len_detail}

    has_email = 1 if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", resume_text or "") else 0
    has_phone = 1 if re.search(r"[\d]{10}", resume_text or "") else 0
    has_links = 1 if re.search(r"(github\.com|linkedin\.com|portfolio)", resume_text or "", re.IGNORECASE) else 0
    fmt_score = (has_email + has_phone + has_links) * 3 + 1
    fmt_score = min(fmt_score, 10)
    score += fmt_score
    breakdown["Contact & Links"] = {
        "score": fmt_score,
        "max": 10,
        "detail": f"Email: {'✓' if has_email else '✗'}  Phone: {'✓' if has_phone else '✗'}  Links: {'✓' if has_links else '✗'}",
    }

    return min(score, 100), breakdown


def _extract_contact_info(resume_text, general_lines):
    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", resume_text or "")
    phone_match = re.search(r"[\+\(]?[\d][\d\s\-\(\)]{8,14}[\d]", resume_text or "")
    github_match = re.search(r"(?:https?://)?(?:www\.)?github\.com/[\w\-]+", resume_text or "", re.IGNORECASE)
    linkedin_match = re.search(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w\-]+", resume_text or "", re.IGNORECASE)

    name = ""
    for line in (general_lines or [])[:5]:
        if "@" not in line and not re.search(r"\d{5,}", line) and len(line.split()) <= 6:
            name = line
            break

    return {
        "name": name,
        "email": email_match.group(0) if email_match else "",
        "phone": phone_match.group(0).strip() if phone_match else "",
        "github": github_match.group(0) if github_match else "",
        "linkedin": linkedin_match.group(0) if linkedin_match else "",
    }


def _build_skills_list(sections, role_keywords, existing_tokens):
    raw_skills = []
    for key in ("skills", "technical skills", "core skills", "competencies"):
        raw_skills.extend(sections.get(key, []))

    expanded = []
    for line in raw_skills:
        if any(sep in line for sep in [",", "|", ";"]):
            parts = re.split(r"[,|;]", line)
            expanded.extend(p.strip() for p in parts if p.strip())
        else:
            expanded.append(line.strip())

    existing_lower = {s.lower() for s in expanded}
    for kw in role_keywords:
        if kw.lower() not in existing_lower:
            expanded.append(kw)

    seen = set()
    final = []
    for s in expanded:
        if s and s.lower() not in seen and len(s) < 50:
            seen.add(s.lower())
            final.append(s)
    return final


def _enhance_resume(resume_text, target_role):
    lines = [line.strip() for line in (resume_text or "").splitlines() if line.strip()]
    if not lines:
        return None

    sections = _split_resume_sections(lines)
    role_keywords = _role_keywords(target_role)
    existing_tokens = _extract_existing_skills(resume_text)

    ats_score, score_breakdown = _compute_ats_score(resume_text, sections, role_keywords, existing_tokens)

    present_keywords = [kw for kw in role_keywords if kw.lower() in existing_tokens]
    missing_keywords = [kw for kw in role_keywords if kw.lower() not in existing_tokens]

    role_display = _to_title_case(target_role) if target_role else "Campus Placement Roles"
    contact = _extract_contact_info(resume_text, sections.get("general", []))

    summary = (
        f"Motivated and detail-oriented student targeting {role_display} roles. "
        "Experienced in building practical, production-style projects with hands-on exposure to "
        "industry tools and technologies. Demonstrates strong analytical and problem-solving skills, "
        "ability to collaborate in team environments, and commitment to delivering clean, maintainable code."
    )

    skills_list = _build_skills_list(sections, role_keywords, existing_tokens)

    exp_lines = (
        sections.get("experience", [])
        + sections.get("work experience", [])
        + sections.get("internship", [])
        + sections.get("internships", [])
    )
    exp_lines = [l for l in exp_lines if len(l) > 10]
    exp_bullets = (
        [_rewrite_bullet(l, i) for i, l in enumerate(exp_lines)]
        if exp_lines
        else [
            "- Completed internship tasks involving real-world software development and problem solving.",
            "- Collaborated with senior developers using Git-based workflows and structured code reviews.",
        ]
    )

    proj_lines = sections.get("projects", []) + sections.get("project", [])
    proj_lines = [l for l in proj_lines if len(l) > 10]
    proj_bullets = (
        [_rewrite_bullet(l, i + len(exp_bullets)) for i, l in enumerate(proj_lines)]
        if proj_lines
        else [
            "- Developed a full-stack web application with user authentication, REST API, and database integration.",
            "- Built and deployed a data analysis pipeline processing 10,000+ records with 95% accuracy.",
            "- Designed a machine learning model achieving competitive benchmark performance on test data.",
        ]
    )

    edu_lines = sections.get("education", [])
    cert_lines = sections.get("certifications", []) + sections.get("achievements", [])

    fixes = [
        "Use Action + Tool + Result format for every bullet point.",
        "Add at least one quantified metric per project (e.g., accuracy %, users, time saved).",
        "Keep resume to one page — prioritize role-relevant experience first.",
        "Use consistent verb tense: past tense for completed work, present for ongoing.",
        "Include your LinkedIn and GitHub profile URL in the header.",
        "Avoid personal pronouns (I, me, my) — start every bullet with an action verb.",
        "Spell-check carefully and ensure zero grammar errors before submission.",
    ]

    sep = "─" * 58
    contact_parts = []
    if contact["email"]:
        contact_parts.append(contact["email"])
    if contact["phone"]:
        contact_parts.append(contact["phone"])
    if contact["linkedin"]:
        contact_parts.append(contact["linkedin"])
    if contact["github"]:
        contact_parts.append(contact["github"])

    skills_formatted = " • ".join(skills_list) if skills_list else "See resume for skills"

    exp_block = "\n".join(exp_bullets) if exp_bullets else "  No prior experience listed."
    proj_block = "\n".join(proj_bullets) if proj_bullets else "  No projects listed."
    edu_block = "\n".join(f"  {l}" for l in edu_lines) if edu_lines else "  [Add your education details here]"
    cert_block = "\n".join(f"  • {l}" for l in cert_lines) if cert_lines else ""

    plain_text = (
        f"""{contact["name"] if contact["name"] else "YOUR NAME"}
{" | ".join(contact_parts) if contact_parts else "email@example.com | linkedin.com/in/you | github.com/you"}
{sep}

PROFESSIONAL SUMMARY
{summary}

{sep}

TECHNICAL SKILLS
{skills_formatted}

{sep}

EXPERIENCE & INTERNSHIPS
{exp_block}

{sep}

PROJECTS
{proj_block}

{sep}

EDUCATION
{edu_block}
"""
        + (f"\n{sep}\n\nCERTIFICATIONS & ACHIEVEMENTS\n{cert_block}\n" if cert_block else "")
    )

    return {
        "role_display": role_display,
        "ats_score": ats_score,
        "score_breakdown": score_breakdown,
        "contact": contact,
        "summary": summary,
        "skills_list": skills_list,
        "exp_bullets": exp_bullets,
        "proj_bullets": proj_bullets,
        "edu_lines": edu_lines,
        "cert_lines": cert_lines,
        "present_keywords": present_keywords,
        "missing_keywords": missing_keywords,
        "fixes": fixes,
        "plain_text": plain_text,
    }


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


def _load_questions_from_csv(csv_path):
    questions = []
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                section = _normalize_test_section(row.get("section"))
                if section not in {"Aptitude", "Logical", "Technical", "Coding"}:
                    continue

                option_a = (row.get("option_a") or "").strip()
                option_b = (row.get("option_b") or "").strip()
                option_c = (row.get("option_c") or "").strip()
                option_d = (row.get("option_d") or "").strip()
                correct = _coerce_correct_answer_letter(
                    row.get("correct_answer"), option_a, option_b, option_c, option_d
                )
                if correct not in {"A", "B", "C", "D"}:
                    continue

                difficulty = (row.get("difficulty") or "").strip().lower()
                topic = (row.get("topic") or "").strip()
                time_limit = (row.get("time_limit") or "").strip()
                weight = (row.get("weight") or "").strip()
                company_level = (row.get("company_level") or "").strip()

                questions.append(
                    {
                        "section": section,
                        "question_text": (row.get("question_text") or "").strip(),
                        "option_a": option_a,
                        "option_b": option_b,
                        "option_c": option_c,
                        "option_d": option_d,
                        "options": {"A": option_a, "B": option_b, "C": option_c, "D": option_d},
                        "answer": correct,
                        "difficulty": difficulty,
                        "topic": topic,
                        "time_limit": time_limit,
                        "weight": weight,
                        "company_level": company_level,
                    }
                )
    except OSError:
        return []
    return questions


def _pick_with_difficulty_mix(candidates, needed, used_texts):
    pool = [q for q in candidates if q.get("question_text") and q["question_text"] not in used_texts]
    if len(pool) < needed:
        return []

    by_diff = {"easy": [], "medium": [], "hard": [], "": []}
    for q in pool:
        by_diff.get(q.get("difficulty") or "", by_diff[""]).append(q)

    for diff in by_diff:
        random.shuffle(by_diff[diff])

    picked = []

    # Ensure a mix where possible.
    preferred = ["easy", "medium", "hard"]
    if needed >= 2:
        for diff in preferred:
            if len(picked) >= needed:
                break
            if by_diff.get(diff):
                picked.append(by_diff[diff].pop())

    # Fill the remainder randomly from all remaining questions.
    remaining = []
    for diff in ["easy", "medium", "hard", ""]:
        remaining.extend(by_diff.get(diff, []))
    random.shuffle(remaining)

    while len(picked) < needed and remaining:
        picked.append(remaining.pop())

    if len(picked) != needed:
        return []

    for q in picked:
        used_texts.add(q["question_text"])
    return picked


def _pick_with_difficulty_profile(candidates, needed, used_texts, profile):
    """
    profile example: {"easy": 0.7, "medium": 0.3, "hard": 0.0}
    Best-effort: tries to match distribution, then tops up from remaining pool.
    """
    pool = [q for q in candidates if q.get("question_text") and q["question_text"] not in used_texts]
    if len(pool) < needed:
        return []

    by_diff = {"easy": [], "medium": [], "hard": [], "": []}
    for q in pool:
        by_diff.get(q.get("difficulty") or "", by_diff[""]).append(q)
    for diff in by_diff:
        random.shuffle(by_diff[diff])

    targets = {}
    remaining = needed
    for diff in ("easy", "medium", "hard"):
        frac = float((profile or {}).get(diff, 0.0) or 0.0)
        count = int(round(frac * needed))
        targets[diff] = max(0, count)
    # Normalize target totals to needed.
    total_target = sum(targets.values())
    if total_target != needed:
        targets["medium"] = max(0, targets.get("medium", 0) + (needed - total_target))

    picked = []
    for diff in ("easy", "medium", "hard"):
        want = min(targets.get(diff, 0), needed - len(picked))
        if want <= 0:
            continue
        take = by_diff.get(diff, [])[:want]
        picked.extend(take)
        by_diff[diff] = by_diff.get(diff, [])[want:]

    # Top up from any remaining questions.
    rest = []
    for diff in ("easy", "medium", "hard", ""):
        rest.extend(by_diff.get(diff, []))
    random.shuffle(rest)
    while len(picked) < needed and rest:
        picked.append(rest.pop())

    if len(picked) != needed:
        return []

    for q in picked:
        used_texts.add(q["question_text"])
    return picked


def _generate_mock_test_from_csv(total_questions=30, mode="full"):
    plan = _build_section_plan(total_questions)
    candidates = []
    for p in (
        os.path.join(app.root_path, "questions.csv"),
        os.path.join(os.getcwd(), "questions.csv"),
    ):
        if os.path.exists(p):
            candidates = _load_questions_from_csv(p)
            if candidates:
                break

    if not candidates:
        return None

    grouped = {"Aptitude": [], "Logical": [], "Technical": [], "Coding": []}
    for q in candidates:
        grouped.setdefault(q["section"], []).append(q)

    used_texts = set()
    selected = []
    for section_name, count in plan.items():
        if (mode or "full") == "practice":
            profile = {"easy": 0.7, "medium": 0.3, "hard": 0.0}
            picked = _pick_with_difficulty_profile(grouped.get(section_name, []), count, used_texts, profile)
        else:
            picked = _pick_with_difficulty_mix(grouped.get(section_name, []), count, used_texts)
        if not picked:
            return {
                "error": "Insufficient questions in CSV for balanced generation.",
                "required": plan,
                "available": {k: len(v) for k, v in grouped.items()},
            }
        for q in picked:
            selected.append({**q, "id": secrets.token_hex(6)})

    random.shuffle(selected)

    # Do not expose answers by default; return a separate key for graders.
    answer_key = {q["id"]: q["answer"] for q in selected}
    questions_out = []
    for q in selected:
        q2 = dict(q)
        q2.pop("answer", None)
        questions_out.append(q2)

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_questions": total_questions,
        "mode": mode or "full",
        "section_plan": plan,
        "questions": questions_out,
        "answer_key": answer_key,
    }


def _difficulty_step(current, is_correct):
    order = ["easy", "medium", "hard"]
    if current not in order:
        current = "medium"
    idx = order.index(current)
    idx = min(idx + 1, len(order) - 1) if is_correct else max(idx - 1, 0)
    return order[idx]


def _question_dedupe_key(question):
    core = (
        (question.get("section") or "").strip(),
        (question.get("question_text") or "").strip(),
        (question.get("options") or {}).get("A", "").strip(),
        (question.get("options") or {}).get("B", "").strip(),
        (question.get("options") or {}).get("C", "").strip(),
        (question.get("options") or {}).get("D", "").strip(),
    )
    return "|".join(core).casefold()


def _pick_adaptive_question(grouped, section_name, target_difficulty, asked_keys, rng):
    order = ["easy", "medium", "hard"]
    target = (target_difficulty or "").strip().lower()
    if target not in order:
        target = "medium"

    def available(diff_value):
        out = []
        for q in grouped.get(section_name, []):
            if (q.get("difficulty") or "").strip().lower() != diff_value:
                continue
            key = _question_dedupe_key(q)
            if key in asked_keys:
                continue
            out.append(q)
        return out

    # Try target, then adjacent, then any.
    candidates = available(target)
    if not candidates:
        idx = order.index(target)
        neighbors = []
        if idx - 1 >= 0:
            neighbors.append(order[idx - 1])
        if idx + 1 < len(order):
            neighbors.append(order[idx + 1])
        for diff_value in neighbors:
            candidates = available(diff_value)
            if candidates:
                break

    if not candidates:
        any_candidates = []
        for q in grouped.get(section_name, []):
            key = _question_dedupe_key(q)
            if key in asked_keys:
                continue
            any_candidates.append(q)
        candidates = any_candidates

    if not candidates:
        return None

    return rng.choice(candidates)


def _compute_weighted_score(served_questions, answers, answer_key):
    total_weight = 0.0
    got_weight = 0.0
    for q in served_questions:
        qid = q.get("id")
        if not qid:
            continue
        try:
            w = float((q.get("weight") or "1").strip() or "1")
        except Exception:
            w = 1.0
        total_weight += w
        expected = (answer_key or {}).get(qid)
        given = (answers or {}).get(qid)
        if expected and given and str(given).strip().upper() == str(expected).strip().upper():
            got_weight += w
    if total_weight <= 0:
        return 0.0
    return round((got_weight / total_weight) * 100.0, 2)


def _adaptive_remaining_total(remaining_plan):
    try:
        return sum(int(v) for v in (remaining_plan or {}).values())
    except Exception:
        return 0


def _adaptive_build_payload(attempt, include_answer_key=False):
    remaining = json.loads(attempt.remaining_plan_json or "{}")
    served = json.loads(attempt.served_questions_json or "[]")
    payload = {
        "attempt_id": attempt.id,
        "created_at": attempt.created_at.isoformat() + "Z",
        "submitted_at": attempt.submitted_at.isoformat() + "Z" if attempt.submitted_at else None,
        "total_questions": attempt.total_questions,
        "answered": len(json.loads(attempt.answers_json or "{}")),
        "remaining": _adaptive_remaining_total(remaining),
        "remaining_by_section": remaining,
        "correct_count": attempt.correct_count,
        "score_pct": attempt.score_pct,
        "weighted_score_pct": attempt.weighted_score_pct,
        "section_breakdown": json.loads(attempt.section_breakdown_json or "{}"),
        "pending_question_id": attempt.pending_question_id,
        "served_count": len(served),
    }
    if include_answer_key:
        payload["answer_key"] = json.loads(attempt.answer_key_json or "{}")
    return payload


@app.route("/api/mock-test/csv/adaptive/start", methods=["POST"])
def api_csv_adaptive_start():
    if "student_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    # Ensure CSV is present/valid.
    base = _generate_mock_test_from_csv(total_questions=30)
    if not base:
        return jsonify({"error": "questions.csv not found or no valid questions available."}), 400
    if isinstance(base, dict) and base.get("error"):
        return jsonify(base), 400

    plan = _build_section_plan(30)
    difficulty_state = {k: "medium" for k in plan.keys()}
    seed = secrets.randbelow(2**31 - 1)

    attempt = CsvAdaptiveTestAttempt(
        student_id=session["student_id"],
        total_questions=30,
        section_plan_json=json.dumps(plan, ensure_ascii=False),
        remaining_plan_json=json.dumps(plan, ensure_ascii=False),
        difficulty_state_json=json.dumps(difficulty_state, ensure_ascii=False),
        seed=seed,
        asked_keys_json=json.dumps([], ensure_ascii=False),
        served_questions_json=json.dumps([], ensure_ascii=False),
        answer_key_json=json.dumps({}, ensure_ascii=False),
        answers_json=json.dumps({}, ensure_ascii=False),
        correct_count=0,
    )
    db.session.add(attempt)
    db.session.commit()

    return jsonify({"attempt_id": attempt.id, "section_plan": plan, "difficulty_state": difficulty_state})


@app.route("/api/mock-test/csv/adaptive/next", methods=["GET"])
def api_csv_adaptive_next():
    if "student_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    attempt_id = _safe_int(request.args.get("attempt_id"))
    if attempt_id is None:
        return jsonify({"error": "attempt_id is required."}), 400

    attempt = CsvAdaptiveTestAttempt.query.filter_by(id=attempt_id, student_id=session["student_id"]).first()
    if not attempt:
        return jsonify({"error": "Attempt not found."}), 404
    if attempt.submitted_at is not None:
        return jsonify({**_adaptive_build_payload(attempt), "status": "completed"}), 200

    # If there is a pending question not answered yet, return it again.
    answers = json.loads(attempt.answers_json or "{}")
    if attempt.pending_question_id and attempt.pending_question_id not in answers:
        served = json.loads(attempt.served_questions_json or "[]")
        pending = next((q for q in served if q.get("id") == attempt.pending_question_id), None)
        if pending:
            q_out = dict(pending)
            q_out.pop("answer", None)
            return jsonify({"question": q_out, **_adaptive_build_payload(attempt)}), 200

    remaining = json.loads(attempt.remaining_plan_json or "{}")
    if _adaptive_remaining_total(remaining) <= 0:
        attempt.submitted_at = datetime.utcnow()
        db.session.commit()
        return jsonify({**_adaptive_build_payload(attempt), "status": "completed"}), 200

    difficulty_state = json.loads(attempt.difficulty_state_json or "{}")
    asked_keys = set(json.loads(attempt.asked_keys_json or "[]"))
    served_questions = json.loads(attempt.served_questions_json or "[]")
    answer_key = json.loads(attempt.answer_key_json or "{}")

    # Load CSV pool and group by section.
    candidates = []
    for p in (
        os.path.join(app.root_path, "questions.csv"),
        os.path.join(os.getcwd(), "questions.csv"),
    ):
        if os.path.exists(p):
            candidates = _load_questions_from_csv(p)
            if candidates:
                break
    if not candidates:
        return jsonify({"error": "questions.csv not found or no valid questions available."}), 400

    grouped = {"Aptitude": [], "Logical": [], "Technical": [], "Coding": []}
    for q in candidates:
        grouped.setdefault(q["section"], []).append(q)

    # Pick next section: the one with highest remaining, tie-broken randomly.
    max_remaining = max(int(v) for v in remaining.values() if int(v) > 0)
    candidate_sections = [k for k, v in remaining.items() if int(v) == max_remaining and int(v) > 0]
    rng = random.Random(int(attempt.seed) + len(served_questions))
    section_name = rng.choice(candidate_sections)

    target_diff = difficulty_state.get(section_name) or "medium"
    picked = _pick_adaptive_question(grouped, section_name, target_diff, asked_keys, rng)
    if not picked:
        return jsonify({"error": f"Insufficient CSV questions for section {section_name}."}), 400

    qid = secrets.token_hex(6)
    picked_out = dict(picked)
    picked_out["id"] = qid
    picked_out["selected_section"] = section_name

    served_questions.append({**picked_out, "answer": picked.get("answer")})
    answer_key[qid] = picked.get("answer")
    asked_keys.add(_question_dedupe_key(picked))

    remaining[section_name] = int(remaining.get(section_name, 0)) - 1

    attempt.pending_question_id = qid
    attempt.remaining_plan_json = json.dumps(remaining, ensure_ascii=False)
    attempt.asked_keys_json = json.dumps(sorted(list(asked_keys)), ensure_ascii=False)
    attempt.served_questions_json = json.dumps(served_questions, ensure_ascii=False)
    attempt.answer_key_json = json.dumps(answer_key, ensure_ascii=False)
    db.session.commit()

    q_out = dict(picked_out)
    q_out.pop("answer", None)
    return jsonify({"question": q_out, **_adaptive_build_payload(attempt)}), 200


@app.route("/api/mock-test/csv/adaptive/answer", methods=["POST"])
def api_csv_adaptive_answer():
    if "student_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    attempt_id = _safe_int(data.get("attempt_id"))
    question_id = (data.get("question_id") or "").strip()
    answer = (data.get("answer") or "").strip().upper()

    if attempt_id is None or not question_id or answer not in {"A", "B", "C", "D"}:
        return jsonify({"error": "Invalid payload. Expected {attempt_id, question_id, answer(A-D)}."}), 400

    attempt = CsvAdaptiveTestAttempt.query.filter_by(id=attempt_id, student_id=session["student_id"]).first()
    if not attempt:
        return jsonify({"error": "Attempt not found."}), 404
    if attempt.submitted_at is not None:
        return jsonify({**_adaptive_build_payload(attempt), "status": "completed"}), 200

    served = json.loads(attempt.served_questions_json or "[]")
    q = next((item for item in served if item.get("id") == question_id), None)
    if not q:
        return jsonify({"error": "Unknown question_id for this attempt."}), 400

    answers = json.loads(attempt.answers_json or "{}")
    if question_id in answers:
        return jsonify({"error": "Question already answered."}), 409

    answer_key = json.loads(attempt.answer_key_json or "{}")
    expected = (answer_key.get(question_id) or "").strip().upper()
    is_correct = expected and answer == expected

    # Update adaptive difficulty state for that section.
    difficulty_state = json.loads(attempt.difficulty_state_json or "{}")
    section = q.get("section") or ""
    current_diff = (difficulty_state.get(section) or "medium").strip().lower()
    difficulty_state[section] = _difficulty_step(current_diff, bool(is_correct))

    # Store answer and update score.
    answers[question_id] = answer
    attempt.answers_json = json.dumps(answers, ensure_ascii=False)
    if is_correct:
        attempt.correct_count = int(attempt.correct_count or 0) + 1

    # Recompute breakdown + score.
    public_questions = []
    for item in served:
        item2 = dict(item)
        item2.pop("answer", None)
        public_questions.append(item2)
    scored = _score_csv_attempt(public_questions, answer_key, answers)
    attempt.score_pct = scored["score_pct"]
    attempt.section_breakdown_json = json.dumps(scored["breakdown"], ensure_ascii=False)
    attempt.weighted_score_pct = _compute_weighted_score(served, answers, answer_key)

    attempt.difficulty_state_json = json.dumps(difficulty_state, ensure_ascii=False)

    if attempt.pending_question_id == question_id:
        attempt.pending_question_id = None

    # Finish when all answered.
    if len(answers) >= int(attempt.total_questions or 30):
        attempt.submitted_at = datetime.utcnow()

    db.session.commit()

    # Persist result row for analytics when completed.
    if attempt.submitted_at is not None:
        public_questions = []
        for item in served:
            item2 = dict(item)
            item2.pop("answer", None)
            public_questions.append(item2)
        final_score = _score_csv_attempt(public_questions, answer_key, answers)
        _persist_student_mock_result(
            student_id=session["student_id"],
            source="csv_adaptive",
            attempt_id=attempt.id,
            score_obj=final_score,
            submitted_at=attempt.submitted_at,
        )

    return jsonify(
        {
            "attempt_id": attempt.id,
            "question_id": question_id,
            "correct": bool(is_correct),
            "expected": expected if "admin_id" in session else None,
            "difficulty_state": difficulty_state,
            **_adaptive_build_payload(attempt),
            "status": "completed" if attempt.submitted_at else "in_progress",
        }
    )


def _score_csv_attempt(questions, answer_key, submitted_answers):
    submitted = submitted_answers or {}
    section_totals = {"Aptitude": 0, "Logical": 0, "Technical": 0, "Coding": 0}
    section_correct = {"Aptitude": 0, "Logical": 0, "Technical": 0, "Coding": 0}
    difficulty_totals = {"easy": 0, "medium": 0, "hard": 0, "": 0}
    difficulty_correct = {"easy": 0, "medium": 0, "hard": 0, "": 0}

    correct = 0
    for q in questions:
        qid = q.get("id")
        if not qid:
            continue
        section = q.get("section") or ""
        difficulty = (q.get("difficulty") or "").strip().lower()
        expected = (answer_key or {}).get(qid)
        given = (submitted.get(qid) or "").strip().upper()

        if section in section_totals:
            section_totals[section] += 1
        difficulty_totals[difficulty if difficulty in difficulty_totals else ""] += 1

        if expected and given and given == expected:
            correct += 1
            if section in section_correct:
                section_correct[section] += 1
            difficulty_correct[difficulty if difficulty in difficulty_correct else ""] += 1

    total = sum(section_totals.values())
    score_pct = round((correct / total * 100.0), 2) if total else 0.0
    breakdown = {
        "by_section": {
            k: {"correct": section_correct[k], "total": section_totals[k]}
            for k in section_totals
        },
        "by_difficulty": {
            k or "unknown": {"correct": difficulty_correct[k], "total": difficulty_totals[k]}
            for k in difficulty_totals
            if difficulty_totals[k] > 0
        },
    }
    return {"correct": correct, "total": total, "score_pct": score_pct, "breakdown": breakdown}


def _persist_student_mock_result(student_id, source, attempt_id, score_obj, submitted_at=None):
    # Best-effort dedupe: do not write twice for the same attempt.
    if attempt_id is not None:
        existing = StudentMockTestResult.query.filter_by(
            student_id=student_id, source=source, attempt_id=attempt_id
        ).first()
        if existing:
            return existing

    by_section = (score_obj or {}).get("breakdown", {}).get("by_section", {})
    result = StudentMockTestResult(
        student_id=student_id,
        source=source,
        attempt_id=attempt_id,
        score=int((score_obj or {}).get("correct") or 0),
        total_questions=int((score_obj or {}).get("total") or 0),
        aptitude_score=int((by_section.get("Aptitude") or {}).get("correct") or 0),
        logical_score=int((by_section.get("Logical") or {}).get("correct") or 0),
        technical_score=int((by_section.get("Technical") or {}).get("correct") or 0),
        coding_score=int((by_section.get("Coding") or {}).get("correct") or 0),
        submitted_at=submitted_at or datetime.utcnow(),
    )
    db.session.add(result)
    db.session.commit()
    return result


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

    dialect = getattr(db.engine, "dialect", None)
    dialect_name = getattr(dialect, "name", "") if dialect else ""
    random_order_fn = func.rand if dialect_name in {"mysql", "mariadb"} else func.random

    try:
        for section, required in plan.items():
            section_rows = (
                QuestionBank.query.filter_by(section=section).filter(
                    QuestionBank.correct_answer.in_(("A", "B", "C", "D"))
                )
                .order_by(random_order_fn())
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
            top_up = query.order_by(random_order_fn()).limit(missing_count).all()
            selected.extend(top_up)
            selected_ids.update(row.id for row in top_up)
    except Exception:
        app.logger.exception("Failed to pick questions from QuestionBank.")
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

    dialect = getattr(db.engine, "dialect", None)
    dialect_name = getattr(dialect, "name", "") if dialect else ""
    random_order_fn = func.rand if dialect_name in {"mysql", "mariadb"} else func.random

    for section, required in plan.items():
        rows = (
            Question.query.filter_by(test_id=test_id, section=section)
            .order_by(random_order_fn())
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
        top_up = query.order_by(random_order_fn()).limit(missing_count).all()
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
        return False, "Use 8+ chars with uppercase, lowercase, number, and special character."
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

# (Fraud detection module is implemented in fraud_detector.py)

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

    # Hide notifications for placement drives that have been deleted.
    existing_company_names = {(p.cmpname or "").strip().lower() for p in placements}
    raw_notifications = (
        db.session.query(Notification)
        .outerjoin(Placement, Placement.placeid == Notification.placement_id)
        .filter(Notification.student_id == session["student_id"])
        .filter(or_(Notification.placement_id.is_(None), Placement.placeid.isnot(None)))
        .order_by(Notification.date.desc())
        .limit(40)
        .all()
    )

    deleted_nids = []
    notifications = []
    new_drive_re = re.compile(r"^New placement drive scheduled:\s*(.+?)\s*\(")
    status_re = re.compile(r"^Your application status for\s+(.+?)\s+has been updated", re.IGNORECASE)
    for note in raw_notifications:
        msg = (note.msgtext or "").strip()
        company = ""
        m1 = new_drive_re.match(msg)
        if m1:
            company = (m1.group(1) or "").strip()
        else:
            m2 = status_re.match(msg)
            if m2:
                company = (m2.group(1) or "").strip()

        if company and company.lower() not in existing_company_names:
            deleted_nids.append(note.nid)
            continue

        notifications.append(note)
        if len(notifications) >= 10:
            break

    if deleted_nids:
        Notification.query.filter(
            Notification.student_id == session["student_id"],
            Notification.nid.in_(deleted_nids),
        ).delete(synchronize_session=False)
        db.session.commit()
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
    """
    Check if a student is eligible for a placement.
    Safely handles missing or None student data by treating it as ineligible.
    """

    if placement is None or student is None:
        return False
    
    # CGPA check - treat None as not eligible
    student_cgpa = getattr(student, 'cgpa', None)
    if student_cgpa is None:
        return False
    if placement.min_cgpa and student_cgpa < placement.min_cgpa:
        return False

    # Department check - treat None as not eligible
    student_department = getattr(student, 'department', None)
    if student_department is None:
        return False
    if placement.department and student_department != placement.department:
        return False

    # Year check - treat None as not eligible
    student_year = getattr(student, 'year', None)
    if student_year is None:
        return False
    if placement.allowed_year and student_year != placement.allowed_year:
        return False

    # Arrears check - use default 0 if None
    student_arrears = getattr(student, 'number_of_arrears', None) or 0
    if placement.max_arrears is not None and student_arrears > placement.max_arrears:
        return False

    # Skills check (minimum 60% match of required skills)
    required_skills = set()
    required_skills.update(_split_skill_tokens(getattr(placement, "required_programming_languages", None)))
    required_skills.update(_split_skill_tokens(getattr(placement, "required_technical_skills", None)))
    required_skills.update(_split_skill_tokens(getattr(placement, "required_tools", None)))

    # Handle missing student skills - treat None as empty set (no skills)
    student_programming_langs = getattr(student, 'programming_languages', None) or ""
    student_technical_skills = getattr(student, 'technical_skills', None) or ""
    student_tools = getattr(student, 'tools_technologies', None) or ""
    
    student_skills = set()
    student_skills.update(_split_skill_tokens(student_programming_langs))
    student_skills.update(_split_skill_tokens(student_technical_skills))
    student_skills.update(_split_skill_tokens(student_tools))

    # If required skills are specified, check minimum 60% match
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

    # Check if profile is complete
    if not student.has_complete_profile():
        missing = []
        try:
            missing = student.missing_profile_fields() or []
        except Exception:
            missing = []
        suffix = f" Missing: {', '.join(missing)}." if missing else ""
        flash(f"Please complete your profile before applying to placements.{suffix}", "warning")
        return redirect(url_for("edit_student", student_id=student.st_id))

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

    pdf_enabled = _try_get_pdf_reader() is not None
    docx_enabled = _try_get_docx_document() is not None

    result = None
    original_resume = None
    target_role = ""
    if request.method == "POST":
        target_role = (request.form.get("target_role") or "").strip()
        uploaded_file = request.files.get("resume_file")

        resume_text, error = _extract_resume_text(uploaded_file)
        if error:
            flash(error, "danger")
            return render_template(
                "resume_enhancer.html",
                student=student,
                original_resume=None,
                result=None,
                target_role=target_role,
                pdf_enabled=pdf_enabled,
                docx_enabled=docx_enabled,
            )

        result = _enhance_resume(resume_text, target_role)
        if result is None:
            flash("No content found in uploaded resume.", "danger")
            return render_template(
                "resume_enhancer.html",
                student=student,
                original_resume=None,
                result=None,
                target_role=target_role,
                pdf_enabled=pdf_enabled,
                docx_enabled=docx_enabled,
            )

        original_resume = resume_text
        flash("Resume enhanced successfully.", "success")

    return render_template(
        "resume_enhancer.html",
        student=student,
        original_resume=original_resume,
        result=result,
        target_role=target_role,
        pdf_enabled=pdf_enabled,
        docx_enabled=docx_enabled,
    )


@app.route("/resume_download", methods=["POST"])
def resume_download():
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    content = request.form.get("content", "")
    filename = request.form.get("filename", "enhanced_resume.txt")
    safe_name = secure_filename(filename) or "enhanced_resume.txt"
    if not safe_name.lower().endswith(".txt"):
        safe_name = f"{safe_name}.txt"

    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment; filename={safe_name}"},
    )


@app.route("/api/mock-test/from-csv", methods=["GET"])
def api_mock_test_from_csv():
    if "student_id" not in session and "admin_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    payload = _generate_mock_test_from_csv(total_questions=30)
    if not payload:
        return jsonify({"error": "questions.csv not found or no valid questions available."}), 400
    if isinstance(payload, dict) and payload.get("error"):
        return jsonify(payload), 400

    include_answers = (request.args.get("include_answers") or "").strip() in {"1", "true", "yes"}
    if include_answers and "admin_id" in session:
        return jsonify(payload)

    payload.pop("answer_key", None)
    return jsonify(payload)


@app.route("/api/mock-test/csv/generate", methods=["POST"])
def api_csv_mock_generate():
    if "student_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    mode = (data.get("mode") or request.args.get("mode") or "full").strip().lower()
    if mode not in {"full", "practice"}:
        mode = "full"

    payload = _generate_mock_test_from_csv(total_questions=30, mode=mode)
    if not payload:
        return jsonify({"error": "questions.csv not found or no valid questions available."}), 400
    if isinstance(payload, dict) and payload.get("error"):
        return jsonify(payload), 400

    attempt = CsvMockTestAttempt(
        student_id=session["student_id"],
        total_questions=payload.get("total_questions") or 30,
        section_plan_json=json.dumps(payload.get("section_plan") or {}, ensure_ascii=False),
        questions_json=json.dumps(payload.get("questions") or [], ensure_ascii=False),
        answer_key_json=json.dumps(payload.get("answer_key") or {}, ensure_ascii=False),
    )
    db.session.add(attempt)
    db.session.commit()

    payload.pop("answer_key", None)
    payload["attempt_id"] = attempt.id
    payload["mode"] = mode
    return jsonify(payload)


@app.route("/api/mock-test/csv/submit", methods=["POST"])
def api_csv_mock_submit():
    if "student_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    attempt_id = _safe_int(data.get("attempt_id"))
    submitted_answers = data.get("answers") or {}
    if attempt_id is None or not isinstance(submitted_answers, dict):
        return jsonify({"error": "Invalid payload. Expected {attempt_id, answers{question_id: option}}."}), 400

    attempt = CsvMockTestAttempt.query.filter_by(id=attempt_id, student_id=session["student_id"]).first()
    if not attempt:
        return jsonify({"error": "Attempt not found."}), 404

    if attempt.submitted_at is not None:
        return jsonify(
            {
                "attempt_id": attempt.id,
                "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
                "correct": attempt.correct_count,
                "total": attempt.total_questions,
                "score_pct": attempt.score_pct,
                "breakdown": json.loads(attempt.section_breakdown_json or "{}"),
                "status": "already_submitted",
            }
        )

    try:
        questions = json.loads(attempt.questions_json or "[]")
        answer_key = json.loads(attempt.answer_key_json or "{}")
    except Exception:
        return jsonify({"error": "Stored attempt data is corrupted."}), 500

    score = _score_csv_attempt(questions, answer_key, submitted_answers)

    attempt.submitted_at = datetime.utcnow()
    attempt.answers_json = json.dumps(submitted_answers, ensure_ascii=False)
    attempt.correct_count = score["correct"]
    attempt.score_pct = score["score_pct"]
    attempt.section_breakdown_json = json.dumps(score["breakdown"], ensure_ascii=False)
    db.session.commit()

    # Persist result row for analytics (MySQL-friendly table).
    source = "csv_full"
    try:
        mode = (data.get("mode") or "").strip().lower()
        if mode == "practice":
            source = "csv_practice"
    except Exception:
        pass
    _persist_student_mock_result(
        student_id=session["student_id"],
        source=source,
        attempt_id=attempt.id,
        score_obj=score,
        submitted_at=attempt.submitted_at,
    )

    return jsonify(
        {
            "attempt_id": attempt.id,
            "submitted_at": attempt.submitted_at.isoformat() + "Z",
            "correct": score["correct"],
            "total": score["total"],
            "score_pct": score["score_pct"],
            "breakdown": score["breakdown"],
        }
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


@app.route("/mock_tests/csv")
def csv_mock_test_page():
    if "student_id" not in session:
        return redirect(url_for("student_login"))
    student = Student.query.filter_by(st_id=session["student_id"]).first()
    if not student:
        flash("Student profile not found.", "danger")
        return redirect(url_for("student_login"))

    mode = (request.args.get("mode") or "").strip().lower()
    if mode not in {"practice"}:
        mode = "full"

    return render_template("csv_mock_test.html", student=student, mode=mode)


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

    # Persist result row for analytics table (MySQL-friendly).
    score_obj = {
        "correct": score,
        "total": len(questions),
        "breakdown": {
            "by_section": {
                "Aptitude": {"correct": section_scores["Aptitude"], "total": section_totals["Aptitude"]},
                "Logical": {"correct": section_scores["Logical"], "total": section_totals["Logical"]},
                "Technical": {"correct": section_scores["Technical"], "total": section_totals["Technical"]},
                "Coding": {"correct": section_scores["Coding"], "total": section_totals["Coding"]},
            }
        },
    }
    _persist_student_mock_result(
        student_id=session["student_id"],
        source="db_test",
        attempt_id=result.id,
        score_obj=score_obj,
        submitted_at=result.submitted_at,
    )

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
    # Prefer unified results table (covers CSV + DB mock tests).
    student_id = int(session["student_id"])
    result_rows = (
        db.session.query(StudentMockTestResult)
        .filter(StudentMockTestResult.student_id == student_id)
        .order_by(StudentMockTestResult.submitted_at.desc(), StudentMockTestResult.id.desc())
        .all()
    )

    rows = []
    if result_rows:
        from types import SimpleNamespace

        db_attempt_ids = [
            r.attempt_id
            for r in result_rows
            if (r.source == "db_test" and r.attempt_id is not None)
        ]
        test_by_attempt_id = {}
        if db_attempt_ids:
            db_rows = (
                db.session.query(TestResult, MockTest)
                .outerjoin(MockTest, MockTest.id == TestResult.test_id)
                .filter(TestResult.id.in_(db_attempt_ids))
                .all()
            )
            for test_result, mock_test in db_rows:
                test_by_attempt_id[test_result.id] = mock_test

        def _title_for_source(src: str) -> str:
            key = (src or "").strip().lower()
            if key == "csv_full":
                return "CSV Mock Test (Full)"
            if key == "csv_practice":
                return "CSV Mock Test (Practice)"
            if key == "csv_adaptive":
                return "CSV Mock Test (Adaptive)"
            if key == "db_test":
                return "DB Mock Test"
            return src or "Mock Test"

        for r in result_rows:
            test_obj = None
            if r.source == "db_test" and r.attempt_id is not None:
                test_obj = test_by_attempt_id.get(r.attempt_id)
                if test_obj is None:
                    test_obj = SimpleNamespace(title="DB Mock Test")
            else:
                test_obj = SimpleNamespace(title=_title_for_source(r.source))
            rows.append((r, test_obj))

    # Backward-compatible fallback.
    if not rows:
        rows = (
            db.session.query(TestResult, MockTest)
            .outerjoin(MockTest, MockTest.id == TestResult.test_id)
            .filter(TestResult.student_id == student_id)
            .order_by(TestResult.submitted_at.desc(), TestResult.id.desc())
            .all()
        )

    return render_template("test_history.html", rows=rows)



@app.route("/analytics_report")
def analytics_report():
    if "student_id" not in session:
        return redirect(url_for("student_login"))

    student = Student.query.filter_by(st_id=session["student_id"]).first()
    return render_template(
        "analytics_report.html",
        student=student,
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
        internship_experience = (request.form.get("internship_experience") or "").strip()
        certifications = (request.form.get("certifications") or "").strip()
        resume_file = request.files.get("resume_pdf")

        form_data = {
            "name": name,
            "register_number": register_number,
            "email": email,
            "phone": (request.form.get("phone") or "").strip(),
            "department": (request.form.get("department") or "").strip(),
            "number_of_arrears": (request.form.get("number_of_arrears") or "").strip(),
            "year": (request.form.get("year") or "").strip(),
            "cgpa": (request.form.get("cgpa") or "").strip(),
            "tenth_percentage": (request.form.get("tenth_percentage") or "").strip(),
            "twelfth_percentage": (request.form.get("twelfth_percentage") or "").strip(),
            "technical_skills": technical_skills,
            "programming_languages": programming_languages,
            "tools_technologies": tools_technologies,
            "projects": projects,
            "internship_experience": internship_experience,
            "certifications": certifications,
        }

        if len(name) < 3:
            flash("Name must be at least 3 characters.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        if not re.fullmatch(r"CEC\d{2}[A-Z]{2}\d{3}", register_number):
            flash("Register number must be like CEC23CS027.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        if "@" not in email:
            flash("Enter a valid email address.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        register_owner = Student.query.filter_by(register_number=register_number).first()
        if register_owner and register_owner.st_id != student.st_id:
            flash("That register number is already used by another account.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        email_owner = Student.query.filter_by(email=email).first()
        if email_owner and email_owner.st_id != student.st_id:
            flash("That email is already used by another account.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        year = _safe_int(request.form.get("year"))
        cgpa = _safe_float(request.form.get("cgpa"))
        tenth_percentage = _safe_float(request.form.get("tenth_percentage"))
        twelfth_percentage = _safe_float(request.form.get("twelfth_percentage"))
        number_of_arrears = _safe_int(request.form.get("number_of_arrears"))

        if request.form.get("year") and year is None:
            flash("Year must be a valid number.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        if request.form.get("cgpa") and cgpa is None:
            flash("CGPA must be a valid number.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        if not request.form.get("tenth_percentage"):
            flash("10th percentage is required.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        if tenth_percentage is None:
            flash("10th percentage must be a valid number.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        if not request.form.get("twelfth_percentage"):
            flash("12th percentage is required.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        if twelfth_percentage is None:
            flash("12th percentage must be a valid number.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        if request.form.get("number_of_arrears") and number_of_arrears is None:
            flash("Number of arrears must be a valid number.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        if cgpa is not None and (cgpa < 0 or cgpa > 10):
            flash("CGPA must be between 0 and 10.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        if not (0 <= tenth_percentage <= 100):
            flash("10th percentage must be between 0 and 100.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        if not (0 <= twelfth_percentage <= 100):
            flash("12th percentage must be between 0 and 100.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        if number_of_arrears is not None and number_of_arrears < 0:
            flash("Number of arrears cannot be negative.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

        if resume_file and resume_file.filename and not _is_allowed_resume_file(resume_file.filename):
            flash("Resume must be a PDF file.", "danger")
            return render_template("edit_student.html", student=student, form_data=form_data)

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

        current_password = (request.form.get("current_password") or "").strip()
        new_password = (request.form.get("new_password") or "").strip()
        confirm_password = (request.form.get("confirm_password") or "").strip()

        # Only treat this as a password-change attempt if user provides a new password (or confirmation).
        # This avoids false warnings when browsers autofill the current password field.
        if new_password or confirm_password:
            if not current_password or not new_password or not confirm_password:
                flash("To change password, fill current, new, and confirm password.", "danger")
                return render_template("edit_student.html", student=student, form_data=form_data)

            if not check_password_hash(student.password, current_password):
                flash("Current password is incorrect.", "danger")
                return render_template("edit_student.html", student=student, form_data=form_data)

            if new_password != confirm_password:
                flash("New password and confirm password do not match.", "danger")
                return render_template("edit_student.html", student=student, form_data=form_data)

            is_strong, password_message = _is_strong_password(new_password)
            if not is_strong:
                flash(password_message, "danger")
                return render_template("edit_student.html", student=student, form_data=form_data)

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

    return render_template("edit_student.html", student=student, form_data=None)


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
    total_companies = db.session.query(func.count(Company.id)).scalar() or 0
    students = Student.query.all()
    placements = Placement.query.all()
    placement_applications = (
        PlacementApplication.query.order_by(PlacementApplication.app_id.desc()).limit(50).all()
    )

    return render_template(
        "admindash.html",
        total_students=total_students,
        total_placements=total_placements,
        total_applications=total_applications,
        total_companies=total_companies,
        students=students,
        placements=placements,
        placement_applications=placement_applications,
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
    writer.writerow(["Generated At (UTC)", now.isoformat()])

    csv_data = buffer.getvalue()
    buffer.close()

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=system_analytics_{now.strftime('%Y%m%d_%H%M%S')}.csv"},
    )


# =========================
# ADMIN: STUDENT MANAGEMENT
# =========================
@app.route("/admin/students", methods=["GET"])
def admin_students():
    redirect_response = _admin_login_redirect()
    if redirect_response:
        return redirect_response

    q = (request.args.get("q") or "").strip()
    query = Student.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Student.name.ilike(like),
                Student.email.ilike(like),
                Student.register_number.ilike(like),
            )
        )

    students = query.order_by(Student.created_at.desc(), Student.st_id.desc()).limit(500).all()
    return render_template("admin_students.html", students=students, q=q)


@app.route("/admin/students/<int:student_id>/edit", methods=["GET", "POST"])
def admin_edit_student(student_id: int):
    redirect_response = _admin_login_redirect()
    if redirect_response:
        return redirect_response

    student = Student.query.filter_by(st_id=student_id).first()
    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for("admin_students"))

    if request.method == "POST":
        register_number = (request.form.get("register_number") or "").strip().upper()
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        phone = (request.form.get("phone") or "").strip() or None
        department = (request.form.get("department") or "").strip() or None

        year_raw = (request.form.get("year") or "").strip()
        cgpa_raw = (request.form.get("cgpa") or "").strip()
        tenth_raw = (request.form.get("tenth_percentage") or "").strip()
        twelfth_raw = (request.form.get("twelfth_percentage") or "").strip()
        arrears_raw = (request.form.get("number_of_arrears") or "").strip()

        technical_skills = (request.form.get("technical_skills") or "").strip() or None
        programming_languages = (request.form.get("programming_languages") or "").strip() or None
        tools_technologies = (request.form.get("tools_technologies") or "").strip() or None
        projects = (request.form.get("projects") or "").strip() or None
        internship_experience = (request.form.get("internship_experience") or "").strip() or None
        certifications = (request.form.get("certifications") or "").strip() or None

        new_password = (request.form.get("new_password") or "").strip()

        form_data = {
            "register_number": register_number,
            "name": name,
            "email": email,
            "phone": phone or "",
            "department": department or "",
            "year": year_raw,
            "cgpa": cgpa_raw,
            "tenth_percentage": tenth_raw,
            "twelfth_percentage": twelfth_raw,
            "number_of_arrears": arrears_raw,
            "technical_skills": technical_skills or "",
            "programming_languages": programming_languages or "",
            "tools_technologies": tools_technologies or "",
            "projects": projects or "",
            "internship_experience": internship_experience or "",
            "certifications": certifications or "",
        }

        if len(name) < 3:
            flash("Name must be at least 3 characters.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)

        if not re.fullmatch(r"CEC\d{2}[A-Z]{2}\d{3}", register_number):
            flash("Register number must be like CEC23CS027.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)

        if "@" not in email:
            flash("Enter a valid email address.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)

        register_owner = Student.query.filter_by(register_number=register_number).first()
        if register_owner and register_owner.st_id != student.st_id:
            flash("That register number is already used by another account.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)

        email_owner = Student.query.filter_by(email=email).first()
        if email_owner and email_owner.st_id != student.st_id:
            flash("That email is already used by another account.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)

        year = _safe_int(year_raw) if year_raw else None
        cgpa = _safe_float(cgpa_raw) if cgpa_raw else None
        tenth_percentage = _safe_float(tenth_raw) if tenth_raw else None
        twelfth_percentage = _safe_float(twelfth_raw) if twelfth_raw else None
        number_of_arrears = _safe_int(arrears_raw) if arrears_raw else None

        if year_raw and year is None:
            flash("Year must be a valid number.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)
        if cgpa_raw and cgpa is None:
            flash("CGPA must be a valid number.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)
        if tenth_raw and tenth_percentage is None:
            flash("10th percentage must be a valid number.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)
        if twelfth_raw and twelfth_percentage is None:
            flash("12th percentage must be a valid number.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)
        if arrears_raw and number_of_arrears is None:
            flash("Number of arrears must be a valid number.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)

        if cgpa is not None and (cgpa < 0 or cgpa > 10):
            flash("CGPA must be between 0 and 10.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)
        if tenth_percentage is not None and not (0 <= tenth_percentage <= 100):
            flash("10th percentage must be between 0 and 100.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)
        if twelfth_percentage is not None and not (0 <= twelfth_percentage <= 100):
            flash("12th percentage must be between 0 and 100.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)
        if number_of_arrears is not None and number_of_arrears < 0:
            flash("Number of arrears cannot be negative.", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)

        if new_password:
            is_strong, password_message = _is_strong_password(new_password)
            if not is_strong:
                flash(password_message, "danger")
                return render_template("admin_edit_student.html", student=student, form_data=form_data)

        try:
            student.register_number = register_number
            student.name = name
            student.email = email
            student.phone = phone
            student.department = department
            if year_raw:
                student.year = year
            if cgpa_raw:
                student.cgpa = cgpa
            if tenth_raw:
                student.tenth_percentage = tenth_percentage
            if twelfth_raw:
                student.twelfth_percentage = twelfth_percentage
            if arrears_raw:
                student.number_of_arrears = number_of_arrears

            student.technical_skills = technical_skills
            student.programming_languages = programming_languages
            student.tools_technologies = tools_technologies
            student.projects = projects
            student.internship_experience = internship_experience
            student.certifications = certifications

            if new_password:
                student.password = generate_password_hash(new_password)

            db.session.commit()
            flash("Student updated.", "success")
            return redirect(url_for("admin_students"))
        except Exception as exc:
            db.session.rollback()
            flash(f"Unable to update student: {exc}", "danger")
            return render_template("admin_edit_student.html", student=student, form_data=form_data)

    return render_template("admin_edit_student.html", student=student, form_data=None)


@app.route("/admin/students/delete/<int:student_id>", methods=["POST"])
def admin_delete_student(student_id: int):
    redirect_response = _admin_login_redirect()
    if redirect_response:
        return redirect_response

    student = Student.query.filter_by(st_id=student_id).first()
    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for("admin_students"))

    try:
        PlacementApplication.query.filter_by(student_id=student.st_id).delete(synchronize_session=False)
        Notification.query.filter_by(student_id=student.st_id).delete(synchronize_session=False)
        Resume.query.filter_by(student_id=student.st_id).delete(synchronize_session=False)
        StudentMockTestResult.query.filter_by(student_id=student.st_id).delete(synchronize_session=False)
        CsvMockTestAttempt.query.filter_by(student_id=student.st_id).delete(synchronize_session=False)
        CsvAdaptiveTestAttempt.query.filter_by(student_id=student.st_id).delete(synchronize_session=False)
        TestResult.query.filter_by(student_id=student.st_id).delete(synchronize_session=False)
        db.session.delete(student)
        db.session.commit()
        flash("Student deleted.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Unable to delete student: {exc}", "danger")

    return redirect(url_for("admin_students"))


# =========================
# COMPANIES (Fraud Analysis)
# =========================
@app.route("/admin/companies", methods=["GET"])
def admin_companies():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    companies = Company.query.order_by(Company.created_at.desc(), Company.id.desc()).limit(100).all()
    latest_by_company = {}
    if companies:
        ids = [c.id for c in companies]
        records = (
            FraudDetectionRecord.query.filter(FraudDetectionRecord.company_id.in_(ids))
            .order_by(FraudDetectionRecord.created_at.desc(), FraudDetectionRecord.id.desc())
            .all()
        )
        for record in records:
            if record.company_id not in latest_by_company:
                latest_by_company[record.company_id] = record

    return render_template(
        "companies.html",
        companies=companies,
        latest_by_company=latest_by_company,
    )


@app.route("/admin/companies/status", methods=["GET"])
def admin_companies_status():
    if "admin_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    raw_ids = (request.args.get("ids") or "").strip()
    if not raw_ids:
        return jsonify({"companies": {}})

    ids = []
    for part in raw_ids.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except Exception:
            continue

    # Keep this endpoint cheap.
    ids = list(dict.fromkeys(ids))[:200]
    if not ids:
        return jsonify({"companies": {}})

    company_rows = Company.query.filter(Company.id.in_(ids)).all()
    company_status_by_id = {c.id: (c.status or "-") for c in company_rows}

    latest_by_company_id = {}
    records = (
        FraudDetectionRecord.query.filter(FraudDetectionRecord.company_id.in_(ids))
        .order_by(FraudDetectionRecord.created_at.desc(), FraudDetectionRecord.id.desc())
        .all()
    )
    for record in records:
        if record.company_id not in latest_by_company_id:
            latest_by_company_id[record.company_id] = record

    payload = {}
    for company_id in ids:
        record = latest_by_company_id.get(company_id)
        stage = ""
        if record and getattr(record, "details_json", None):
            try:
                details = json.loads(record.details_json)
                stage = (details.get("analysis_stage") or "").strip()
            except Exception:
                stage = ""
        payload[str(company_id)] = {
            "company_status": company_status_by_id.get(company_id, "-"),
            "classification": (getattr(record, "classification", None) or "").strip() if record else "",
            "risk_score_pct": float(getattr(record, "risk_score_pct", 0.0) or 0.0) if record else None,
            "reasons": (getattr(record, "reasons", None) or "").strip() if record else "",
            "analysis_stage": stage,
        }

    return jsonify({"companies": payload})


@app.route("/admin/companies/add", methods=["POST"])
def admin_add_company():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    form = request.form
    company_name = (form.get("company_name") or "").strip()
    if not company_name:
        flash("Company name is required.", "danger")
        return redirect(url_for("admin_companies"))

    existing = Company.query.filter_by(company_name=company_name).first()
    if existing:
        flash("Company name already exists.", "warning")
        return redirect(url_for("admin_companies"))

    payload = {
        "company_name": company_name,
        "industry": (form.get("industry") or "").strip() or None,
        "contact_person": (form.get("contact_person") or "").strip() or None,
        "contact_email": (form.get("contact_email") or "").strip() or None,
        "contact_phone": (form.get("contact_phone") or "").strip() or None,
        "website": (form.get("website") or "").strip() or None,
        "address": (form.get("address") or "").strip() or None,
        "registration_number": (form.get("registration_number") or "").strip() or None,
        "gst_number": (form.get("gst_number") or "").strip() or None,
        "salary_package": (form.get("salary_package") or "").strip() or None,
        "previous_history": (form.get("previous_history") or "0").strip() or "0",
        "description": (form.get("description") or "").strip() or None,
    }

    quick_analysis = None
    try:
        quick_analysis = run_quick_analysis(payload)
    except Exception:
        quick_analysis = None

    try:
        payload_log = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        payload_log = str(payload)
    print(f"[fraud] input payload: {payload_log}")
    try:
        app.logger.info("[fraud] input payload: %s", payload_log)
    except Exception:
        pass

    # Create DB rows first, then run fraud analysis asynchronously.
    status = "Pending"
    company = Company(
        company_name=company_name,
        industry=payload.get("industry"),
        website=payload.get("website"),
        contact_person=payload.get("contact_person"),
        contact_email=payload.get("contact_email"),
        contact_phone=payload.get("contact_phone"),
        address=payload.get("address"),
        description=payload.get("description"),
        registration_number=payload.get("registration_number"),
        gst_number=payload.get("gst_number"),
        status=status,
    )

    try:
        db.session.add(company)
        db.session.flush()

        quick_classification = ((quick_analysis or {}).get("classification") or "pending").strip().lower()
        try:
            quick_risk_pct = float((quick_analysis or {}).get("risk_score_pct") or 0.0)
        except Exception:
            quick_risk_pct = 0.0
        quick_reasons = (quick_analysis or {}).get("reasons") or "Fraud analysis pending"
        quick_breakdown = (quick_analysis or {}).get("scoring_breakdown") or []
        quick_layer1 = (quick_analysis or {}).get("layer1_format") or {}
        quick_ml = (quick_analysis or {}).get("ml_score") or 0.0
        quick_details = quick_analysis or {"analysis_stage": "quick"}

        fraud_record = FraudDetectionRecord(
            company_id=company.id,
            risk_score=quick_risk_pct,
            status=quick_classification,
            fraud_reasons=str(quick_reasons),
            classification=quick_classification,
            risk_score_pct=quick_risk_pct,
            is_fraud=bool(quick_classification == "fraud"),
            anomaly_score=float((quick_analysis or {}).get("anomaly_score") or 0.0),
            reasons=str(quick_reasons),
            features_used=json.dumps(
                {
                    "company_name": payload.get("company_name"),
                    "industry": payload.get("industry"),
                    "website": payload.get("website"),
                    "contact_email": payload.get("contact_email"),
                    "contact_phone": payload.get("contact_phone"),
                    "registration_number": payload.get("registration_number"),
                    "gst_number": payload.get("gst_number"),
                    "salary_package": payload.get("salary_package"),
                    "previous_history": payload.get("previous_history"),
                }
            ),
            scoring_breakdown_json=json.dumps(quick_breakdown),
            layer1_format_json=json.dumps(quick_layer1),
            layer3_web_json=json.dumps({}),
            ml_score=float(quick_ml or 0.0),
            details_json=json.dumps(quick_details),
        )
        db.session.add(fraud_record)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f"Could not add company: {exc}", "danger")
        return redirect(url_for("admin_companies"))

    if _should_spawn_background_tasks():
        payload_copy = copy.deepcopy(payload)
        thread = threading.Thread(
            target=_run_company_fraud_analysis_background,
            args=(company.id, fraud_record.id, payload_copy),
            daemon=True,
        )
        thread.start()

    flash(
        "Company added.\nFraud analysis is running in the background; refresh in a few seconds for results.",
        "info",
    )

    return redirect(url_for("admin_companies"))


def _should_spawn_background_tasks():
    # Avoid duplicate background executions under Flask debug reloader.
    if not app.config.get("DEBUG", False):
        return True
    flag = os.environ.get("WERKZEUG_RUN_MAIN")
    # If the reloader isn't enabled, run tasks normally.
    if flag is None:
        return True
    return str(flag).strip().lower() in {"true", "1", "yes"}


def _run_company_fraud_analysis_background(company_id: int, record_id: int, payload: dict):
    # Runs in background thread; must create its own app context.
    with app.app_context():
        try:
            analysis = run_full_analysis(company_data=payload, session=db.session, CompanyModel=Company)
            if isinstance(analysis, dict):
                analysis["analysis_stage"] = "full"

            try:
                analysis_log = json.dumps(analysis, ensure_ascii=False, default=str)
            except Exception:
                analysis_log = str(analysis)
            print(f"[fraud] analysis result: {analysis_log}")
            try:
                app.logger.info("[fraud] analysis result: %s", analysis_log)
            except Exception:
                pass
        except Exception as exc:
            db.session.rollback()
            analysis = {
                "analysis_stage": "error",
                "classification": "error",
                "risk_score_pct": 0.0,
                "is_fraud": False,
                "anomaly_score": 0.0,
                "reasons": f"Fraud analysis failed: {exc}",
                "scoring_breakdown": [],
                "layer1_format": {},
                "layer3_web": {},
                "ml_score": 0.0,
            }

        classification = (analysis.get("classification") or "legitimate").strip().lower()
        try:
            risk_pct = float(analysis.get("risk_score_pct") or 0.0)
        except Exception:
            risk_pct = 0.0

        company_status = "Active" if classification == "legitimate" else ("Blocked" if classification == "fraud" else "Review")

        try:
            record = FraudDetectionRecord.query.get(record_id)
            company = Company.query.get(company_id)

            if record:
                record.risk_score = risk_pct
                record.status = classification
                record.fraud_reasons = analysis.get("reasons") or ""
                record.classification = classification if classification else "legitimate"
                record.risk_score_pct = risk_pct
                record.is_fraud = bool(analysis.get("is_fraud"))
                record.anomaly_score = float(analysis.get("anomaly_score") or 0.0)
                record.reasons = analysis.get("reasons") or ""
                record.scoring_breakdown_json = json.dumps(analysis.get("scoring_breakdown") or [])
                record.layer1_format_json = json.dumps(analysis.get("layer1_format") or {})
                record.layer3_web_json = json.dumps(analysis.get("layer3_web") or {})
                record.ml_score = float(analysis.get("ml_score") or 0.0)
                record.details_json = json.dumps(analysis)

            if company:
                company.status = company_status if classification != "error" else "Review"

            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            try:
                app.logger.exception("Background fraud analysis update failed: %s", exc)
            except Exception:
                pass
    return


@app.route("/admin/companies/delete/<int:company_id>", methods=["POST"])
def admin_delete_company(company_id: int):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    company = Company.query.get(company_id)
    if not company:
        flash("Company not found.", "danger")
        return redirect(url_for("admin_companies"))

    try:
        FraudDetectionRecord.query.filter_by(company_id=company_id).delete(synchronize_session=False)
        db.session.delete(company)
        db.session.commit()
        flash("Company deleted.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Unable to delete company: {exc}", "danger")

    return redirect(url_for("admin_companies"))


@app.route("/admin/companies/<int:company_id>/details", methods=["GET"])
def admin_company_fraud_details(company_id: int):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    company = Company.query.get(company_id)
    if not company:
        flash("Company not found.", "danger")
        return redirect(url_for("admin_companies"))

    record = (
        FraudDetectionRecord.query.filter_by(company_id=company_id)
        .order_by(FraudDetectionRecord.created_at.desc(), FraudDetectionRecord.id.desc())
        .first()
    )
    if not record:
        flash("No fraud analysis record found for this company.", "warning")
        return redirect(url_for("admin_companies"))

    details = {}
    try:
        details = json.loads(record.details_json or "{}")
    except Exception:
        details = {}

    layer1 = details.get("layer1_format") or {}
    layer3 = details.get("layer3_web") or {}

    if not layer1 and record.layer1_format_json:
        try:
            layer1 = json.loads(record.layer1_format_json or "{}") or {}
        except Exception:
            layer1 = {}
    if not layer3 and record.layer3_web_json:
        try:
            layer3 = json.loads(record.layer3_web_json or "{}") or {}
        except Exception:
            layer3 = {}

    email_validation = layer1.get("email_format") or {}
    gst_validation = layer1.get("gst_format") or {}
    domain_age = layer3.get("domain_age") or {}
    ml_score = details.get("ml_score")

    if ml_score is None:
        ml_score = record.ml_score

    return render_template(
        "company_fraud_details.html",
        company=company,
        record=record,
        email_validation=email_validation,
        gst_validation=gst_validation,
        domain_age=domain_age,
        ml_score=ml_score,
    )


@app.route("/admin/companies/<int:company_id>/edit", methods=["GET", "POST"])
def admin_edit_company(company_id: int):
    redirect_response = _admin_login_redirect()
    if redirect_response:
        return redirect_response

    company = Company.query.get(company_id)
    if not company:
        flash("Company not found.", "danger")
        return redirect(url_for("admin_companies"))

    if request.method == "POST":
        form = request.form
        company_name = (form.get("company_name") or "").strip()
        if not company_name:
            flash("Company name is required.", "danger")
            return render_template("admin_edit_company.html", company=company, form_data=form)

        existing = Company.query.filter_by(company_name=company_name).first()
        if existing and existing.id != company.id:
            flash("Company name already exists.", "warning")
            return render_template("admin_edit_company.html", company=company, form_data=form)

        contact_email = (form.get("contact_email") or "").strip() or None
        if contact_email and "@" not in contact_email:
            flash("Enter a valid email address.", "danger")
            return render_template("admin_edit_company.html", company=company, form_data=form)

        website = (form.get("website") or "").strip() or None
        if website:
            try:
                parsed = urlparse(website)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    flash("Website must be a valid http(s) URL.", "danger")
                    return render_template("admin_edit_company.html", company=company, form_data=form)
            except Exception:
                flash("Website must be a valid URL.", "danger")
                return render_template("admin_edit_company.html", company=company, form_data=form)

        try:
            company.company_name = company_name
            company.industry = (form.get("industry") or "").strip() or None
            company.contact_person = (form.get("contact_person") or "").strip() or None
            company.contact_email = contact_email
            company.contact_phone = (form.get("contact_phone") or "").strip() or None
            company.website = website
            company.address = (form.get("address") or "").strip() or None
            company.registration_number = (form.get("registration_number") or "").strip() or None
            company.gst_number = (form.get("gst_number") or "").strip() or None
            company.description = (form.get("description") or "").strip() or None

            db.session.commit()
            flash("Company updated.", "success")
            return redirect(url_for("admin_companies"))
        except Exception as exc:
            db.session.rollback()
            flash(f"Unable to update company: {exc}", "danger")
            return render_template("admin_edit_company.html", company=company, form_data=form)

    return render_template("admin_edit_company.html", company=company, form_data=None)

# =========================
# LEGACY FRAUD ROUTES
# =========================
@app.route("/admin/fraud-detection", methods=["GET"])
def fraud_detection_dashboard():
    """Backward-compatible redirect to company registration page."""
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))
    return redirect(url_for("admin_companies"))


@app.route("/api/student/analytics/<int:student_id>", methods=["GET"])
def api_student_analytics(student_id):
    # Allow students to see their own analytics; admins can view anyone.
    if "admin_id" not in session:
        if "student_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        if int(session["student_id"]) != int(student_id):
            return jsonify({"error": "Forbidden"}), 403

    # Prefer CSV/mock result table; fall back to legacy TestResult-based analytics if empty.
    results = (
        StudentMockTestResult.query.filter_by(student_id=student_id)
        .order_by(StudentMockTestResult.submitted_at.asc(), StudentMockTestResult.id.asc())
        .all()
    )

    if not results:
        legacy = _build_student_analytics(student_id)
        legacy_scores = legacy.get("scores") or []
        avg_score = round((sum(legacy_scores) / len(legacy_scores)), 2) if legacy_scores else 0.0
        best_score = round(max(legacy_scores), 2) if legacy_scores else 0.0
        return jsonify(
            {
                "labels": legacy.get("labels") or [],
                "datasets": [
                    {
                        "label": "Overall Score (%)",
                        "data": legacy.get("scores") or [],
                        "borderColor": "#ff69b4",
                        "backgroundColor": "rgba(255, 105, 180, 0.2)",
                        "borderWidth": 3,
                        "tension": 0.35,
                        "fill": True,
                    }
                ],
                "history": [
                    {"label": lbl, "score_pct": float(val)}
                    for lbl, val in zip((legacy.get("labels") or []), legacy_scores)
                ],
                "meta": {
                    "source": "legacy",
                    "attempts": legacy.get("attempts_count") or 0,
                    "average_score_pct": avg_score,
                    "best_score_pct": best_score,
                },
            }
        )

    labels = []
    overall_pct = []
    aptitude_pct = []
    logical_pct = []
    technical_pct = []
    coding_pct = []

    for idx, r in enumerate(results, start=1):
        ts = r.submitted_at.isoformat() if r.submitted_at else f"Attempt {idx}"
        labels.append(ts)
        total = float(r.total_questions or 0) or 0.0
        if total <= 0:
            total = 1.0

        overall_pct.append(round((float(r.score or 0) / total) * 100.0, 2))
        aptitude_pct.append(round((float(r.aptitude_score or 0) / total) * 100.0, 2))
        logical_pct.append(round((float(r.logical_score or 0) / total) * 100.0, 2))
        technical_pct.append(round((float(r.technical_score or 0) / total) * 100.0, 2))
        coding_pct.append(round((float(r.coding_score or 0) / total) * 100.0, 2))

    average_score_pct = round((sum(overall_pct) / len(overall_pct)), 2) if overall_pct else 0.0
    best_score_pct = round(max(overall_pct), 2) if overall_pct else 0.0

    datasets = [
        {
            "label": "Overall Score (%)",
            "data": overall_pct,
            "borderColor": "#ff69b4",
            "backgroundColor": "rgba(255, 105, 180, 0.2)",
            "borderWidth": 3,
            "tension": 0.35,
            "fill": True,
        },
        {
            "label": "Aptitude (%)",
            "data": aptitude_pct,
            "borderColor": "#22c55e",
            "backgroundColor": "rgba(34, 197, 94, 0.12)",
            "borderWidth": 2,
            "tension": 0.35,
            "fill": False,
        },
        {
            "label": "Logical (%)",
            "data": logical_pct,
            "borderColor": "#a855f7",
            "backgroundColor": "rgba(168, 85, 247, 0.12)",
            "borderWidth": 2,
            "tension": 0.35,
            "fill": False,
        },
        {
            "label": "Technical (%)",
            "data": technical_pct,
            "borderColor": "#3b82f6",
            "backgroundColor": "rgba(59, 130, 246, 0.12)",
            "borderWidth": 2,
            "tension": 0.35,
            "fill": False,
        },
        {
            "label": "Coding (%)",
            "data": coding_pct,
            "borderColor": "#f59e0b",
            "backgroundColor": "rgba(245, 158, 11, 0.12)",
            "borderWidth": 2,
            "tension": 0.35,
            "fill": False,
        },
    ]

    return jsonify(
        {
            "labels": labels[-30:],
            "datasets": [{**ds, "data": ds["data"][-30:]} for ds in datasets],
            # Convenience for simple Chart.js clients that expect a single series.
            "data": overall_pct[-30:],
            "history": [
                {
                    "timestamp": labels[i],
                    "score_pct": overall_pct[i],
                    "aptitude_pct": aptitude_pct[i],
                    "logical_pct": logical_pct[i],
                    "technical_pct": technical_pct[i],
                    "coding_pct": coding_pct[i],
                    "source": (results[i].source if i < len(results) else ""),
                }
                for i in range(max(0, len(labels) - 30), len(labels))
            ],
            "meta": {
                "source": "student_mock_test_results",
                "attempts": len(results),
                "average_score_pct": average_score_pct,
                "best_score_pct": best_score_pct,
            },
        }
    )


@app.route("/api/student/performance/latest/<int:student_id>", methods=["GET"])
def api_student_latest_performance(student_id):
    if "admin_id" not in session:
        if "student_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        if int(session["student_id"]) != int(student_id):
            return jsonify({"error": "Forbidden"}), 403

    latest = (
        StudentMockTestResult.query.filter_by(student_id=student_id)
        .order_by(StudentMockTestResult.submitted_at.desc(), StudentMockTestResult.id.desc())
        .first()
    )
    if not latest:
        return jsonify({"student_id": student_id, "has_data": False})

    total = float(latest.total_questions or 0) or 1.0
    payload = {
        "student_id": student_id,
        "has_data": True,
        "source": latest.source,
        "attempt_id": latest.attempt_id,
        "timestamp": latest.submitted_at.isoformat() + "Z" if latest.submitted_at else None,
        "score": int(latest.score or 0),
        "total_questions": int(latest.total_questions or 0),
        "score_pct": round((float(latest.score or 0) / total) * 100.0, 2),
        "section_scores": {
            "aptitude": int(latest.aptitude_score or 0),
            "logical": int(latest.logical_score or 0),
            "technical": int(latest.technical_score or 0),
            "coding": int(latest.coding_score or 0),
        },
        "section_pct": {
            "aptitude": round((float(latest.aptitude_score or 0) / total) * 100.0, 2),
            "logical": round((float(latest.logical_score or 0) / total) * 100.0, 2),
            "technical": round((float(latest.technical_score or 0) / total) * 100.0, 2),
            "coding": round((float(latest.coding_score or 0) / total) * 100.0, 2),
        },
    }
    return jsonify(payload)


@app.route("/api/student/placement-stats/<int:student_id>", methods=["GET"])
def api_student_placement_stats(student_id):
    """Get student's placement statistics: applications, selections, and salary data."""
    if "admin_id" not in session:
        if "student_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        if int(session["student_id"]) != int(student_id):
            return jsonify({"error": "Forbidden"}), 403

    # Get all applications for this student
    applications = (
        PlacementApplication.query.filter_by(student_id=student_id)
        .join(Placement, Placement.placeid == PlacementApplication.placement_id)
        .add_columns(Placement.package, Placement.date, Placement.cmpname)
        .all()
    )

    if not applications:
        return jsonify({
            "student_id": student_id,
            "total_applications": 0,
            "selected_count": 0,
            "success_rate": 0.0,
            "average_salary": 0.0,
            "max_salary": 0.0,
            "salary_history": [],
            "timeline": []
        })

    total_apps = len(applications)
    selected_apps = []
    salaries = []
    timeline_data = []

    for app, package, date, company_name in applications:
        # Count selections (where status is "Selected", "Placed", etc.)
        if app.status and app.status.lower() in ["selected", "placed", "accepted"]:
            selected_apps.append(app)
            if package:
                salaries.append(package)

        # Build timeline data
        timeline_data.append({
            "date": date.isoformat() if date else None,
            "company": company_name,
            "status": app.status or "Applied",
            "package": package
        })

    selected_count = len(selected_apps)
    success_rate = round((selected_count / total_apps * 100) if total_apps > 0 else 0, 2)
    average_salary = round(sum(salaries) / len(salaries), 2) if salaries else 0.0
    max_salary = round(max(salaries), 2) if salaries else 0.0

    # Sort timeline by date
    timeline_data.sort(key=lambda x: x["date"] or "")

    return jsonify({
        "student_id": student_id,
        "total_applications": total_apps,
        "selected_count": selected_count,
        "success_rate": success_rate,
        "average_salary": average_salary,
        "max_salary": max_salary,
        "salary_history": sorted(salaries),
        "timeline": timeline_data
    })


@app.route("/api/placement-salary-trends", methods=["GET"])
def api_placement_salary_trends():
    """Get overall placement salary trends: average and count by time period."""
    if "admin_id" not in session and "student_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    # Get all placements with applications that resulted in selection
    placements = Placement.query.all()

    if not placements:
        return jsonify({
            "labels": [],
            "average_salaries": [],
            "placement_counts": [],
            "total_placements": 0,
            "average_package": 0.0,
            "highest_package": 0.0,
            "company_stats": []
        })

    company_stats = {}
    all_salaries = []
    monthly_data = {}  # Format: "2026-03" -> {"count": 0, "salaries": []}

    for placement in placements:
        if not placement.package:
            continue

        all_salaries.append(placement.package)

        # Track by company
        company_name = placement.cmpname or "Unknown"
        if company_name not in company_stats:
            company_stats[company_name] = {"count": 0, "salaries": [], "avg": 0.0}

        company_stats[company_name]["count"] += 1
        company_stats[company_name]["salaries"].append(placement.package)

        # Track by month
        if placement.date:
            month_key = placement.date.strftime("%Y-%m")
            if month_key not in monthly_data:
                monthly_data[month_key] = {"count": 0, "salaries": []}
            monthly_data[month_key]["count"] += 1
            monthly_data[month_key]["salaries"].append(placement.package)

    # Calculate company averages
    for company_name in company_stats:
        salaries = company_stats[company_name]["salaries"]
        company_stats[company_name]["avg"] = round(sum(salaries) / len(salaries), 2)

    # Prepare monthly timeline
    labels = sorted(monthly_data.keys())
    average_salaries = []
    placement_counts = []

    for month in labels:
        data = monthly_data[month]
        avg = round(sum(data["salaries"]) / len(data["salaries"]), 2) if data["salaries"] else 0.0
        average_salaries.append(avg)
        placement_counts.append(data["count"])

    avg_package = round(sum(all_salaries) / len(all_salaries), 2) if all_salaries else 0.0
    highest_package = round(max(all_salaries), 2) if all_salaries else 0.0

    # Sort companies by average salary (top 10)
    sorted_companies = sorted(
        [
            {
                "name": name,
                "count": stats["count"],
                "average": stats["avg"],
                "min": round(min(stats["salaries"]), 2),
                "max": round(max(stats["salaries"]), 2)
            }
            for name, stats in company_stats.items()
        ],
        key=lambda x: x["average"],
        reverse=True
    )[:10]

    return jsonify({
        "labels": labels,
        "average_salaries": average_salaries,
        "placement_counts": placement_counts,
        "total_placements": sum(placement_counts),
        "average_package": avg_package,
        "highest_package": highest_package,
        "company_stats": sorted_companies
    })


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
    # Prefer unified results table (covers CSV + DB mock tests).
    rows = (
        db.session.query(StudentMockTestResult, Student)
        .outerjoin(Student, Student.st_id == StudentMockTestResult.student_id)
        .order_by(StudentMockTestResult.submitted_at.desc(), StudentMockTestResult.id.desc())
        .all()
    )

    results = []
    if rows:
        from types import SimpleNamespace

        db_attempt_ids = [
            r.attempt_id
            for r, _student in rows
            if (r.source == "db_test" and r.attempt_id is not None)
        ]
        test_title_by_attempt_id = {}
        if db_attempt_ids:
            db_rows = (
                db.session.query(TestResult, MockTest)
                .outerjoin(MockTest, MockTest.id == TestResult.test_id)
                .filter(TestResult.id.in_(db_attempt_ids))
                .all()
            )
            for test_result, mock_test in db_rows:
                test_title_by_attempt_id[test_result.id] = mock_test

        for r, student in rows:
            test_obj = None
            if r.source == "db_test" and r.attempt_id is not None:
                test_obj = test_title_by_attempt_id.get(r.attempt_id)
                if test_obj is None:
                    test_obj = SimpleNamespace(title="DB Mock Test")
            elif r.source == "csv_full":
                test_obj = SimpleNamespace(title="CSV Mock Test (Full)")
            elif r.source == "csv_practice":
                test_obj = SimpleNamespace(title="CSV Mock Test (Practice)")
            elif r.source == "csv_adaptive":
                test_obj = SimpleNamespace(title="CSV Mock Test (Adaptive)")
            else:
                test_obj = SimpleNamespace(title=(r.source or "Mock Test"))

            results.append((r, student, test_obj))

    # Backward-compatible fallback.
    if not results:
        results = (
            db.session.query(TestResult, Student, MockTest)
            .outerjoin(Student, Student.st_id == TestResult.student_id)
            .outerjoin(MockTest, MockTest.id == TestResult.test_id)
            .order_by(TestResult.submitted_at.desc(), TestResult.id.desc())
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
                placement_id=new_placement.placeid,
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

    # Remove notifications related to this drive (including legacy message-only ones).
    legacy_new_prefix = f"New placement drive scheduled: {placement.cmpname} "
    legacy_status_prefix = f"Your application status for {placement.cmpname} "
    Notification.query.filter(
        (Notification.placement_id == placement.placeid)
        | (Notification.msgtext.like(f"{legacy_new_prefix}%"))
        | (Notification.msgtext.like(f"{legacy_status_prefix}%"))
    ).delete(synchronize_session=False)

    # Remove dependent rows first to avoid FK conflicts.
    PlacementApplication.query.filter_by(placement_id=placement.placeid).delete()

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
                placement_id=placement.placeid,
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
        try:
            CsvMockTestAttempt.query.filter_by(student_id=student_id).delete(synchronize_session=False)
            CsvAdaptiveTestAttempt.query.filter_by(student_id=student_id).delete(synchronize_session=False)
            StudentMockTestResult.query.filter_by(student_id=student_id).delete(synchronize_session=False)
            PlacementApplication.query.filter_by(student_id=student_id).delete(synchronize_session=False)
            Notification.query.filter_by(student_id=student_id).delete(synchronize_session=False)
            Resume.query.filter_by(student_id=student_id).delete(synchronize_session=False)
            TestResult.query.filter_by(student_id=student_id).delete(synchronize_session=False)

            db.session.delete(student)
            db.session.commit()
            flash("Student deleted successfully.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(f"Unable to delete student: {exc}", "danger")
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
