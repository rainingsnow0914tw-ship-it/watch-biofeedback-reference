"""Supplementary local privacy gate. Findings contain locations only."""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

FORBIDDEN_DIRS = {".git", "生理資料", "日記", "真實案例", "三星SDK",
                  "rollback", ".gradle", ".kotlin", ".idea", "__pycache__"}
FORBIDDEN_SUFFIXES = {".apk", ".aar", ".jar", ".pem", ".key", ".p12",
                      ".pfx", ".jks", ".keystore", ".db", ".sqlite", ".log"}
PATTERNS = [
    ("LOCAL_PATH", r"(?i)(?:[A-Z]:[\\/]+Users[\\/]+[^\s\"']+|/(?:home|Users|c/Users|mnt/[a-z]/Users)/[^\s\"']+)"),
    ("PRIVATE_IP", r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"),
    ("DEVICE_ID", r"\bR[A-Z0-9]{8,}\b|adb-(?!tls-(?:connect|pairing)\b)[A-Za-z0-9_-]{5,}"),
    ("DEVICE_MAC", r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"),
    ("GOOGLE_KEY", r"AIza[0-9A-Za-z_-]{35}"),
    ("API_TOKEN", r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}|gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,})"),
    ("DEPLOYED_ENDPOINT", r"https?://[^\s\"'<>]*(?:\.run\.app|\.ngrok(?:-free)?\.(?:app|io)|\.workers\.dev)[^\s\"'<>]*"),
]
def decode(data: bytes) -> str:
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16le")
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16be")
    return data.decode("utf-8-sig")

def inspect(root: Path, forbidden_terms=()):
    findings = []
    file_count = 0
    for item in sorted(root.rglob("*")):
        rel = item.relative_to(root).as_posix()
        if item.is_symlink():
            findings.append({"where": rel, "code": "SYMLINK", "severity": "HIGH"})
            continue
        if any(part in FORBIDDEN_DIRS for part in item.relative_to(root).parts):
            findings.append({"where": rel, "code": "FORBIDDEN_ARTIFACT", "severity": "HIGH"})
            continue
        if not item.is_file():
            continue
        file_count += 1
        if ".bak" in item.name or item.suffix.lower() in FORBIDDEN_SUFFIXES or item.name in {".env", "local.properties", "puller_config.json"}:
            findings.append({"where": rel, "code": "PRIVATE_ARTIFACT", "severity": "HIGH"})
            continue
        try:
            text = decode(item.read_bytes())
        except (UnicodeError, OSError):
            findings.append({"where": rel, "code": "UNREADABLE_TEXT", "severity": "HIGH"})
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for code, pattern in PATTERNS:
                for match in re.finditer(pattern, line):
                    if code == "DEVICE_MAC" and match.group() == "XX:XX:XX:XX:XX:XX":
                        continue
                    findings.append({"where": f"{rel}:{line_no}", "code": code, "severity": "HIGH"})
            if any(term.casefold() in line.casefold() for term in forbidden_terms):
                findings.append({"where": f"{rel}:{line_no}", "code": "FORBIDDEN_TERM", "severity": "HIGH"})
        if any(term.casefold() in rel.casefold() for term in forbidden_terms):
            findings.append({"where": rel, "code": "FORBIDDEN_FILENAME", "severity": "HIGH"})
    unique = {json.dumps(row, sort_keys=True): row for row in findings}
    return {"files": file_count, "high": len(unique), "findings": list(unique.values())}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--forbidden-term", action="append", default=[])
    args = parser.parse_args()
    result = inspect(Path(args.root).resolve(), args.forbidden_term)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["high"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
