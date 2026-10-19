"""Set every user's password to their email, except the protected admin.

Usage:
    python -m scripts.reset_user_passwords_to_email
"""

from datetime import UTC, datetime

from sqlalchemy import func, update
from sqlmodel import Session, select

from codebase.backend.auth.models import User
from codebase.backend.auth.security import hash_password
from codebase.backend.db import engine

PROTECTED_EMAIL = "admin@diab.example.com"
BATCH_SIZE = 20


def reset_passwords() -> int:
    with Session(engine) as session:
        accounts = session.exec(
            select(User.id, User.email).where(func.lower(User.email) != PROTECTED_EMAIL)
        ).all()

    for offset in range(0, len(accounts), BATCH_SIZE):
        batch = accounts[offset : offset + BATCH_SIZE]
        password_hashes = [(user_id, hash_password(email)) for user_id, email in batch]
        with Session(engine) as session:
            now = datetime.now(UTC).replace(tzinfo=None)
            for user_id, password_hash in password_hashes:
                session.exec(
                    update(User)
                    .where(User.id == user_id)
                    .values(
                        hashed_password=password_hash,
                        token_version=User.token_version + 1,
                        updated_at=now,
                    )
                )
            session.commit()

    return len(accounts)


if __name__ == "__main__":
    count = reset_passwords()
    print(f"Updated {count} account password(s); kept {PROTECTED_EMAIL} unchanged.")
