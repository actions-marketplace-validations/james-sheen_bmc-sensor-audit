"""What the pin probe removes on its way in, and what it must not.

Every run of this probe builds one virtualenv per release in range. The `finally`
that removes them covers exceptions and nothing else -- not SIGKILL, not a
timed-out CI step, not a reboot -- so a run killed that way leaves gigabytes
behind with nothing alive to clean up after it. `reap_abandoned` closes that on
the way in, because the run that leaked is not around to do it on the way out.

A tool that deletes directories it did not create in this process needs its
refusals tested harder than its removals, and that is most of what is here.
"""

from __future__ import annotations

import ast
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import probe_pin  # noqa: E402


@pytest.fixture(scope="module")
def probe_pin_source() -> str:
    return Path(probe_pin.__file__).read_text()


# --- the reaper: what a killed run leaves, and what is not ours to remove ----
#
# `finally` covers the exceptions. It does not cover SIGKILL, a timed-out CI step
# or a reboot, and a run that dies that way leaves one virtualenv per release with
# nothing alive to remove them. The reaper closes that, which means it deletes
# directories nobody asked it to -- so most of what follows is about what it must
# NOT touch. Every removal case is paired with a refusal, because a reaper that
# matched nothing at all would pass every refusal on its own.


def _work(root, name, *, age_hours=None, environments=False, other=False):
    """Build a directory in `root` and optionally age it. Ages via `os.utime`
    rather than by waiting, so the threshold is exercised rather than approximated."""
    made = root / name
    made.mkdir()
    if environments:
        (made / "in-1.0").mkdir()
        (made / "in-1.0" / "pyvenv.cfg").write_text("home = /usr\n")
    if other:
        (made / "notes.txt").write_text("somebody else's file\n")
    if age_hours is not None:
        when = time.time() - age_hours * 3600
        os.utime(made, (when, when))
    return made


