import json
import os
import time

from typer.testing import CliRunner

from factory import housekeeping
from factory.cli import app


def _run(root, name, *, finished=True, age_days=0.0):
    run = root / name
    (run / "ws" / "build" / "zephyr").mkdir(parents=True)
    (run / "ws" / "build" / "zephyr" / "zephyr.elf").write_bytes(b"x" * 1000)
    (run / "ws" / "build-ztest").mkdir()
    (run / "ws" / "src").mkdir()
    (run / "ws" / "src" / "main.c").write_text("int main(void){return 0;}\n")
    events = [{"ts": 0, "type": "run_started", "mode": "new", "board": "b", "title": "t"}]
    if finished:
        events.append({"ts": 1, "type": "run_finished", "ok": True})
    log = run / "events.jsonl"
    log.write_text("".join(json.dumps(e) + "\n" for e in events))
    t = time.time() - age_days * 86400
    os.utime(log, (t, t))
    return run


def test_default_removes_only_build_trees_of_finished_runs(tmp_path):
    done = _run(tmp_path, "a", finished=True)
    live = _run(tmp_path, "b", finished=False)
    crashed = _run(tmp_path, "c", finished=False, age_days=2)
    targets, skipped = housekeeping.plan(tmp_path)
    assert skipped == ["b"]
    assert {t.path for t in targets} == {
        done / "ws" / "build", done / "ws" / "build-ztest",
        crashed / "ws" / "build", crashed / "ws" / "build-ztest",
    }
    assert housekeeping.remove(targets, tmp_path) >= 2000
    assert not (done / "ws" / "build").exists()
    assert (done / "ws" / "src" / "main.c").exists() and (done / "events.jsonl").exists()
    assert (live / "ws" / "build").exists()


def test_older_than_and_whole_runs(tmp_path):
    _run(tmp_path, "new", age_days=1)
    old = _run(tmp_path, "old", age_days=10)
    targets, _ = housekeeping.plan(tmp_path, older_than_days=7, whole_runs=True)
    assert [t.path for t in targets] == [old]


def test_remove_refuses_paths_outside_root(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "runs"
    root.mkdir()
    housekeeping.remove([housekeeping.Target(outside, 0, "x")], root)
    assert outside.exists()


def test_cli_dry_run_changes_nothing(tmp_path):
    run = _run(tmp_path, "a")
    r = CliRunner().invoke(app, ["clean", "--dry-run", "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "would be freed" in r.output
    assert (run / "ws" / "build").exists()
