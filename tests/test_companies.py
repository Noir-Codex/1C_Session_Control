import tempfile
import unittest
from pathlib import Path

from web.company_store import AssignedInfobase, CompanyStore, CompanyStoreError


class CompanyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database = Path(self.temporary_directory.name) / "companies.db"
        self.store = CompanyStore(database)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_company_and_assignments_are_persisted(self) -> None:
        company_id = self.store.create_company("Компания")
        base = AssignedInfobase("localhost:1545", "cluster", "base", "База")
        self.store.replace_bases(company_id, [base])

        companies = self.store.list_companies()
        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0].name, "Компания")
        self.assertEqual(companies[0].session_limit, 0)
        self.assertEqual(companies[0].bases, (base,))

    def test_session_limit_is_persisted(self) -> None:
        company_id = self.store.create_company("Компания")
        self.store.update_session_limit(company_id, 25)

        self.assertEqual(self.store.list_companies()[0].session_limit, 25)

    def test_control_settings_are_persisted(self) -> None:
        company_id = self.store.create_company("Компания")
        self.store.update_control_settings(company_id, 3, True, "AdminBase\nТехПользователь")

        company = self.store.list_companies()[0]
        self.assertEqual(company.session_limit, 3)
        self.assertTrue(company.enforcement_enabled)
        self.assertEqual(company.excluded_users, ("AdminBase", "ТехПользователь"))

    def test_one_base_cannot_belong_to_two_companies(self) -> None:
        first = self.store.create_company("Первая")
        second = self.store.create_company("Вторая")
        base = AssignedInfobase("localhost:1545", "cluster", "base", "База")
        self.store.replace_bases(first, [base])

        with self.assertRaises(CompanyStoreError):
            self.store.replace_bases(second, [base])


if __name__ == "__main__":
    unittest.main()
