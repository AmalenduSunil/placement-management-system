import csv
from app import app, db
from model import QuestionBank
from routes import _coerce_correct_answer_letter, _normalize_test_section

with app.app_context():
    with open("questions.csv", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row in reader:
            section = _normalize_test_section(row.get("section"))
            option_a = (row.get("option_a") or "").strip()
            option_b = (row.get("option_b") or "").strip()
            option_c = (row.get("option_c") or "").strip()
            option_d = (row.get("option_d") or "").strip()
            correct_answer = _coerce_correct_answer_letter(
                row.get("correct_answer"), option_a, option_b, option_c, option_d
            )

            if section not in {"Aptitude", "Logical", "Technical", "Coding"}:
                continue
            if correct_answer not in {"A", "B", "C", "D"}:
                continue

            q = QuestionBank(
                section=section,
                question_text=(row.get("question_text") or "").strip(),
                option_a=option_a,
                option_b=option_b,
                option_c=option_c,
                option_d=option_d,
                correct_answer=correct_answer,
            )

            db.session.add(q)

        db.session.commit()

print("Questions imported successfully")
