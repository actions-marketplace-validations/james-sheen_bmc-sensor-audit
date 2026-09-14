#!/usr/bin/env python3
"""Install every release this package's pins claim, and run the suite on each.

A version range is a claim about every release inside it. This package declares
two -- `presence-audit` as a hard dependency and `arbiter-engine` under the
`detect` extra -- and no suite can check either, because a suite runs against the
one release the resolver picked. By default that is the newest, which is the
release the claim is least likely to be wrong about. The floor is the interesting
end and it is the end nothing exercises.

So each in-range release goes into its own throwaway environment with this
package installed beside it, and the suite runs there. The release immediately
BELOW each floor goes in too, because a floor nothing below it breaks is a floor
nobody measured -- `--sweep` keeps walking down until something fails, which is
what turns the number into one.

**WRITTEN AFTER A RELEASE PROVED IT WAS NEEDED.** `arbiter-engine` 0.1.14 widened
the decline vocabulary, and a sibling bridge that pins it by range went from a
clean corpus to exit 2 on every release in its own range -- found by that
package's sweep, in CI, rather than by a consumer. This package pins the same
engine and had no such leg. Ported from `filing-balance-audit`, whose version
carries the reasoning in the comments below; the argument is not re-derived here.

    python3 tools/probe_pin.py
    python3 tools/probe_pin.py --sweep          # keep going below the floor
    python3 tools/probe_pin.py --keep           # leave the environments behind

Exit 0 every in-range release passed, 1 a declared claim is false, 2 the probe
could not run -- INCLUDING when a range holds no releases at all. *Every release
passed* is true of an empty set and means nothing, so an empty range is a failure
to measure and not a pass.

WHAT THIS DOES NOT DECIDE: whether a passing below-floor release means the floor
should drop. The pin is true either way. `floor-not-demonstrated` says only that
this probe is not where the number came from, and lowering it needs a reason a
person supplies.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
EVIDENCE = pathlib.Path(__file__).resolve().parent / "pin_evidence.json"

#: `name>=lo,<hi`. Deliberately narrow: this package declares two pins and a
#: general specifier parser would be a dependency added to read a dependency.
PIN = re.compile(r"([A-Za-z0-9._-]+)\s*(>=\s*[0-9][^,\"']*)\s*,\s*(<\s*[0-9][^,\"']*)")


def _version_key(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text))


def declared_pins() -> dict[str, tuple[str, str]]:
    """Read the pins out of `pyproject.toml`, never from a list written here.

    A list here would be a second record of the dependency and would go on
    reporting the old range after somebody edited the real one -- which is
    exactly the shape of failure a pin probe exists to catch, reproduced inside
    the probe.
    """
    found = {}
    for name, low, high in PIN.findall(PYPROJECT.read_text()):
        found[name] = (low.replace(" ", ""), high.replace(" ", ""))
    return found


def released(dist: str) -> list[str]:
    """Every non-yanked, non-prerelease version PyPI serves for `dist`."""
    url = f"https://pypi.org/pypi/{dist}/json"
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = json.load(response)
    out = []
    for version, files in (payload.get("releases") or {}).items():
        if not files or all(f.get("yanked") for f in files):
            continue
        if re.search(r"[a-zA-Z]", version):          # a, b, rc, dev
            continue
        out.append(version)
    return sorted(out, key=_version_key)


def split(versions: list[str], low: str, high: str) -> tuple[list[str], list[str]]:
    lo, hi = _version_key(low), _version_key(high)
    in_range = [v for v in versions if lo <= _version_key(v) < hi]
    if not in_range:
        return [], []
    beneath = [v for v in versions if _version_key(v) < _version_key(in_range[0])]
    return in_range, beneath


def exercise(dist: str, version: str, home: pathlib.Path) -> tuple[bool, str]:
    """Install that release plus this package, run the suite, report pass/fail."""
    made = subprocess.run([sys.executable, "-m", "virtualenv", "-q", str(home)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        return False, f"could not create an environment: {made.stderr.strip()[:120]}"
    pip, python = home / "bin" / "pip", home / "bin" / "python"

    # INSTALL THE PACKAGE AND EVERYTHING IT NEEDS FIRST, then force the one
    # release under test on top with `--no-deps`.
    #
    # The obvious order -- ask pip for the release and the package together --
    # is IMPOSSIBLE below the floor of a REQUIRED dependency, and pip says so:
    # a package that declares a REQUIRED dependency at `>=LOW` and is then asked
    # for the release below LOW beside it gets a ResolutionImpossible. That is pip being right, and it is also the
    # probe being asked the wrong question. The point of a below-floor leg is to
    # violate the declared floor deliberately and see what breaks, which needs
    # the resolver told to stand down rather than consulted.
    install = subprocess.run(
        [str(pip), "install", "-q", "--no-cache-dir", "pytest", f"{ROOT}[detect]"],
        capture_output=True, text=True)
    if install.returncode != 0:
        tail = (install.stderr or install.stdout).strip().splitlines()
        return False, f"install failed: {tail[-1][:140] if tail else '?'}"
    forced = subprocess.run(
        [str(pip), "install", "-q", "--no-cache-dir", "--no-deps",
         "--force-reinstall", f"{dist}=={version}"],
        capture_output=True, text=True)
    if forced.returncode != 0:
        tail = (forced.stderr or forced.stdout).strip().splitlines()
        return False, f"could not pin {dist}=={version}: {tail[-1][:120] if tail else '?'}"

    # PROVE THIS ENVIRONMENT EXERCISES WHAT IT INSTALLED, before trusting what it
    # reports. Running from outside the repository is not enough -- a
    # `conftest.py` that prepends the source tree defeats it from inside, which
    # is what happened here and invalidated two complete sweeps.
    #
    # The version alone would not have caught it: `__version__` reads installed
    # METADATA, so the shadowing copy reported the right number for the release
    # it was standing in front of. So the PATH is asserted, not the number.
    #
    # And the modules checked are the DIST UNDER TEST plus this package -- not a
    # hardcoded pair. The first version named both dependencies, so probing the
    # core failed on an engine that environment had no reason to hold.
    module = dist.replace("-", "_")
    proof = subprocess.run(
        [str(python), "-c",
         f"import os, {module} as d, bmc_sensor_audit as p;"
         "print(os.path.dirname(d.__file__));"
         "print(os.path.dirname(p.__file__))"],
        capture_output=True, text=True, cwd=str(home))
    paths = proof.stdout.strip().splitlines()
    if len(paths) != 2:
        return False, ("could not resolve what this environment imports: "
                       f"{proof.stderr.strip().splitlines()[-1][:120] if proof.stderr.strip() else '?'}")
    shadowed = [line for line in paths if "site-packages" not in line]
    if shadowed:
        return False, f"this environment does not exercise what it installed: {shadowed}"
    ran = subprocess.run(
        [str(python), "-m", "pytest", str(ROOT / "tests"), "-q",
         "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=str(home))
    tail = [ln for ln in (ran.stdout or "").strip().splitlines() if ln.strip()]
    return ran.returncode == 0, (tail[-1][:140] if tail else "no output")


def probe(dist: str, low: str, high: str, sweep: bool, keep: bool) -> dict:
    versions = released(dist)
    in_range, beneath = split(versions, low, high)
    print(f"\n{dist}{low},{high}")
    print(f"  in range : {', '.join(in_range) or '(none)'}")
    print(f"  below    : {', '.join(reversed(beneath)) or '(nothing released below)'}")
    if len(in_range) == 1:
        print("  note     : the range holds one release today, so *every release "
              "in range* is a claim about one")
    result = {"dist": dist, "specifier": f"{low},{high}",
              "in_range": in_range, "below": [], "failures": []}
    if not in_range:
        result["verdict"] = "empty-range"
        return result

    work = pathlib.Path(tempfile.mkdtemp(prefix="bsa-pin-"))
    try:
        for version in in_range:
            ok, note = exercise(dist, version, work / f"in-{version}")
            print(f"  in  {version}: {'pass' if ok else 'FAIL'} -- {note}")
            if not ok:
                result["failures"].append(version)
        walked = list(reversed(beneath)) if sweep else beneath[-1:]
        demonstrated = False
        for version in walked:
            ok, note = exercise(dist, version, work / f"under-{version}")
            print(f"  under {version}: {'pass' if ok else 'fail'} -- {note}")
            result["below"].append({"version": version, "passed": ok})
            if not ok:
                demonstrated = True
                break
        # THREE OUTCOMES, NOT TWO, and the middle one was being reported as the
        # first. `floor-holds` is only true when the release IMMEDIATELY below
        # the floor fails. Where something below it passes and a lower one
        # fails, the floor is higher than anything measured requires -- a
        # different fact, and the first version printed *the floor is the first
        # release above it that does not fail* about a floor two releases up.
        passed_below = [r["version"] for r in result["below"] if r["passed"]]
        if result["failures"]:
            result["verdict"] = "claim-false"
        elif demonstrated and not passed_below:
            result["verdict"] = "floor-holds"
        elif demonstrated:
            result["verdict"] = "floor-higher-than-measured"
        else:
            result["verdict"] = "floor-not-demonstrated"
        result["passed_below"] = passed_below
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sweep", action="store_true",
                    help="keep walking below the floor until something fails")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--out", type=pathlib.Path, default=EVIDENCE)
    args = ap.parse_args(argv)

    pins = declared_pins()
    if not pins:
        print("could-not-run: this package declares no pinned range, so there is "
              "nothing to exercise. An empty sweep reports the same as a clean "
              "one and means something else entirely", file=sys.stderr)
        return 2
    results = []
    for dist, (low, high) in sorted(pins.items()):
        try:
            results.append(probe(dist, low, high, args.sweep, args.keep))
        except (urllib.error.URLError, TimeoutError) as problem:
            print(f"could-not-run: PyPI is not reachable for {dist}: {problem}",
                  file=sys.stderr)
            return 2

    empty = [r["dist"] for r in results if r["verdict"] == "empty-range"]
    false = [r for r in results if r["verdict"] == "claim-false"]
    print(f"\n{len(results)} pin(s) probed, "
          f"{sum(len(r['in_range']) for r in results)} in-range release(s) run")
    for r in results:
        if r["verdict"] == "floor-holds":
            print(f"  note: {r['dist']}: the floor holds -- "
                  f"{r['below'][-1]['version']} is the release immediately below "
                  f"it and it fails")
        elif r["verdict"] == "floor-higher-than-measured":
            passed = r["passed_below"]
            print(f"  note: {r['dist']}: FLOOR HIGHER THAN MEASURED -- "
                  f"{', '.join(passed)} also "
                  f"{'passes' if len(passed) == 1 else 'pass'}, and "
                  f"{r['below'][-1]['version']} is the first that does not. The "
                  f"pin is still true. Whether the floor should drop is a "
                  f"decision, not a measurement: this suite is the oracle and it "
                  f"reaches only as far as it reaches")
        elif r["verdict"] == "floor-not-demonstrated":
            print(f"  note: {r['dist']}: FLOOR NOT DEMONSTRATED -- every release "
                  f"tried below it also passes. The pin is still true; the number "
                  f"is not one this probe produced. Re-run with --sweep, and if "
                  f"nothing fails the floor is higher than anything measured "
                  f"requires")
    code = 2 if empty else 1 if false else 0
    if empty:
        print(f"  could-not-run: {', '.join(empty)} -- the range holds no "
              f"releases, and *every release passed* is true of an empty set")
    args.out.write_text(json.dumps(
        {"pins": results, "exit": code, "swept": args.sweep}, indent=1) + "\n")
    print(f"  evidence: {args.out}")
    print(f"EXIT={code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
