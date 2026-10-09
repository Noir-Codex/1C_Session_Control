import unittest
from unittest.mock import patch

from launcher import choose_web_port


class LauncherTests(unittest.TestCase):
    @patch("launcher.is_this_application", return_value=True)
    @patch("launcher.port_is_open", return_value=True)
    def test_existing_application_is_reused(self, _port_is_open, _is_this_application) -> None:
        self.assertIsNone(choose_web_port(8000))

    @patch("launcher.is_this_application", return_value=False)
    @patch("launcher.port_is_open", side_effect=[True, False])
    def test_next_port_is_used_when_8000_belongs_to_another_program(
        self, _port_is_open, _is_this_application
    ) -> None:
        self.assertEqual(choose_web_port(8000), 8001)


if __name__ == "__main__":
    unittest.main()
