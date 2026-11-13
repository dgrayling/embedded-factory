from factory.workspace import Workspace


def test_lock_detects_and_restores_tampering(tmp_path):
    ws = Workspace(tmp_path / "ws")
    ws.path.mkdir()
    ws.write("src/main.c", "int main(void){return 0;}\n")
    ws.init()
    ws.write("tests/robot/acceptance.robot", "*** Test Cases ***\nA\n    Log  x\n")
    ws.commit("tests")
    hashes, combined = ws.lock_tests()
    ws.commit("lock")
    assert list(hashes) == ["tests/robot/acceptance.robot"] and len(combined) == 64
    assert ws.tests_tampered() == []

    ws.write("tests/robot/acceptance.robot", "weakened\n")
    ws.write("tests/robot/extra.robot", "new\n")
    assert ws.tests_tampered() == ["tests/robot/acceptance.robot", "tests/robot/extra.robot"]
    ws.restore_tests()
    assert ws.tests_tampered() == []
    assert "Log  x" in ws.read("tests/robot/acceptance.robot")
    assert not (ws.path / "tests/robot/extra.robot").exists()


def test_commit_returns_none_when_clean(tmp_path):
    ws = Workspace(tmp_path / "ws")
    ws.path.mkdir()
    ws.init()
    assert ws.commit("nothing") is None
    ws.write("src/a.c", "x")
    assert ws.commit("a") == ws.head()
