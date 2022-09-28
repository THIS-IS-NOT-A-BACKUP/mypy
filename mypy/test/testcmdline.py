"""Test cases for the command line.

To begin we test that "mypy <directory>[/]" always recurses down the
whole tree.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

from mypy.test.config import PREFIX, test_temp_dir
from mypy.test.data import DataDrivenTestCase, DataSuite
from mypy.test.helpers import (
    assert_string_arrays_equal,
    check_test_output_files,
    normalize_error_messages,
)

try:
    import lxml  # type: ignore[import]
except ImportError:
    lxml = None

import pytest

# Path to Python 3 interpreter
python3_path = sys.executable

# Files containing test case descriptions.
cmdline_files = ["cmdline.test", "cmdline.pyproject.test", "reports.test", "envvars.test"]


class PythonCmdlineSuite(DataSuite):
    files = cmdline_files
    native_sep = True

    def run_case(self, testcase: DataDrivenTestCase) -> None:
        if lxml is None and os.path.basename(testcase.file) == "reports.test":
            pytest.skip("Cannot import lxml. Is it installed?")
        for step in [1] + sorted(testcase.output2):
            test_python_cmdline(testcase, step)


def test_python_cmdline(testcase: DataDrivenTestCase, step: int) -> None:
    assert testcase.old_cwd is not None, "test was not properly set up"
    # Write the program to a file.
    program = "_program.py"
    program_path = os.path.join(test_temp_dir, program)
    with open(program_path, "w", encoding="utf8") as file:
        for s in testcase.input:
            file.write(f"{s}\n")
    args = parse_args(testcase.input[0])
    custom_cwd = parse_cwd(testcase.input[1]) if len(testcase.input) > 1 else None
    args.append("--show-traceback")
    if "--error-summary" not in args:
        args.append("--no-error-summary")
    if "--show-error-codes" not in args:
        args.append("--hide-error-codes")
    # Type check the program.
    fixed = [python3_path, "-m", "mypy"]
    env = os.environ.copy()
    env.pop("COLUMNS", None)
    extra_path = os.path.join(os.path.abspath(test_temp_dir), "pypath")
    env["PYTHONPATH"] = PREFIX
    if os.path.isdir(extra_path):
        env["PYTHONPATH"] += os.pathsep + extra_path
    process = subprocess.Popen(
        fixed + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=os.path.join(test_temp_dir, custom_cwd or ""),
        env=env,
    )
    outb, errb = process.communicate()
    result = process.returncode
    # Split output into lines.
    out = [s.rstrip("\n\r") for s in str(outb, "utf8").splitlines()]
    err = [s.rstrip("\n\r") for s in str(errb, "utf8").splitlines()]

    if "PYCHARM_HOSTED" in os.environ:
        for pos, line in enumerate(err):
            if line.startswith("pydev debugger: "):
                # Delete the attaching debugger message itself, plus the extra newline added.
                del err[pos : pos + 2]
                break

    # Remove temp file.
    os.remove(program_path)
    # Compare actual output to expected.
    if testcase.output_files:
        # Ignore stdout, but we insist on empty stderr and zero status.
        if err or result:
            raise AssertionError(
                "Expected zero status and empty stderr%s, got %d and\n%s"
                % (" on step %d" % step if testcase.output2 else "", result, "\n".join(err + out))
            )
        check_test_output_files(testcase, step)
    else:
        if testcase.normalize_output:
            out = normalize_error_messages(err + out)
        obvious_result = 1 if out else 0
        if obvious_result != result:
            out.append(f"== Return code: {result}")
        expected_out = testcase.output if step == 1 else testcase.output2[step]
        # Strip "tmp/" out of the test so that # E: works...
        expected_out = [s.replace("tmp" + os.sep, "") for s in expected_out]
        assert_string_arrays_equal(
            expected_out,
            out,
            "Invalid output ({}, line {}){}".format(
                testcase.file, testcase.line, " on step %d" % step if testcase.output2 else ""
            ),
        )


def parse_args(line: str) -> list[str]:
    """Parse the first line of the program for the command line.

    This should have the form

      # cmd: mypy <options>

    For example:

      # cmd: mypy pkg/
    """
    m = re.match("# cmd: mypy (.*)$", line)
    if not m:
        return []  # No args; mypy will spit out an error.
    return m.group(1).split()


def parse_cwd(line: str) -> str | None:
    """Parse the second line of the program for the command line.

    This should have the form

      # cwd: <directory>

    For example:

      # cwd: main/subdir
    """
    m = re.match("# cwd: (.*)$", line)
    return m.group(1) if m else None