@pytest.fixture()
def tmp_as_tempdir(tmp_path, monkeypatch):
    """Point the reaper at a directory of our own.

    The real one holds other people's work and this test deletes things."""
    monkeypatch.setattr(probe_pin.tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


def test_a_killed_runs_environments_are_reaped(tmp_as_tempdir) -> None:
    """The case the reaper exists for: old, ours, and full of virtualenvs."""
    left = _work(tmp_as_tempdir, probe_pin.WORK_PREFIX + "killed",
                 age_hours=24, environments=True)
    assert probe_pin.reap_abandoned() == [left.name]
    assert not left.exists()


def test_the_empty_shell_of_a_run_that_died_early_is_reaped(tmp_as_tempdir) -> None:
    """A run killed before it built anything leaves a directory with nothing in
    it. Reaped too, or the shells accumulate forever at one inode apiece."""
    left = _work(tmp_as_tempdir, probe_pin.WORK_PREFIX + "early", age_hours=24)
    assert probe_pin.reap_abandoned() == [left.name]
    assert not left.exists()


def test_a_probe_running_right_now_is_never_reaped(tmp_as_tempdir) -> None:
    """The age gate, and the reason there is one. A concurrent run's directory is
    indistinguishable from an abandoned one except by age, and reaping it would
    delete the environments out from under a live probe."""
    live = _work(tmp_as_tempdir, probe_pin.WORK_PREFIX + "live", environments=True)
    assert probe_pin.reap_abandoned() == []
    assert live.exists()


def test_the_age_gate_is_the_reason_and_not_a_reaper_that_matches_nothing(
        tmp_as_tempdir) -> None:
    """Both arms, on ONE directory. The test above passes just as well if the
    reaper is broken and matches nothing, so the same young directory is offered
    to a zero threshold here and must then be removed."""
    live = _work(tmp_as_tempdir, probe_pin.WORK_PREFIX + "live", environments=True)
    assert probe_pin.reap_abandoned() == []
    assert live.exists(), "the real threshold should have declined it"
    assert probe_pin.reap_abandoned(older_than=0) == [live.name]
    assert not live.exists(), "so the refusal above was the age and nothing else"


def test_a_directory_wearing_the_prefix_that_is_not_ours_is_left_alone(
        tmp_as_tempdir) -> None:
    """The prefix is a convention, not a proof of ownership. A directory holding
    files but no environments was made by something else."""
    theirs = _work(tmp_as_tempdir, probe_pin.WORK_PREFIX + "theirs",
                   age_hours=24, other=True)
    assert probe_pin.reap_abandoned(older_than=0) == []
    assert theirs.exists()


def test_a_file_wearing_the_prefix_is_not_removed(tmp_as_tempdir) -> None:
    """Not hypothetical: files named for this prefix have sat in /tmp alongside
    the work directories. The reaper removes trees, so a file it matched would be
    a file it deleted for no reason."""
    theirs = tmp_as_tempdir / (probe_pin.WORK_PREFIX + "ready.json")
    theirs.write_text("{}\n")
    os.utime(theirs, (time.time() - 24 * 3600,) * 2)
    assert probe_pin.reap_abandoned(older_than=0) == []
    assert theirs.exists()


def test_a_symlink_never_takes_the_reaper_outside_the_temporary_directory(
        tmp_as_tempdir) -> None:
    """The target is built to look EXACTLY reapable -- old, and holding
    environments -- so nothing but the link-ness saves it.

    This pins an OUTCOME and not the `is_symlink()` guard above it. Deleting that
    guard does not make this test red, because `shutil.rmtree` refuses a symbolic
    link on its own and the reaper swallows the error: the directory survives for
    a second reason. The guard stays because the refusal should be this tool's
    decision rather than a detail of the standard library that a later refactor
    could quietly step around -- but a test cannot claim to prove a guard whose
    removal changes nothing, so this one does not.
    """
    target = tmp_as_tempdir / "real-and-not-ours"
    target.mkdir()
    (target / "in-1.0").mkdir()
    (target / "in-1.0" / "pyvenv.cfg").write_text("home = /usr\n")
    when = time.time() - 24 * 3600
    os.utime(target, (when, when))
    link = tmp_as_tempdir / (probe_pin.WORK_PREFIX + "link")
    link.symlink_to(target)
    os.utime(link, (when, when), follow_symlinks=False)

    assert probe_pin.reap_abandoned() == []
    assert (target / "in-1.0" / "pyvenv.cfg").exists(), "reaped through the link"


def test_another_tools_directories_are_not_reaped(tmp_as_tempdir) -> None:
    """Several packages in this family run the same probe against the same /tmp.
    Each reaps only its own."""
    sibling = _work(tmp_as_tempdir, "some-other-tool-", age_hours=24,
                    environments=True)
    assert probe_pin.reap_abandoned(older_than=0) == []
    assert sibling.exists()


def test_the_prefix_has_one_definition(probe_pin_source) -> None:
    """The reaper matches on the prefix and `mkdtemp` writes it. Written twice the
    two drift, and a reaper that matches nothing reads exactly like a tool that
    never leaks -- the failure would be invisible in both directions."""
    calls = [node for node in ast.walk(ast.parse(probe_pin_source))
             if isinstance(node, ast.Call)
             and getattr(node.func, "attr", None) == "mkdtemp"]
    assert calls, "no mkdtemp call found; this check would pass over nothing"
    for call in calls:
        prefix = [kw.value for kw in call.keywords if kw.arg == "prefix"]
        assert prefix, "mkdtemp without a prefix cannot be reaped by prefix"
        assert "WORK_PREFIX" in ast.dump(prefix[0]), (
            "the prefix is written out at the mkdtemp rather than taken from "
            "WORK_PREFIX, so the reaper and the writer can disagree")


def test_the_reaper_is_actually_called(probe_pin_source) -> None:
    """Defined and never invoked is the shape this very change shipped in three
    of its five copies on the first pass: the function was present, the call site
    was not, and every test of the function still passed."""
    tree = ast.parse(probe_pin_source)
    mains = [node for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef) and node.name == "main"]
    assert mains, "no main() found; this check would pass over nothing"
    called = {getattr(node.func, "id", None)
              for main in mains for node in ast.walk(main)
              if isinstance(node, ast.Call)}
    assert "reap_abandoned" in called, (
        "main() never calls reap_abandoned, so nothing reaps and the tests above "
        "only prove the function would work if anything ran it")
