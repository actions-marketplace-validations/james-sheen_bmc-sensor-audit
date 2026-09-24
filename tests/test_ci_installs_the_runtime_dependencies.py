"""CI does not install this package, so it must install what the package needs.

The version matrix and the engine canary both run the suite against the source
tree with the package DELIBERATELY not installed -- a test asserting that a bare
module path fails without `PYTHONPATH` is only a real negative that way.

That was free while this project had no dependencies. The split moved the
domain-neutral half to `presence-audit`, the vertical imports it at module
scope, and the whole suite stopped collecting on a runner while passing locally,
because a local editable install had quietly supplied it. Every one of the five
interpreters failed identically and none of them said anything a local run
could have predicted.

So these assert the two halves that have to stay true together: the workflows
install the runtime dependencies, and they get that list from the file that
declares it rather than from a copy.

AND A THIRD, ADDED 2026-09-24 BECAUSE THE FIRST TWO PASSED WHILE IT WAS
FALSE. The canary installed `arbiter-engine` at a range written out in its
own YAML, and satisfied `test_the_workflow_derives_the_list` because the SAME
line also called the helper for the runtime list. One line, two halves, one
of them a copy -- and the guard read the half that was derived. When the real
range moved, the canary went on installing the old one and failed on the
suite's pin check, which is a canary going red for its own bookkeeping.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

sys.path.insert(0, str(ROOT / "tools"))
import runtime_deps                                     # noqa: E402


def _install_lines(path):
    """LOGICAL lines that RUN a pip install, not lines that mention one.

    The first version of this matched any line containing the words, and caught
    a comment in `checks.yml` explaining what an editable install cannot see.
    A predicate that is not the claim, in a test written to catch a predicate
    that was not its claim.

    CONTINUATIONS ARE JOINED, added 2026-09-24. It read PHYSICAL lines, so the
    moment an install was wrapped with a backslash everything after the first
    line left this predicate's reach -- the derivation the guard looks for, and
    equally any copy it should have refused. Found by wrapping one: the guard
    went red for the half of the command it could still see. A checker that a
    line break defeats is not checking the command.
    """
    out, buffer = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if buffer is not None:
            buffer += " " + stripped.rstrip("\\").strip()
            if not stripped.endswith("\\"):
                out.append(buffer)
                buffer = None
            continue
        if stripped.startswith("#"):
            continue
        if "pip install" in stripped:
            if stripped.endswith("\\"):
                buffer = line.rstrip().rstrip("\\").rstrip()
            else:
                out.append(line)
    if buffer is not None:                      # an unterminated continuation
        out.append(buffer)
    return out


class TestTheHelperReadsTheRealList:
    def test_it_returns_the_declared_runtime_dependencies(self):
        deps = runtime_deps.runtime_dependencies(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert deps, "no runtime dependencies found; the parser or the file moved"
        assert any(d.startswith("presence-audit") for d in deps), deps

    def test_it_excludes_the_optional_groups(self):
        """An extra is by definition something the package runs without.
        Installing one here would hide a missing guard instead of revealing it."""
        deps = runtime_deps.runtime_dependencies(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert not any("arbiter-engine" in d or "pytest" in d for d in deps), deps

    def test_the_fallback_parser_agrees_with_tomllib(self):
        """The matrix starts at 3.10 and `tomllib` is 3.11+, so the regex path
        is the one that runs on the oldest interpreter -- and a fallback that
        disagreed with the real parser would be worse than no fallback.

        Skipped where there is no `tomllib` to compare against, which is the
        3.10 leg -- the very interpreter the fallback exists for. That is not
        circular: the fallback is EXERCISED there by every other test in this
        file, and only the agreement check needs both parsers present.
        """
        tomllib = pytest.importorskip(
            "tomllib", reason="3.11+; the fallback is exercised here regardless")
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        via_toml = list(tomllib.loads(text)["project"]["dependencies"])
        import re
        block = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
        via_regex = re.findall(r'"([^"]+)"', block.group(1))
        assert via_regex == via_toml, (via_regex, via_toml)

    def test_it_runs_as_a_command(self):
        out = subprocess.run([sys.executable, str(ROOT / "tools" / "runtime_deps.py")],
                             capture_output=True, text=True, cwd=str(ROOT))
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip(), "printed nothing; the workflow would install nothing"


def _jobs(path):
    """`(name, lines)` per job. Line-based, like `_install_lines`, and for the
    same reason: this suite runs with nothing but pytest installed, so a YAML
    parser here would be a dependency added to read a workflow."""
    out, name, lines = [], None, []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line[:2] == "  " and line[2:3] not in (" ", "-", "#", "") and line.rstrip().endswith(":"):
            if name:
                out.append((name, lines))
            name, lines = line.strip().rstrip(":"), []
        elif name:
            lines.append(line)
    if name:
        out.append((name, lines))
    return out


class TestEveryJobThatRunsTheSuiteInstallsThem:
    """The population is every install line IN A JOB THAT RUNS THE SUITE and
    that does NOT install the package itself. Named by file so a new workflow
    has to be added here deliberately rather than inherit a pass by being
    unseen.

    **The job condition is load-bearing and was missing.** The predicate was
    every install line in the file, which is wider than the sentence above it --
    and the first job to be added that installs something without running the
    suite failed this, correctly by the code and wrongly by the claim. A range
    sweep installs `virtualenv` and then builds its own environments, each of
    which installs the package properly; requiring it to install this package's
    runtime dependencies into a directory nothing imports from would be
    satisfying a check rather than passing one.
    """

    @pytest.mark.parametrize("name", ["checks.yml", "canary.yml"])
    def test_the_workflow_derives_the_list(self, name):
        path = WORKFLOWS / name
        assert path.is_file(), f"{name} is gone; this test now measures nothing"
        runs_suite = [lines for job, lines in _jobs(path)
                      if any("pytest" in l for l in lines)]
        assert runs_suite, f"no job in {name} runs the suite; this measures nothing"
        # THROUGH `_install_lines`, not a second copy of its predicate. This
        # rebuilt the filter inline and therefore kept reading physical lines
        # after the helper learned to join continuations -- the same two-copies
        # shape this file exists to catch, one level in.
        joined = _install_lines(path)
        in_suite_jobs = {l.strip() for lines in runs_suite for l in lines}
        installs = [l for l in joined
                    if any(part.strip() in in_suite_jobs
                           for part in l.split("  ") if part.strip())
                    or l.strip() in in_suite_jobs
                    or any(l.strip().startswith(s2.rstrip("\\").strip())
                           for s2 in in_suite_jobs if "pip install" in s2)]
        installs = [l for l in installs if "-e ." not in l and "build" not in l]
        assert installs, f"no suite-install line found in {name}"
        for line in installs:
            assert "runtime_deps.py" in line, (
                f"{name} installs test tooling but not the package's own runtime "
                f"dependencies, so the suite cannot collect:\n  {line.strip()}")

    def test_no_workflow_hardcodes_the_dependency_name(self):
        """A copy of the range is a copy that drifts from the one that binds,
        and the drift is invisible until the two disagree."""
        for path in WORKFLOWS.glob("*.yml"):
            for line in _install_lines(path):
                assert "presence-audit" not in line, (
                    f"{path.name} names the dependency literally; derive it "
                    f"from pyproject instead:\n  {line.strip()}")


class TestNoWorkflowWritesOutARangeItCouldRead:
    """A version range for a DECLARED dependency, written into a workflow.

    The sibling check above asks whether the derivation is present. This asks
    whether a copy is -- and they are not the same question, which is the whole
    reason this class exists: one line satisfied the first while failing this.

    Scoped to distributions `pyproject.toml` declares. A workflow pinning a
    BUILD TOOL is not this defect: nothing else in the repository states that
    version, so there is no second copy to drift from. What cannot stand is a
    range for something the packaging metadata already ranges, because then two
    files answer *what does this project need* and only one of them binds.
    """

    @staticmethod
    def _declared_distributions() -> set:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        names = set()
        for spec in runtime_deps.runtime_dependencies(text):
            names.add(re.split(r"[<>=!~\[ ]", spec, 1)[0].strip())
        for group in ("detect", "dev"):
            try:
                for spec in runtime_deps.extra_dependencies(text, group):
                    names.add(re.split(r"[<>=!~\[ ]", spec, 1)[0].strip())
            except SystemExit:
                continue
        return {n for n in names if n}

    def test_the_population_is_not_empty(self):
        """A derivation that finds nothing makes every assertion below vacuous,
        and a vacuous pass here reads exactly like a clean sweep."""
        assert len(self._declared_distributions()) >= 2, (
            self._declared_distributions())

    @pytest.mark.parametrize("name", sorted(
        p.name for p in (ROOT / ".github" / "workflows").glob("*.yml")))
    def test_no_install_line_pins_a_declared_dependency(self, name):
        declared = self._declared_distributions()
        offenders = []
        for line in _install_lines(WORKFLOWS / name):
            for dist in declared:
                if re.search(re.escape(dist) + r"\s*(==|>=|<=|~=|<|>)\s*[0-9]",
                             line):
                    offenders.append((dist, line.strip()))
        assert not offenders, (
            f"{name} writes out a version range for a dependency "
            f"`pyproject.toml` already declares; read it with "
            f"`tools/runtime_deps.py --extra <group>` instead, or the two will "
            f"disagree and only one of them will bind:\n  "
            + "\n  ".join(f"{d}: {l}" for d, l in offenders))


class TestTheHelperReadsAnExtraToo:
    def test_it_returns_the_declared_extra(self):
        deps = runtime_deps.extra_dependencies(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8"), "detect")
        assert any(d.startswith("arbiter-engine") for d in deps), deps

    def test_an_unknown_extra_is_refused_rather_than_empty(self):
        """An empty install list is indistinguishable from a successful one, and
        a canary that installs nothing passes every assertion it makes about a
        dependency it has not got."""
        with pytest.raises(SystemExit):
            runtime_deps.extra_dependencies(
                (ROOT / "pyproject.toml").read_text(encoding="utf-8"), "nope")

    def test_it_runs_as_a_command(self):
        out = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "runtime_deps.py"),
             "--extra", "detect"],
            capture_output=True, text=True, cwd=str(ROOT))
        assert out.returncode == 0, out.stderr
        assert "arbiter-engine" in out.stdout, out.stdout
