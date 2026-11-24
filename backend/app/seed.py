"""Demo data: one user per role and the knowledge-base documents.

Run directly with `python -m app.seed` (it also runs automatically at startup;
it never duplicates data).
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.database import SessionLocal, init_db
from app.models import User
from app.security import hash_password
from app.services import rag

DEMO_USERS = [
    ("admin@reqpilot.local", "Asha Admin", "admin"),
    ("analyst@reqpilot.local", "Ravi Business Analyst", "business_analyst"),
    ("compliance@reqpilot.local", "Meera Compliance Officer", "compliance_officer"),
    ("security@reqpilot.local", "Kiran Security Officer", "security_officer"),
    ("pm@reqpilot.local", "Arjun Project Manager", "project_manager"),
    ("auditor@reqpilot.local", "Divya Auditor", "auditor"),
]


def seed(db: Session) -> dict:
    created = 0
    for email, name, role in DEMO_USERS:
        if not db.scalar(select(User).where(User.email == email)):
            db.add(User(email=email, name=name, role=role, password_hash=hash_password(config.DEMO_USER_PASSWORD)))
            created += 1
    db.commit()
    documents = rag.load_seed_documents(db)
    return {"users_created": created, "documents_loaded": documents}


if __name__ == "__main__":
    init_db()
    with SessionLocal() as session:
        print(seed(session))
