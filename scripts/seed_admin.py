"""Bootstrap the first Admin account from ADMIN_SEED_* environment variables.

Idempotent: safe to run on every deploy / after every `make db-reset` — it is a
no-op if a user with the seed email already exists.

Usage: make seed-admin  (or: python -m scripts.seed_admin)
"""

from sqlmodel import Session, select

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.config import get_settings
from codebase.backend.db import create_db_and_tables, engine


def seed_admin() -> None:
    settings = get_settings()
    if not settings.admin_seed_email or not settings.admin_seed_password:
        print("ADMIN_SEED_EMAIL / ADMIN_SEED_PASSWORD not set in .env — skipping admin seed.")
        return

    create_db_and_tables()
    email = settings.admin_seed_email.lower()

    with Session(engine) as session:
        if session.exec(select(User).where(User.email == email)).first() is not None:
            print(f"Admin '{email}' already exists — nothing to do.")
            return

        admin = User(
            email=email,
            full_name=settings.admin_seed_name,
            role=UserRole.ADMIN,
            hashed_password=hash_password(settings.admin_seed_password),
            must_change_password=True,
        )
        session.add(admin)
        session.commit()
        print(f"Created admin '{email}'.")


if __name__ == "__main__":
    seed_admin()
