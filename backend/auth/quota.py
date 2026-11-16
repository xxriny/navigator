"""Persistent model request limit for accounts created with a password."""

from sqlalchemy import Column, Integer, String, text

from auth.database import Base


MODEL_HTTP_PATHS = frozenset({
    "/api/analyze", "/api/idea-chat", "/api/extract-final-idea",
    "/api/agile/impact", "/api/dev-tracking/pr",
    "/api/agile/generate-tasks", "/api/agile/distribute-tasks",
})


class EmailModelUsage(Base):
    __tablename__ = "email_model_usage"

    user_id = Column(String(36), primary_key=True)
    call_count = Column(Integer, nullable=False, default=0)


def reserve_model_call(db, user) -> bool:
    # GitHub-only accounts have no password; linking GitHub later keeps an email account limited.
    if not user.password_hash:
        return True
    try:
        count = db.execute(text("""
            INSERT INTO email_model_usage (user_id, call_count) VALUES (:user_id, 1)
            ON CONFLICT (user_id) DO UPDATE
            SET call_count = email_model_usage.call_count + 1
            WHERE email_model_usage.call_count < 10
            RETURNING call_count
        """), {"user_id": user.id}).scalar_one_or_none()
        db.commit()
        return count is not None
    except Exception:
        db.rollback()
        raise


def check_model_call(token: str) -> str:
    """Return allowed, unauthorized, limited, or unavailable before model work."""
    from auth.database import SessionLocal
    from auth.service import decode_token, get_user_by_id

    payload = decode_token(token) if token else None
    if not payload:
        return "unauthorized"
    with SessionLocal() as db:
        user = get_user_by_id(db, payload.get("sub", ""))
        if not user or not user.team_id:
            return "unauthorized"
        try:
            return "allowed" if reserve_model_call(db, user) else "limited"
        except Exception:
            return "unavailable"
