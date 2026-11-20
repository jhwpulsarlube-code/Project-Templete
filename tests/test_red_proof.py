"""Verify red_proof accepts VARIANTS-style runners (CC/CFLAGS/main, `FAIL file:line  message`)."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PATH = Path(__file__).resolve().parents[1] / "common/test/tools/red_proof.py"
sys.path.insert(0, str(PATH.parent))
spec = importlib.util.spec_from_file_location("workflow_red_proof", PATH)
red_proof = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = red_proof
spec.loader.exec_module(red_proof)

HARNESS = """#ifndef TEST_HARNESS_H
#define TEST_HARNESS_H
#include <stdio.h>
static int g_fails;
#define EXPECT(cond) do { if (!(cond)) { g_fails++; \\
    printf("FAIL %s:%d  %s\\n", __FILE__, __LINE__, #cond); } } while (0)
static int harness_report(void) { return g_fails ? 1 : 0; }
#endif
"""

# R 의 test/run_tests.py 와 같은 공개 면(VARIANTS, CC, CFLAGS, main(argv))만 흉내 낸 러너
RUNNER = """import glob, os, subprocess, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(ROOT)
VARIANTS = [{"name": "v", "extra": []}]
CC = os.environ.get("CC") or "gcc"
CFLAGS = ["-std=c99", "-I" + PROJ, "-I" + ROOT, "-include", os.path.join(PROJ, "root_defs.h")]


def main(argv=None):
    names = [a for a in (sys.argv[1:] if argv is None else argv) if not a.startswith("-")]
    fails = 0
    for t in sorted(glob.glob(os.path.join(ROOT, "test_*.c"))):
        stem = os.path.splitext(os.path.basename(t))[0]
        if names and stem not in names:
            continue
        exe = os.path.join(PROJ, stem + ".exe")
        build = subprocess.run([CC] + CFLAGS + [t, "-o", exe], capture_output=True, text=True)
        if build.returncode:
            print("BUILD FAIL:", stem)
            print(build.stderr)
            fails += 1
            continue
        run = subprocess.run([exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        os.remove(exe)
        if run.returncode:
            sys.stdout.write(run.stdout)
            print("TEST FAIL:", stem)
            fails += 1
    return 1 if fails else 0
"""

PRODUCT_BUGGY = "int product_add(int a, int b) { return a - b; }\n"
PRODUCT_FIXED = "int product_add(int a, int b) { return a + b; }\n"

TEST_BEFORE = """#include "test_harness.h"
#include "../application/add.c"
int main(void)
{
    EXPECT(ROOT_ONE == 1);
    return harness_report();
}
"""


def test_after(extra):
    return TEST_BEFORE.replace("    return harness_report();", extra + "    return harness_report();")


class Fixture:
    def __init__(self, directory):
        self.root = Path(directory)

    def git(self, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       cwd=self.root, check=True, capture_output=True)

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout.strip()

    def start(self):
        self.git("init", "-q")
        self.put("root_defs.h", "#define ROOT_ONE 1\n")        # 저장소 루트 헤더: 격리 사본에 풀려야 한다
        self.put("application/add.c", PRODUCT_BUGGY)
        self.put("test/test_harness.h", HARNESS)
        self.put("test/run_tests.py", RUNNER)
        self.put("test/test_add.c", TEST_BEFORE)
        return self.commit("base")


@unittest.skipUnless(shutil.which("gcc") or os.environ.get("CC"), "host compiler required")
class VariantRunnerTest(unittest.TestCase):
    def prove(self, change):
        with tempfile.TemporaryDirectory(prefix="red proof ", ignore_cleanup_errors=True) as directory:
            fixture = Fixture(directory)
            fixture.start()
            change(fixture)
            commit = fixture.commit("change")
            return red_proof.run_red_proof(fixture.root, commit)

    def test_new_failing_assertion_with_fix_is_accepted(self):
        def change(fixture):
            fixture.put("application/add.c", PRODUCT_FIXED)
            fixture.put("test/test_add.c", test_after("    EXPECT(product_add(2, 3) == 5);\n"))
        report = self.prove(change)
        self.assertEqual(report.reasons, [], report.format())
        self.assertEqual(report.judged, {("test_add", "test/test_add.c: EXPECT ( product_add ( 2 , 3 ) = = 5 )")})

    def test_new_assertion_that_already_passes_in_parent_is_rejected(self):
        def change(fixture):
            fixture.put("application/add.c", PRODUCT_FIXED)
            fixture.put("test/test_add.c", test_after("    EXPECT(product_add(2, 0) == 2);\n"))
        report = self.prove(change)
        self.assertIn("부모에서 통과한 판정 단정", [name for name, _ in report.reasons], report.format())

    def test_two_new_failing_assertions_are_both_judged(self):
        def change(fixture):
            fixture.put("application/add.c", PRODUCT_FIXED)
            fixture.put("test/test_add.c", test_after("    EXPECT(product_add(2, 3) == 5);\n").replace(
                "EXPECT(ROOT_ONE == 1);", "EXPECT(product_add(1, 1) == 2);\n    EXPECT(ROOT_ONE == 1);"))
        report = self.prove(change)
        # 두 새 단정이 모두 판정 집합에 들고 부모에서 둘 다 실패한다
        self.assertEqual(report.reasons, [], report.format())
        self.assertEqual(len(report.judged), 2)

    def test_header_assertion_is_attributed_to_including_units(self):
        def change(fixture):
            fixture.put("application/add.c", PRODUCT_FIXED)
            fixture.put("test/check_add.h", "#define CHECK_ADD() EXPECT(product_add(4, 4) == 8)\n")
            fixture.put("test/test_add.c", test_after("    CHECK_ADD();\n").replace(
                '#include "../application/add.c"', '#include "../application/add.c"\n#include "check_add.h"'))
        report = self.prove(change)
        # 매크로 정의 안의 EXPECT 는 단정 호출이 아니며 사용처의 CHECK_ADD 는 EXPECT* 이름이 아니라 판정 대상 밖이다
        self.assertIn("판정 집합이 비었다", [name for name, _ in report.reasons], report.format())


class ExpectCallsTest(unittest.TestCase):
    def test_multiline_call_spans_its_lines_and_directives_are_ignored(self):
        source = "#define EXPECT_X(a) EXPECT(a)\nvoid f(void)\n{\n    EXPECT(g(1,\n           2) == 3);\n    // EXPECT(c)\n}\n"
        self.assertEqual(red_proof.expect_calls(source), [("EXPECT ( g ( 1 , 2 ) = = 3 )", 4, 5)])

    def test_nested_calls_count_only_the_outer_one(self):
        calls = red_proof.expect_calls("EXPECT_EQ_INT(1, EXPECT_Y(2));")
        self.assertEqual([call[0] for call in calls], ["EXPECT_EQ_INT ( 1 , EXPECT_Y ( 2 ) )"])

    def test_duplicate_calls_get_ordinals(self):
        calls = red_proof.expect_calls("EXPECT(a);\nEXPECT(a);\nEXPECT(b);\n")
        self.assertEqual(red_proof.describe_calls("t.c", calls), [
            "t.c: EXPECT ( a ) (동일 호출 1/2번째)", "t.c: EXPECT ( a ) (동일 호출 2/2번째)", "t.c: EXPECT ( b )"])

    def test_unclosed_call_is_a_tool_error(self):
        with self.assertRaises(red_proof.em.ToolError):
            red_proof.expect_calls("EXPECT(a;\n")

    def test_runner_without_known_interface_is_rejected(self):
        with self.assertRaises(red_proof.em.ToolError):
            red_proof.suite_for(object())


if __name__ == "__main__":
    unittest.main()
