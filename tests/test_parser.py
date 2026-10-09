import unittest

from onec.parser import decode_windows_output, parse_records


class ParserTests(unittest.TestCase):
    def test_records(self) -> None:
        text = "cluster : abc\r\nname : Main\r\n\r\ncluster : def\r\nname : Second\r\n"
        self.assertEqual(
            parse_records(text),
            [{"cluster": "abc", "name": "Main"}, {"cluster": "def", "name": "Second"}],
        )

    def test_cp866_russian(self) -> None:
        source = "user-name : Иванов"
        self.assertEqual(decode_windows_output(source.encode("cp866")), source)

    def test_cp1251_russian(self) -> None:
        source = "user-name : Иванов"
        self.assertEqual(decode_windows_output(source.encode("cp1251")), source)


if __name__ == "__main__":
    unittest.main()
