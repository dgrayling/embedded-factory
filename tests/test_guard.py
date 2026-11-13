import asyncio

from factory.personas.definitions import PERSONAS
from factory.personas.runtime import make_guard, path_allowed, resolve_in_ws

FW = PERSONAS["firmware_engineer"].write_globs


def test_globs_root_only_without_slash():
    assert path_allowed("src/main.c", FW)
    assert path_allowed("src/drivers/led.c", FW)
    assert path_allowed("prj.conf", FW)
    assert path_allowed("app.overlay", FW)
    assert not path_allowed("tests/robot/a.robot", FW)
    assert not path_allowed("tests/ztest/prj.conf", FW)       # '*.conf' is root-only
    assert not path_allowed("tests/x.overlay", FW)
    assert not path_allowed(".factory/test_lock.json", FW)
    assert path_allowed("tests/robot/a.robot", PERSONAS["test_engineer"].write_globs)
    assert not path_allowed("src/main.c", PERSONAS["test_engineer"].write_globs)


def test_resolve_in_ws(tmp_path):
    assert resolve_in_ws(tmp_path, str(tmp_path / "src/a.c")) == "src/a.c"
    assert resolve_in_ws(tmp_path, "src/a.c") == "src/a.c"
    assert resolve_in_ws(tmp_path, str(tmp_path / ".." / "etc")) is None
    assert resolve_in_ws(tmp_path, "/etc/passwd") is None


def _decide(guard, tool, **inp):
    out = asyncio.run(guard({"tool_name": tool, "tool_input": inp}, None, None))
    return out.get("hookSpecificOutput", {}).get("permissionDecision", "allow")


def test_guard_decisions(tmp_path):
    fw = make_guard(tmp_path, PERSONAS["firmware_engineer"])
    assert _decide(fw, "Write", file_path=str(tmp_path / "src/main.c")) == "allow"
    assert _decide(fw, "Edit", file_path=str(tmp_path / "tests/robot/a.robot")) == "deny"
    assert _decide(fw, "Read", file_path="/Users/someone/.ssh/id_rsa") == "deny"
    assert _decide(fw, "Read", file_path=str(tmp_path / "tests/robot/a.robot")) == "allow"
    assert _decide(fw, "Grep", pattern="x") == "allow"
    rev = make_guard(tmp_path, PERSONAS["reviewer"])
    assert _decide(rev, "Write", file_path=str(tmp_path / "src/main.c")) == "deny"
