from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Sequence

from onec.parser import decode_windows_output, parse_records

log = logging.getLogger(__name__)


class RacError(RuntimeError):
    pass


class RacClient:
    """The only layer allowed to execute rac.exe operations."""

    def __init__(self, executable: Path, timeout_seconds: float = 30) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def _run(self, arguments: Sequence[str]) -> str:
        command = [str(self.executable), *arguments]
        log.debug("Executing RAC operation: %s", " ".join(self._redact(command)))
        try:
            completed = subprocess.run(
                command,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RacError(str(exc)) from exc
        stdout = decode_windows_output(completed.stdout).strip()
        stderr = decode_windows_output(completed.stderr).strip()
        if completed.returncode != 0:
            log.error("rac.exe failed (code %s): %s", completed.returncode, stderr or stdout)
            raise RacError(stderr or stdout or f"rac.exe exited with code {completed.returncode}")
        return stdout

    @staticmethod
    def _redact(command: Sequence[str]) -> list[str]:
        result = list(command)
        for index, value in enumerate(result):
            if value in {"--cluster-pwd", "--infobase-pwd"}:
                if index + 1 < len(result):
                    result[index + 1] = "***"
            elif value.startswith(("--cluster-pwd=", "--infobase-pwd=")):
                result[index] = value.split("=", 1)[0] + "=***"
        return result

    @staticmethod
    def _credentials(user: str, password: str) -> list[str]:
        args: list[str] = []
        if user:
            args.append(f"--cluster-user={user}")
        if password:
            args.append(f"--cluster-pwd={password}")
        return args

    def version(self) -> str:
        # `--version` is supported by current RAC builds; a successful help call is
        # an acceptable presence check for builds that do not expose it.
        try:
            return self._run(["--version"]).splitlines()[0]
        except RacError:
            output = self._run(["help"])
            return output.splitlines()[0] if output else ""

    def list_clusters(self, endpoint: str) -> list[dict[str, str]]:
        return parse_records(self._run(["cluster", "list", endpoint]))

    def list_infobases(self, endpoint: str, cluster_id: str, user: str, password: str) -> list[dict[str, str]]:
        args = ["infobase", "summary", "list", f"--cluster={cluster_id}"]
        args += self._credentials(user, password) + [endpoint]
        return parse_records(self._run(args))

    def list_sessions(self, endpoint: str, cluster_id: str, user: str, password: str) -> list[dict[str, str]]:
        args = ["session", "list", f"--cluster={cluster_id}"]
        args += self._credentials(user, password) + [endpoint]
        return parse_records(self._run(args))

    def terminate_session(
        self,
        endpoint: str,
        cluster_id: str,
        session_id: str,
        user: str,
        password: str,
        error_message: str,
    ) -> None:
        """Terminate one session selected by the controller."""
        args = [
            "session",
            "terminate",
            f"--cluster={cluster_id}",
            *self._credentials(user, password),
            f"--session={session_id}",
            f"--error-message={error_message}",
            endpoint,
        ]
        self._run(args)
