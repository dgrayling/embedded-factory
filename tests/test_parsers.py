from factory.tools.parsers import parse_gcc_diagnostics, parse_memory_report, parse_robot_output

GCC_LOG = """\
[12/140] Building C object CMakeFiles/app.dir/src/main.c.obj
/work/runs/x/ws/src/main.c:14:9: error: 'foo' undeclared (first use in this function)
   14 |         foo = 1;
      |         ^~~
/work/runs/x/ws/src/main.c:14:9: note: each undeclared identifier is reported only once
/work/runs/x/ws/src/main.c:20:5: warning: unused variable 'x' [-Wunused-variable]
/work/runs/x/ws/src/main.c:14:9: error: 'foo' undeclared (first use in this function)
/opt/toolchains/arm-zephyr-eabi/bin/ld.bfd: app/libapp.a(main.c.obj): in function `main':
main.c:(.text.main+0x8): undefined reference to `bar'
CMake Error at /west/zephyr/cmake/modules/kconfig.cmake:396 (message):
"""


def test_gcc_diagnostics_parsed_and_deduped():
    diags = parse_gcc_diagnostics(GCC_LOG, root_prefix="/work/runs/x/ws")
    errs = [d for d in diags if d.severity == "error"]
    assert diags[0].file == "src/main.c" and diags[0].line == 14 and diags[0].column == 9
    assert any(d.severity == "warning" and d.line == 20 for d in diags)
    assert any(d.severity == "note" for d in diags)
    assert sum(1 for d in errs if "'foo' undeclared" in d.message) == 1
    assert any("undefined reference to 'bar'" in d.message for d in errs)
    assert any(d.message.startswith("CMake Error") and d.line == 396 for d in errs)


MEM_LOG = """\
[140/140] Linking C executable zephyr/zephyr.elf
Memory region         Used Size  Region Size  %age Used
           FLASH:       16384 B         1 MB      1.56%
             RAM:          4 KB       192 KB      2.08%
        IDT_LIST:          0 GB        32 KB      0.00%
Generating files from /work/build/zephyr/zephyr.elf
"""


def test_memory_report():
    regions = {r.name: r for r in parse_memory_report(MEM_LOG)}
    assert regions["FLASH"].used_bytes == 16384
    assert regions["FLASH"].size_bytes == 1024 * 1024
    assert regions["RAM"].used_bytes == 4096
    assert round(regions["RAM"].percent, 2) == 2.08
    assert regions["IDT_LIST"].used_bytes == 0


ROBOT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<robot generator="Robot 7.0">
<suite name="Smoke" id="s1">
  <test name="Should Announce Boot" id="s1-t1">
    <kw name="Wait For Line On Uart"><status status="PASS"/></kw>
    <status status="PASS"/>
  </test>
  <test name="Should Blink" id="s1-t2">
    <kw name="Assert LED Is Blinking"><status status="FAIL">inner</status></kw>
    <status status="FAIL">LED state did not change within 0.5s</status>
  </test>
  <status status="FAIL"/>
</suite>
</robot>
"""


def test_robot_output():
    tests = parse_robot_output(ROBOT_XML)
    assert [t.passed for t in tests] == [True, False]
    assert tests[1].message == "LED state did not change within 0.5s"
    assert tests[1].suite == "Smoke"


# --- Real toolchain output captured from `factory gates` (Zephyr v4.5.0, Renode 1.17.0) ---
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"


def test_real_west_build_log():
    log = (FIX / "west_build_stm32f4.log").read_text()
    regions = {r.name: r for r in parse_memory_report(log)}
    assert {"FLASH", "RAM"} <= regions.keys()
    assert 8 * 1024 < regions["FLASH"].used_bytes < 64 * 1024
    assert regions["FLASH"].size_bytes == 1024 * 1024
    assert not [d for d in parse_gcc_diagnostics(log) if d.severity == "error"]


def test_real_renode_robot_output():
    tests = parse_robot_output((FIX / "renode_robot_output.xml").read_text())
    assert {t.name for t in tests} == {"Should Announce Boot On Console", "Should Blink LED At 1 Hz"}
    assert all(t.passed for t in tests)


ZTEST_OK = """*** Booting Zephyr OS build v4.5.0 ***
Running TESTSUITE framework_tests
START - test_assert
 PASS - test_assert in 0.001 seconds
TESTSUITE framework_tests succeeded
PROJECT EXECUTION SUCCESSFUL
"""
ZTEST_FAIL = """Running TESTSUITE blink
START - test_period
    Assertion failed at WEST_TOPDIR/app/tests/ztest/src/main.c:12: blink_test_period: (period_ms not equal to 500)
 FAIL - test_period in 0.002 seconds
 PASS - test_duty in 0.001 seconds
PROJECT EXECUTION FAILED
"""


def test_ztest_output():
    from factory.tools.parsers import parse_ztest_output
    cases, verdict = parse_ztest_output(ZTEST_OK)
    assert verdict == "passed" and [c.name for c in cases] == ["test_assert"]
    cases, verdict = parse_ztest_output(ZTEST_FAIL)
    assert verdict == "failed"
    assert [c.passed for c in cases] == [False, True]
    assert "period_ms not equal to 500" in cases[0].message
    assert parse_ztest_output("*** Booting Zephyr\nhard fault")[1] == "incomplete"
