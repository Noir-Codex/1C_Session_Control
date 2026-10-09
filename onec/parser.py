from __future__ import annotations

import re

_FIELD = re.compile(r"^\s*([^:]+?)\s*:\s*(.*)$")


def decode_windows_output(data: bytes) -> str:
    """Decode RAC output without depending on the active Windows console code page."""
    if not data:
        return ""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass

    # OEM (cp866) is common for console tools, while some RAC builds emit the
    # Windows ANSI code page (cp1251). Both decode most byte strings, so prefer
    # the candidate without box-drawing/control artifacts.
    candidates = [data.decode(name, errors="replace") for name in ("cp866", "cp1251")]

    def quality(value: str) -> int:
        cyrillic = sum("\u0400" <= char <= "\u04ff" for char in value)
        artifacts = sum("\u2500" <= char <= "\u259f" or char == "�" for char in value)
        controls = sum(ord(char) < 32 and char not in "\r\n\t" for char in value)
        return cyrillic - artifacts * 10 - controls * 20

    return max(candidates, key=quality)


def parse_records(text: str) -> list[dict[str, str]]:
    """Parse RAC's blank-line-separated `name : value` records."""
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            if current:
                records.append(current)
                current = {}
            continue
        match = _FIELD.match(line)
        if match:
            current[match.group(1).strip().lower()] = match.group(2).strip()
    if current:
        records.append(current)
    return records
