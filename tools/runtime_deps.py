#!/usr/bin/env python3
"""Print this project's RUNTIME dependencies, one per line -- or an extra's.

CI deliberately does not install this package: a test asserting that a bare
module path fails without `PYTHONPATH` has to stay a real negative, and an
installed package makes it vacuous. That was free while this project had no
dependencies at all. It stopped being free when the domain-neutral half moved
to `presence-audit`, because the vertical imports it at module scope -- the
whole suite then failed to collect on a runner, having passed locally, where
an editable install had quietly supplied it.

So the runtime dependencies are installed and the package is not, and the list
comes from `pyproject.toml` rather than from a second copy in a workflow file.
A hand-kept copy is a version range that drifts from the one that binds, and
the drift is invisible until the day the two disagree about something.

`tomllib` is 3.11+, and the version matrix starts at 3.10, so the fallback is
not laziness -- it is the older interpreter this has to run on.

`--extra <name>` PRINTS ONE OPTIONAL GROUP, and it was added the day the
sentence above came true for the thing it did not cover. The engine canary
installed `arbiter-engine>=0.1.8,<0.2` written out in its own YAML, under a
comment saying *the range is what a consumer actually gets, so the range is
what gets tested*. It was testing a COPY of the range. The day the real one
moved, the canary went on installing the old one, the suite's pin guard
refused, and the job failed for a reason that had nothing to do with the
engine's behaviour -- which is the one thing a canary must never do.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def runtime_dependencies(text: str) -> list[str]:
    """The `[project] dependencies` array, in declaration order.

    Deliberately NOT the optional-dependency groups: an extra is by definition
    something the package runs without, and installing one here would hide a
    missing guard rather than reveal it.
    """
    try:
        import tomllib
        return list(tomllib.loads(text).get("project", {}).get("dependencies", []))
    except ModuleNotFoundError:
        pass
    block = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
    if block is None:
        return []
    return re.findall(r'"([^"]+)"', block.group(1))


def extra_dependencies(text: str, name: str) -> list[str]:
    """One `[project.optional-dependencies]` group, in declaration order.

    Refuses an unknown name rather than printing nothing: an empty install list
    is indistinguishable from a successful one, and a canary that installs
    nothing passes every behavioural assertion it makes about a dependency it
    has not got.
    """
    groups = {}
    try:
        import tomllib
        groups = tomllib.loads(text).get("project", {}).get(
            "optional-dependencies", {})
    except ModuleNotFoundError:
        section = re.search(
            r"^\[project\.optional-dependencies\](.*?)(?=^\[|\Z)",
            text, re.S | re.M)
        if section:
            for key, body in re.findall(
                    r"^(\w[\w-]*)\s*=\s*\[(.*?)\]", section.group(1),
                    re.S | re.M):
                groups[key] = re.findall(r'"([^"]+)"', body)
    if name not in groups:
        raise SystemExit(
            f"no `{name}` extra in pyproject.toml; declared: "
            f"{sorted(groups)}")
    return list(groups[name])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--extra", metavar="NAME",
                        help="print this optional-dependency group instead of "
                             "the runtime dependencies")
    args = parser.parse_args()
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    deps = (extra_dependencies(text, args.extra) if args.extra
            else runtime_dependencies(text))
    for dep in deps:
        print(dep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
