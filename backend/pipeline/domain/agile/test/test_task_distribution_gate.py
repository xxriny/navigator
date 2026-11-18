"""Model assignments cannot cross the current task and team membership boundaries."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from auth import database
from auth.shared_models import SharedBase, Team, TeamMember, User
from pipeline.domain.agile import task_coordinator
from pipeline.domain.agile.nodes import task_distributor
from pipeline.domain.agile.schemas import TaskDistributorOutput


class TaskDistributionGateTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        SharedBase.metadata.create_all(
            self.engine,
            tables=[Team.__table__, User.__table__, TeamMember.__table__],
        )
        task_coordinator._Base.metadata.create_all(self.engine)
        session_factory = sessionmaker(bind=self.engine)
        for target, name in (
            (database, "SharedSessionLocal"),
            (task_coordinator, "_Session"),
        ):
            p = patch.object(target, name, session_factory)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(task_coordinator, "_engine", self.engine)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.engine.dispose)

        with session_factory() as db:
            db.add_all([
                Team(id="team-a", name="A"),
                Team(id="team-b", name="B"),
                User(id="alice", team_id="team-a", name="Alice", email="alice@test.invalid",
                     role="backend", password_hash="x"),
                User(id="bob", team_id="team-a", name="Bob", email="bob@test.invalid",
                     role="frontend", password_hash="x"),
                TeamMember(user_id="alice", team_id="team-a", role="backend"),
                TeamMember(user_id="bob", team_id="team-a", role="frontend"),
                task_coordinator.AgileTask(id="valid", team_id="team-a", status="unassigned",
                                           task_type="feature", title="Backend work", area="backend"),
                task_coordinator.AgileTask(id="foreign", team_id="team-b", status="unassigned",
                                           task_type="feature", title="Foreign work", area="backend"),
                task_coordinator.AgileTask(id="assigned", team_id="team-a", status="in_progress",
                                           task_type="feature", title="Active work", area="backend"),
            ])
            db.commit()

    def test_only_current_team_unassigned_task_and_compatible_member_can_change(self):
        for task_id, assignee, member_id in (
            ("missing", "Alice", "alice"),
            ("foreign", "Alice", "alice"),
            ("assigned", "Alice", "alice"),
            ("valid", "Bob", "bob"),
            ("valid", "Mallory", "mallory"),
        ):
            with self.subTest(task_id=task_id, assignee=assignee):
                self.assertIsNone(task_coordinator.assign_task(
                    task_id, assignee, team_id="team-a", assignee_id=member_id))

        self.assertEqual(task_coordinator.get_task("foreign")["status"], "unassigned")
        self.assertEqual(task_coordinator.get_task("assigned")["status"], "in_progress")
        self.assertEqual(task_coordinator.get_task("valid")["status"], "unassigned")
        result = task_coordinator.assign_task(
            "valid", "Alice", "pm-1", team_id="team-a", assignee_id="alice")
        self.assertEqual(result["status"], "pending_approval")
        self.assertEqual(result["assignee"], "Alice")
        self.assertEqual(result["reviewed_by"], "pm-1")
        self.assertIsNone(task_coordinator.assign_task(
            "valid", "Alice", team_id="team-a", assignee_id="alice"))

    def test_client_member_and_model_assignment_cannot_invent_a_team_member(self):
        output = TaskDistributorOutput(assignments=[
            {"task_id": "valid", "assignee_name": "Mallory"},
            {"task_id": "foreign", "assignee_name": "Alice"},
        ])
        with patch.object(task_distributor, "call_structured",
                          return_value=SimpleNamespace(parsed=output)):
            result = task_distributor.run_task_distributor(
                "team-a", "fake-key", "fake-model",
                members=[{"id": "mallory", "name": "Mallory", "role": "backend"}],
            )
        self.assertEqual(result["assigned"], 0)
        self.assertEqual(task_coordinator.get_task("valid")["status"], "unassigned")
        self.assertEqual(task_coordinator.get_task("foreign")["status"], "unassigned")

    def test_valid_model_assignment_still_reaches_pending_approval(self):
        output = TaskDistributorOutput(assignments=[
            {"task_id": "valid", "assignee_name": "Alice"},
        ])
        with patch.object(task_distributor, "call_structured",
                          return_value=SimpleNamespace(parsed=output)):
            result = task_distributor.run_task_distributor(
                "team-a", "fake-key", "fake-model", distributed_by="pm-1")
        self.assertEqual(result["assigned"], 1)
        task = task_coordinator.get_task("valid")
        self.assertEqual(task["status"], "pending_approval")
        self.assertEqual(task["assignee"], "Alice")


if __name__ == "__main__":
    unittest.main()
