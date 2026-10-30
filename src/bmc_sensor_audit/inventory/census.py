"""Count what a whole configuration corpus declares, and name the revision counted.

`declare` says what one board's configuration declares. This says what a corpus
declares -- every configuration file under a directory, normally the
`configurations/` directory of an `entity-manager` checkout -- so a count quoted
about the corpus can be taken again by anyone, at any revision, rather than
believed.

**Counts drift; shapes do not.** Every count this project has published about the
corpus moved within weeks, as files were added and renamed upstream, while every
shape it found held: some files are not strict JSON, one entry can declare dozens
of rails, names are templates, a threshold's `Direction` decides and its `Name` is
free text. So a count is only half a claim, and the census prints the other half:
the commit the directory is checked out at, and whether any file under it differs
from that commit. A directory outside a git checkout is still counted, and is said
to name no revision. The reader matters too -- what counts as one declaration has
changed between releases of this tool -- so the census names the version that
counted.

It reads exactly as `declare` and `coverage` read: the same parse, the same
classification of a `Type` into sensor, not a sensor or unrecognised, and the same
rule for a threshold whose name contradicts its `Direction`. Those are imported
here rather than restated, so a change to how this tool reads a board changes what
the census counts in the same edit.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from .. import __version__
from .entity_manager import (ANY_TEMPLATE, KNOWN_TEMPLATE, _DIRECTION_BOUND,
                             _expand, _name_asserts_bound, _strip_block_comments,
                             parse_config_text)
from .sensor_types import KINDS, NOT_A_SENSOR, SENSOR, UNRECOGNISED, classify

__all__ = ["CENSUS_FORMAT", "take_census", "revision_of", "census_as_text"]

CENSUS_FORMAT = "bmc-sensor-audit/census/1"

_NO_TYPE = "(no Type)"


def _ranked(counter: Counter) -> dict[str, int]:
    """Largest first, then by name, so the order is the same on every run."""
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _git(directory: Path, *args: str) -> subprocess.CompletedProcess:
    # `core.fsmonitor` is the one setting under which `git status` would run a
    # command named by the repository's own configuration. A clone never carries
    # it, but a census can be pointed at a tree that arrived some other way.
    return subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(directory),
                           *args], capture_output=True, text=True, timeout=60)


def revision_of(directory: str | Path) -> dict[str, Any]:
    """The commit `directory` is checked out at, and how many paths under it differ.

    `modified` counts every path under the directory that is not as that commit
    has it -- changed, added, deleted, untracked or ignored -- because each one is
    counted by a census or missing from it. When there is no commit to name, the
    result says why instead.
    """
    directory = Path(directory)
    try:
        head = _git(directory, "rev-parse", "--verify", "HEAD")
    except (OSError, subprocess.TimeoutExpired):
        return {"commit": None, "why": "git could not be run to ask"}
    if head.returncode:
        return {"commit": None, "why": "not inside a git checkout"}
    if not _git(directory, "ls-files", "--", ".").stdout.strip():
        return {"commit": None,
                "why": "inside a git checkout that tracks none of the files under it"}
    committed = _git(directory, "log", "-1", "--format=%cI", "HEAD").stdout.strip()
    changed = _git(directory, "status", "--porcelain", "--untracked-files=all",
                   "--ignored", "--", ".").stdout.splitlines()
    return {"commit": head.stdout.strip(), "committed": committed[:10] or None,
            "modified": len(changed)}


def take_census(directory: str | Path) -> dict[str, Any]:
    """Count every configuration file under `directory`, as this tool reads them.

    Returns a JSON-ready dict in the `CENSUS_FORMAT` layout. File paths in it are
    relative to `directory`.
    """
    root = Path(directory)
    found = not_strict = arrays = 0
    unreadable: list[dict[str, str]] = []
    kinds: Counter = Counter()
    by_type = {kind: Counter() for kind in KINDS}
    templated = disabled = 0
    variables: Counter = Counter()
    unknown_tokens: Counter = Counter()
    anomalies: Counter = Counter()
    labelled = rails = most_rails = rails_unnamed = 0
    thresholds = 0
    directions: Counter = Counter()
    spellings: Counter = Counter()
    contradictions: list[dict[str, Any]] = []

    for path in _expand([root], "*.json"):
        found += 1
        relative = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            unreadable.append({"file": relative, "reason": f"cannot read: {exc}"})
            continue

        # The reader's own reading: what `declare` and `coverage` would expect.
        declaration = parse_config_text(text, source=relative)
        unreadable += [{"file": relative, "reason": reason}
                       for _, reason in declaration.unreadable]
        for sensor in declaration.sensors:
            kind = classify(sensor.type)
            kinds[kind] += 1
            by_type[kind][sensor.type or _NO_TYPE] += 1
            disabled += sensor.disabled_in_config
            if sensor.is_templated:
                templated += 1
                tokens = ANY_TEMPLATE.findall(sensor.name)
                for variable in {match.group(0).lower() for token in tokens
                                 if (match := KNOWN_TEMPLATE.match(token))}:
                    variables[variable] += 1
                for token in tokens:
                    if not KNOWN_TEMPLATE.match(token):
                        unknown_tokens[token] += 1
        for anomaly in declaration.anomalies:
            anomalies[anomaly.kind] += 1
        if not declaration.files_read:
            continue

        # The same document, walked for what a declaration does not keep: how the
        # file is written, every threshold once, and the rails each entry declares.
        data = json.loads(_strip_block_comments(text))
        try:
            json.loads(text)
        except json.JSONDecodeError:
            not_strict += 1
        arrays += isinstance(data, list)
        for record in data if isinstance(data, list) else [data]:
            exposes = record.get("Exposes") if isinstance(record, dict) else None
            for entry in exposes if isinstance(exposes, list) else []:
                if not isinstance(entry, dict):
                    continue
                bounds = [t for t in entry.get("Thresholds") or [] if isinstance(t, dict)]
                for threshold in bounds:
                    thresholds += 1
                    name = str(threshold.get("Name", ""))
                    direction = str(threshold.get("Direction", ""))
                    directions[direction] += 1
                    spellings[name] += 1
                    claimed = _name_asserts_bound(name)
                    bound = _DIRECTION_BOUND.get(direction)
                    if claimed and bound and claimed != bound:
                        contradictions.append({
                            "file": relative, "entry": entry.get("Name"),
                            "label": threshold.get("Label"), "name": name,
                            "direction": direction, "value": threshold.get("Value")})
                labels = entry.get("Labels")
                if isinstance(labels, list) and labels:
                    named = {threshold.get("Label") for threshold in bounds}
                    labelled += 1
                    rails += len(labels)
                    most_rails = max(most_rails, len(labels))
                    rails_unnamed += sum(1 for label in labels if label not in named)

    return {
        "format": CENSUS_FORMAT,
        "counted_by": f"bmc-sensor-audit {__version__}",
        "directory": str(directory),
        "revision": revision_of(root),
        "files": {"found": found, "not_strict_json": not_strict,
                  "top_level_arrays": arrays, "unreadable": unreadable},
        "declarations": {
            "total": sum(kinds.values()),
            SENSOR: kinds[SENSOR], NOT_A_SENSOR: kinds[NOT_A_SENSOR],
            UNRECOGNISED: kinds[UNRECOGNISED],
            "by_type": {kind: _ranked(by_type[kind]) for kind in KINDS},
            "templated": templated, "template_variables": _ranked(variables),
            "unknown_template_tokens": _ranked(unknown_tokens),
            "disabled": disabled},
        "labels": {"entries": labelled, "rails": rails, "most_in_one_entry": most_rails,
                   "rails_no_threshold_names": rails_unnamed},
        "thresholds": {"total": thresholds, "by_direction": _ranked(directions),
                       "by_name": _ranked(spellings),
                       "name_contradicts_direction": contradictions},
        "anomalies": _ranked(anomalies),
    }


def _revision_line(revision: dict[str, Any]) -> str:
    if revision.get("commit") is None:
        return (f"  no revision: {revision['why']}, so these counts cannot be taken "
                f"again at the same corpus")
    when = f", committed {revision['committed']}" if revision.get("committed") else ""
    if not revision["modified"]:
        return f"  at {revision['commit']}{when}, no file under it modified"
    return (f"  at {revision['commit']}{when}, but {revision['modified']} path(s) under "
            f"it differ from that commit, so these are not that revision's counts")


def census_as_text(census: dict[str, Any], *, top: int = 10) -> str:
    """The census as a person reads it. Everything here is also in the JSON."""
    files, decl = census["files"], census["declarations"]
    labels, bounds = census["labels"], census["thresholds"]
    by_type = decl["by_type"]
    rows: list[tuple[str, int, str, list[str]]] = []

    def row(label: str, number: int, after: str = "", below: list[str] = ()) -> None:
        rows.append((label, number, after, list(below)))

    unknown = [f"    {token} in {n} name(s) names no variable this tool knows"
               for token, n in decl["unknown_template_tokens"].items()]
    row("files", files["found"])
    row("  not strict JSON (C-style comments)", files["not_strict_json"])
    row("  holding an array of records", files["top_level_arrays"])
    row("  unreadable", len(files["unreadable"]), "",
        [f"    {item['file']}: {item['reason']}" for item in files["unreadable"]])
    row("declarations, as this tool reads them", decl["total"])
    row("  sensors", decl[SENSOR])
    row("  not sensors", decl[NOT_A_SENSOR], f", of {len(by_type[NOT_A_SENSOR])} types")
    row("  of a type this tool does not know", decl[UNRECOGNISED],
        f", of {len(by_type[UNRECOGNISED])} types")
    row("  with a runtime template in the name", decl["templated"],
        ", using " + (" ".join(decl["template_variables"]) or "none"), unknown)
    row("  disabled in the configuration", decl["disabled"])
    row("entries declaring rails in `Labels`", labels["entries"])
    row("  rails declared", labels["rails"],
        f", at most {labels['most_in_one_entry']} in one entry")
    row("  rails no threshold names", labels["rails_no_threshold_names"])
    row("thresholds", bounds["total"])
    for direction, count in bounds["by_direction"].items():
        row(f"  Direction {direction!r}", count)
    row("  spellings of their Name", len(bounds["by_name"]))
    conflicts = []
    for c in bounds["name_contradicts_direction"]:
        where = " ".join(str(part) for part in (c["entry"], c["label"]) if part)
        conflicts.append(f"    {c['file']}: {where} -- named {c['name']!r}, "
                         f"Direction {c['direction']!r}, at {c['value']}")
    row("  Name contradicting Direction", len(conflicts), "", conflicts)

    width = max(len(label) for label, *_ in rows) + 1
    digits = max(len(str(number)) for _, number, *_ in rows)
    lines = [f"census of {census['directory']}, counted by {census['counted_by']}",
             _revision_line(census["revision"]), ""]
    for label, number, after, below in rows:
        lines.append(f"{label:<{width}}{number:>{digits}}{after}")
        lines += below

    def table(heading: str, counts: dict[str, int]) -> list[str]:
        if not counts:
            return []
        wide = max(len(name) for name in counts) + 1
        return ["", heading] + [f"  {name:<{wide}}{n:>{digits}}" for name, n in counts.items()]

    lines += table("anomalies found reading them, by kind:", census["anomalies"])
    ranked = dict(list(by_type[NOT_A_SENSOR].items())[:max(top, 0)])
    lines += table(f"not sensors, the {len(ranked)} largest of "
                   f"{len(by_type[NOT_A_SENSOR])} types:", ranked)
    lines += table("types this tool does not know -- each reported, never judged absent:",
                   by_type[UNRECOGNISED])
    return "\n".join(lines)
