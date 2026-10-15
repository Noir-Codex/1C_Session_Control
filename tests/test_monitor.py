import unittest

from config import RasServer
from web.auth import ClusterCredentials
from web.company_store import AssignedInfobase, Company
from web.monitor import MonitorService, application_name, is_user_session


class FakeRac:
    def __init__(self) -> None:
        self.terminated: list[str] = []

    def terminate_session(self, endpoint, cluster_id, session_id, user, password, message):
        self.terminated.append(session_id)


class FakeCompanies:
    def __init__(self, company: Company) -> None:
        self.company = company

    def list_companies(self):
        return (self.company,)


class SessionClassificationTests(unittest.TestCase):
    def test_application_names_are_localized(self) -> None:
        self.assertEqual(application_name("1CV8C"), "1С:Предприятие (тонкий клиент)")
        self.assertEqual(application_name("Designer"), "Конфигуратор")
        self.assertEqual(application_name("BackgroundJob"), "Фоновое задание")
        self.assertEqual(application_name("CustomClient"), "CustomClient")

    def test_only_interactive_non_admin_session_is_counted(self) -> None:
        self.assertTrue(is_user_session({"id": "1", "app_id": "1CV8C", "user_name": "Ivan"}))
        self.assertFalse(is_user_session({"id": "2", "app_id": "BackgroundJob", "user_name": "Ivan"}))
        self.assertFalse(is_user_session({"id": "3", "app_id": "1CV8", "user_name": "Administrator"}))
        self.assertFalse(
            is_user_session(
                {"id": "4", "app_id": "WebClient", "user_name": "ServiceUser"},
                excluded_users=("ServiceUser",),
            )
        )

    def test_enforcement_terminates_only_newest_excess_user_session(self) -> None:
        company = Company(
            id=1,
            name="Компания",
            session_limit=1,
            bases=(AssignedInfobase("127.0.0.1:1545", "cluster", "base", "База"),),
            enforcement_enabled=True,
        )
        rac = FakeRac()
        monitor = MonitorService(rac, (), FakeCompanies(company))
        snapshots = [
            {
                "endpoint": "127.0.0.1:1545",
                "clusters": [
                    {
                        "cluster": {"id": "cluster"},
                        "sessions": [
                            {"id": "old", "infobase_id": "base", "app_id": "1CV8C", "user_name": "User1", "started_at": "2026-01-01"},
                            {"id": "new", "infobase_id": "base", "app_id": "WebClient", "user_name": "User2", "started_at": "2026-01-02"},
                            {"id": "admin", "infobase_id": "base", "app_id": "1CV8", "user_name": "admin", "started_at": "2026-01-03"},
                            {"id": "job", "infobase_id": "base", "app_id": "BackgroundJob", "user_name": "User1", "started_at": "2026-01-04"},
                        ],
                    }
                ],
            }
        ]

        monitor._enforce(snapshots, ClusterCredentials("admin", "secret"))

        self.assertEqual(rac.terminated, ["new"])

    def test_reconfigure_replaces_runtime_connection_settings(self) -> None:
        original_rac = FakeRac()
        replacement_rac = FakeRac()
        monitor = MonitorService(
            original_rac,
            (),
            FakeCompanies(Company(id=1, name="Компания", session_limit=0, bases=())),
        )
        monitor._topology = {"old": object()}
        server = RasServer("Новый", "10.0.0.5", 1546)

        monitor.reconfigure(
            replacement_rac,
            (server,),
            poll_seconds=1.5,
            topology_seconds=45,
        )

        self.assertIs(monitor.rac, replacement_rac)
        self.assertEqual(monitor.servers, (server,))
        self.assertEqual(monitor.poll_seconds, 1.5)
        self.assertEqual(monitor.topology_seconds, 45)
        self.assertEqual(monitor._topology, {})


if __name__ == "__main__":
    unittest.main()
