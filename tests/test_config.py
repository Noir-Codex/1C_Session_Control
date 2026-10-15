import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import RasServer, Settings, load_settings, save_settings


class SettingsPersistenceTests(unittest.TestCase):
    def test_stale_rac_path_falls_back_to_installed_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            detected_rac = root / "installed" / "bin" / "rac.exe"
            detected_rac.parent.mkdir(parents=True)
            detected_rac.write_bytes(b"")
            config_path = root / "config.toml"
            config_path.write_text(
                'rac_path = "C:/old-computer/rac.exe"\n'
                '[[servers]]\nname = "Локальный"\nhost = "127.0.0.1"\n',
                encoding="utf-8",
            )

            with patch("config.shutil.which", return_value=str(detected_rac)):
                settings = load_settings(config_path)

            self.assertEqual(settings.rac_path, detected_rac.resolve())

    def test_web_settings_round_trip_without_storing_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rac_path = root / "1cv8" / "bin" / "rac.exe"
            rac_path.parent.mkdir(parents=True)
            rac_path.write_bytes(b"")
            config_path = root / "config.toml"
            settings = Settings(
                rac_path=rac_path,
                command_timeout_seconds=12.5,
                log_file=Path(r"logs\session-control.log"),
                servers=(RasServer(
                    name="Сервер 1С",
                    host="127.0.0.1",
                    port=1545,
                    cluster_user="admin",
                    cluster_password="secret",
                    cluster_user_env="SESSION_CONTROL_CLUSTER_USER",
                    cluster_password_env="SESSION_CONTROL_CLUSTER_PASSWORD",
                ),),
                poll_interval_seconds=1.5,
                topology_refresh_seconds=45,
            )

            save_settings(config_path, settings)
            content = config_path.read_text(encoding="utf-8")
            loaded = load_settings(config_path)

            self.assertNotIn("secret", content)
            self.assertEqual(loaded.rac_path, rac_path.resolve())
            self.assertEqual(loaded.command_timeout_seconds, 12.5)
            self.assertEqual(loaded.poll_interval_seconds, 1.5)
            self.assertEqual(loaded.topology_refresh_seconds, 45)
            self.assertEqual(loaded.servers[0].name, "Сервер 1С")
            self.assertEqual(loaded.servers[0].endpoint, "127.0.0.1:1545")
            self.assertEqual(loaded.servers[0].cluster_user_env, "SESSION_CONTROL_CLUSTER_USER")


if __name__ == "__main__":
    unittest.main()
