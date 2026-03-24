from collections import defaultdict

from app import app, db
from model import QuestionBank


SECTIONS = ("Aptitude", "Logical", "Technical", "Coding")
TARGET_PER_SECTION = 300  # 300 x 4 = 1200 questions


def _build_question(section, index):
    # Placeholder generator. Replace with real curated content or CSV ingestion.
    question_text = f"{section} Question #{index}: Choose the best option."
    return QuestionBank(
        section=section,
        question_text=question_text,
        option_a="Option A",
        option_b="Option B",
        option_c="Option C",
        option_d="Option D",
        correct_answer="A",
    )


def seed_question_bank():
    with app.app_context():
        db.create_all()

        counts = defaultdict(int)
        for section, count in db.session.query(QuestionBank.section, db.func.count(QuestionBank.id)).group_by(QuestionBank.section).all():
            counts[section] = count

        to_insert = []
        for section in SECTIONS:
            existing = counts.get(section, 0)
            remaining = max(0, TARGET_PER_SECTION - existing)
            for i in range(existing + 1, existing + remaining + 1):
                to_insert.append(_build_question(section, i))

        if not to_insert:
            print("Question bank already satisfies target counts.")
            return

        db.session.bulk_save_objects(to_insert)
        db.session.commit()
        print(f"Inserted {len(to_insert)} questions into question_bank.")


if __name__ == "__main__":
    seed_question_bank()
