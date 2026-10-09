from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


class CompanyStoreError(ValueError):
    pass


@dataclass(frozen=True)
class AssignedInfobase:
    server_endpoint: str
    cluster_id: str
    infobase_id: str
    infobase_name: str


@dataclass(frozen=True)
class Company:
    id: int
    name: str
    session_limit: int
    bases: tuple[AssignedInfobase, ...]


class CompanyStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE,
                    session_limit INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS company_bases (
                    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                    server_endpoint TEXT NOT NULL,
                    cluster_id TEXT NOT NULL,
                    infobase_id TEXT NOT NULL,
                    infobase_name TEXT NOT NULL,
                    PRIMARY KEY (company_id, server_endpoint, cluster_id, infobase_id),
                    UNIQUE (server_endpoint, cluster_id, infobase_id)
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(companies)").fetchall()
            }
            if "session_limit" not in columns:
                connection.execute(
                    "ALTER TABLE companies ADD COLUMN session_limit INTEGER NOT NULL DEFAULT 0"
                )

    def create_company(self, name: str) -> int:
        clean_name = " ".join(name.split())
        if not clean_name:
            raise CompanyStoreError("Введите название компании")
        if len(clean_name) > 120:
            raise CompanyStoreError("Название компании не должно превышать 120 символов")
        try:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute(
                    "INSERT INTO companies (name, name_key, created_at) VALUES (?, ?, ?)",
                    (clean_name, clean_name.casefold(), datetime.now(timezone.utc).isoformat()),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise CompanyStoreError("Компания с таким названием уже существует") from exc

    def delete_company(self, company_id: int) -> None:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("DELETE FROM companies WHERE id = ?", (company_id,))
            if cursor.rowcount == 0:
                raise CompanyStoreError("Компания не найдена")

    def update_session_limit(self, company_id: int, session_limit: int) -> None:
        if session_limit < 0:
            raise CompanyStoreError("Лимит сеансов не может быть отрицательным")
        if session_limit > 1_000_000:
            raise CompanyStoreError("Лимит сеансов слишком большой")
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE companies SET session_limit = ? WHERE id = ?",
                (session_limit, company_id),
            )
            if cursor.rowcount == 0:
                raise CompanyStoreError("Компания не найдена")

    def replace_bases(self, company_id: int, bases: Iterable[AssignedInfobase]) -> None:
        selected = tuple(bases)
        try:
            with closing(self._connect()) as connection, connection:
                exists = connection.execute(
                    "SELECT 1 FROM companies WHERE id = ?", (company_id,)
                ).fetchone()
                if exists is None:
                    raise CompanyStoreError("Компания не найдена")
                connection.execute("DELETE FROM company_bases WHERE company_id = ?", (company_id,))
                connection.executemany(
                    """
                    INSERT INTO company_bases
                        (company_id, server_endpoint, cluster_id, infobase_id, infobase_name)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            company_id,
                            base.server_endpoint,
                            base.cluster_id,
                            base.infobase_id,
                            base.infobase_name,
                        )
                        for base in selected
                    ],
                )
        except sqlite3.IntegrityError as exc:
            raise CompanyStoreError("Одна из баз уже прикреплена к другой компании") from exc

    def list_companies(self) -> tuple[Company, ...]:
        with closing(self._connect()) as connection, connection:
            company_rows = connection.execute(
                "SELECT id, name, session_limit FROM companies ORDER BY name_key"
            ).fetchall()
            base_rows = connection.execute(
                """
                SELECT company_id, server_endpoint, cluster_id, infobase_id, infobase_name
                FROM company_bases
                ORDER BY infobase_name
                """
            ).fetchall()
        bases_by_company: dict[int, list[AssignedInfobase]] = {}
        for row in base_rows:
            bases_by_company.setdefault(int(row["company_id"]), []).append(
                AssignedInfobase(
                    server_endpoint=str(row["server_endpoint"]),
                    cluster_id=str(row["cluster_id"]),
                    infobase_id=str(row["infobase_id"]),
                    infobase_name=str(row["infobase_name"]),
                )
            )
        return tuple(
            Company(
                id=int(row["id"]),
                name=str(row["name"]),
                session_limit=int(row["session_limit"]),
                bases=tuple(bases_by_company.get(int(row["id"]), [])),
            )
            for row in company_rows
        )
