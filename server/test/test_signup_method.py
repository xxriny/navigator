"""Existing GitHub accounts keep their unlimited model-call policy."""

from unittest.mock import patch

from sqlalchemy import create_engine, text


def test_server_backfills_existing_signup_method(tmp_path):
    import database
    from models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO users (id, name, email, role, password_hash, github_id)
            VALUES ('github', 'GitHub', 'github@test.invalid', 'software_engineer', 'synthetic-hash', '123')
        """))
        conn.execute(text("""
            INSERT INTO users (id, name, email, role, password_hash)
            VALUES ('email', 'Email', 'email@test.invalid', 'software_engineer', 'real-hash')
        """))
    with patch.object(database, "engine", engine):
        database._run_migrations()
    with engine.connect() as conn:
        methods = dict(conn.execute(text("SELECT id, signup_method FROM users")).all())
    assert methods == {"github": "github", "email": "email"}
