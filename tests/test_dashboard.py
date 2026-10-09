import unittest

from web.company_store import AssignedInfobase, Company
from web.routes_v2 import _available_bases, _company_views


class DashboardGroupingTests(unittest.TestCase):
    def test_all_bases_are_listed_and_company_sessions_are_aggregated(self) -> None:
        snapshots = [
            {
                "server_name": "SRV-1C01",
                "endpoint": "127.0.0.1:1545",
                "clusters": [
                    {
                        "cluster": {"id": "cluster", "name": "Локальный кластер"},
                        "infobases": [
                            {"id": "base-1", "name": "Первая"},
                            {"id": "base-2", "name": "Вторая"},
                        ],
                        "sessions": [
                            {
                                "id": "session",
                                "infobase_id": "base-2",
                                "infobase_name": "Вторая",
                                "user_name": "User",
                                "app_id": "1CV8C",
                            }
                        ],
                    }
                ],
            }
        ]
        bases = _available_bases(snapshots)
        company = Company(
            id=1,
            name="Компания",
            session_limit=5,
            bases=(
                AssignedInfobase("127.0.0.1:1545", "cluster", "base-1", "Первая"),
                AssignedInfobase("127.0.0.1:1545", "cluster", "base-2", "Вторая"),
            ),
        )

        views = _company_views((company,), bases)

        self.assertEqual(len(bases), 2)
        self.assertEqual(
            {base["infobase_name"]: base["session_count"] for base in bases},
            {"Первая": 0, "Вторая": 1},
        )
        self.assertEqual(len(views[0]["sessions"]), 1)
        self.assertEqual(views[0]["session_limit"], 5)
        self.assertEqual(views[0]["remaining_sessions"], 4)
        self.assertFalse(views[0]["over_limit"])


if __name__ == "__main__":
    unittest.main()
