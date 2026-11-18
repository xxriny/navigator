"""Shared chat artifacts contain only the selected team's requested section."""

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_published_artifact_contains_only_requested_section(tmp_path):
    from auth.database import Base, SharedBase
    from auth.models import AnalysisResult, AnalysisSession
    from auth.shared_models import PublishedSnapshot
    from storage.publish_service import get_snapshot, publish_snapshot, pull_snapshot

    engine = create_engine(f"sqlite:///{tmp_path / 'artifact.db'}")
    Base.metadata.create_all(engine, tables=[AnalysisSession.__table__, AnalysisResult.__table__])
    SharedBase.metadata.create_all(engine, tables=[PublishedSnapshot.__table__])
    with Session(engine) as db:
        db.add(AnalysisSession(run_id="run-1", team_id="team-a", created_by="author"))
        db.add(AnalysisResult(run_id="run-1", shaped_result=json.dumps({
            "components": [{"component_name": "Gateway", "role": "entry"}],
            "apis": [{"endpoint": "GET /health", "response_schema": {"ok": "boolean"}}],
            "tables": [{"table_name": "private_table"}],
        })))
        db.commit()

        component = publish_snapshot(db, db, "run-1", "컴포넌트", team_id="team-a", user_id="author", kind="components")
        data = get_snapshot(db, component["id"])["data"]
        assert data == {"artifact_kind": "components", "components": [{"component_name": "Gateway", "role": "entry"}]}
        try:
            pull_snapshot(db, db, component["id"], "run-1")
        except ValueError:
            pass
        else:
            raise AssertionError("partial artifact replaced a full analysis")
        assert "private_table" in db.get(AnalysisResult, "run-1").shaped_result

        api = publish_snapshot(db, db, "run-1", "API Spec", team_id="team-a", user_id="author", kind="apis")
        assert get_snapshot(db, api["id"])["data"] == {
            "artifact_kind": "apis", "apis": [{"endpoint": "GET /health", "response_schema": {"ok": "boolean"}}],
        }

        try:
            publish_snapshot(db, db, "run-1", "컴포넌트", team_id="team-b", user_id="intruder", kind="components")
        except ValueError:
            pass
        else:
            raise AssertionError("foreign team published another team's result")
