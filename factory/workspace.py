"""Run workspace: a git repo holding the firmware under construction, plus the test lock."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

FACTORY_DIR = ".factory"   # requirements.yaml, design.md, review.json, report.md live here


class Workspace:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.factory_dir = self.path / FACTORY_DIR
        self.lock_file = self.factory_dir / "test_lock.json"
        self._locked: dict | None = None

    # git -------------------------------------------------------------------------------
    def git(self, *args: str, check: bool = True) -> str:
        p = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True)
        if check and p.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
        return p.stdout.strip()

    def init(self) -> None:
        self.factory_dir.mkdir(parents=True, exist_ok=True)
        gi = self.path / ".gitignore"
        lines = set(gi.read_text().splitlines()) if gi.exists() else set()
        gi.write_text("\n".join(sorted(lines | {"build/"})) + "\n")
        if not (self.path / ".git").exists():
            self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "embedded-factory")
        self.git("config", "user.email", "factory@localhost")
        self.commit("factory: initial workspace")

    def commit(self, message: str) -> str | None:
        self.git("add", "-A")
        if not self.git("status", "--porcelain"):
            return None
        self.git("commit", "-q", "--no-verify", "-m", message)
        return self.git("rev-parse", "--short", "HEAD")

    def head(self) -> str:
        return self.git("rev-parse", "--short", "HEAD")

    def diff(self, since: str, paths: list[str] | None = None, max_chars: int = 30000) -> str:
        d = self.git("diff", since, "HEAD", "--", *(paths or ["."]), check=False)
        return d if len(d) <= max_chars else d[:max_chars] + "\n[diff truncated]"

    # files -----------------------------------------------------------------------------
    def write(self, rel: str, content: str) -> Path:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def read(self, rel: str) -> str:
        p = self.path / rel
        return p.read_text() if p.exists() else ""

    def files(self, prefix: str) -> list[str]:
        base = self.path / prefix
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.path)) for p in base.rglob("*")
                      if p.is_file() and "build" not in p.relative_to(self.path).parts)

    # test lock -------------------------------------------------------------------------
    # The lock is kept in memory as well as in .factory/test_lock.json. Builds run persona-written CMake
    # with write access to the workspace, so the file alone cannot be trusted to say what was locked.
    def _hash_tests(self) -> dict[str, str]:
        return {f: hashlib.sha256((self.path / f).read_bytes()).hexdigest() for f in self.files("tests")}

    def _lock(self) -> dict | None:
        if self._locked is not None:
            return self._locked
        return json.loads(self.lock_file.read_text()) if self.lock_file.exists() else None

    @property
    def lock_commit(self) -> str:
        lock = self._lock()
        if lock is None:
            raise RuntimeError("tests are not locked")
        return lock["commit"]

    def lock_tests(self) -> tuple[dict[str, str], str]:
        hashes = self._hash_tests()
        self._locked = {"commit": self.head(), "files": hashes}
        self.lock_file.write_text(json.dumps(self._locked, indent=2))
        combined = hashlib.sha256("".join(f"{k}{v}" for k, v in sorted(hashes.items())).encode()).hexdigest()
        return hashes, combined

    def tests_tampered(self) -> list[str]:
        lock = self._lock()
        if lock is None:
            return []
        locked = lock["files"]
        now = self._hash_tests()
        return sorted({f for f in locked if now.get(f) != locked[f]} | (set(now) - set(locked)))

    def restore_tests(self) -> None:
        lock = self._lock()
        if lock is None:
            raise RuntimeError("tests are not locked")
        for extra in set(self._hash_tests()) - set(lock["files"]):
            (self.path / extra).unlink()
        self.git("checkout", lock["commit"], "--", "tests")
        if self._locked is not None:
            self.lock_file.write_text(json.dumps(self._locked, indent=2))
        still = self.tests_tampered()
        if still:
            raise RuntimeError(f"could not restore locked tests: {', '.join(still)}")
