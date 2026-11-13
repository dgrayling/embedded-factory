"""Runs toolchain commands inside a long-lived factory container.

The repo root is bind-mounted read-only at /work, with only runs/ writable, and the west workspace
volume read-only at /west, so any host path under the repo maps to /work/<relative path> inside the container.
Builds execute persona-written CMake, so the container must not be able to modify the factory itself.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMAGE = "embedded-factory/toolchain:dev"
CONTAINER_NAME = "embedded-factory-tc"
# Bump when the `docker run` arguments change so a container started by an older factory is replaced.
CONTAINER_CONFIG = "3"
WRITABLE_DIRS = ("runs",)


@dataclass
class ExecResult:
    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def q(path: str | Path) -> str:
    """Shell-quote a value for a command string (paths can contain persona-chosen file names)."""
    return shlex.quote(str(path))


class Toolchain:
    def __init__(self, image: str = DEFAULT_IMAGE, name: str = CONTAINER_NAME, root: Path = REPO_ROOT):
        self.image, self.name, self.root = image, name, root

    def to_container(self, host_path: Path) -> str:
        rel = Path(host_path).resolve().relative_to(self.root)
        return f"/work/{rel.as_posix()}"

    def ensure_running(self) -> None:
        state = subprocess.run(
            ["docker", "inspect", "-f",
             '{{.State.Running}} {{index .Config.Labels "factory.config"}} {{.Config.Image}}', self.name],
            capture_output=True, text=True,
        )
        if state.returncode == 0 and state.stdout.split() == ["true", CONTAINER_CONFIG, self.image]:
            return
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        mounts = ["-v", "factory-west:/west:ro", "-v", f"{self.root}:/work:ro"]
        for d in WRITABLE_DIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)
            mounts += ["-v", f"{self.root / d}:/work/{d}"]
        subprocess.run(
            ["docker", "run", "-d", "--name", self.name, "--label", f"factory.config={CONTAINER_CONFIG}",
             *mounts, "-w", "/work", self.image, "sleep", "infinity"],
            check=True, capture_output=True,
        )

    def exec(self, cmd: str, cwd: str = "/work", timeout: float = 900) -> ExecResult:
        """Run `cmd` with bash in the container. The timeout is enforced inside the container too:
        killing only the `docker exec` client would leave the build or Renode running there."""
        self.ensure_running()
        secs = max(1, int(timeout))
        try:
            p = subprocess.run(
                ["docker", "exec", "-w", cwd, self.name,
                 "timeout", "-k", "10", str(secs), "bash", "-lc", cmd],
                capture_output=True, text=True, timeout=secs + 30,
            )
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            return ExecResult(124, out + f"\n[factory] command timed out after {secs}s")
        out = p.stdout + p.stderr
        if p.returncode == 124:
            out += f"\n[factory] command timed out after {secs}s"
        return ExecResult(p.returncode, out)

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
