"""`census` counts a whole configuration corpus, and names the revision it counted.

The counts this project published about the upstream corpus were taken by hand,
each at whatever revision a checkout happened to hold, and several were later
quoted beside counts taken by a different version of this reader -- a share whose
numerator and denominator came from two readings. `census` replaces them with a
command anyone can run. So what is pinned here is that it counts what this tool
reads, each count separately, and that it never states a revision it did not count.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bmc_sensor_audit import cli
from bmc_sensor_audit.inventory.census import (CENSUS_FORMAT, census_as_text,
                                               revision_of, take_census)
from bmc_sensor_audit.inventory.entity_manager import TEMPLATE_VARS, load_declaration

UPSTREAM = Path(__file__).parent / "fixtures" / "upstream"

BOARD = {"Name": "Board", "Exposes": [
    {"Name": "Inlet Temp", "Type": "TMP75", "Thresholds": [
        {"Name": "upper critical", "Direction": "greater than", "Value": 80, "Severity": 1},
        {"Name": "lower non critical", "Direction": "less than", "Value": 5, "Severity": 0}]},
    {"Name": "Zone 1", "Type": "Pid"},
    {"Name": "$bus Mystery", "Type": "Mystery"},
    {"Name": "Off Temp", "Type": "TMP75", "Status": "disabled"},
]}

# Not strict JSON, and an array of two records: the two file shapes a strict
# reader of one object per file would get wrong.
HSC = """/* a hot-swap controller and a fan */
[{"Name": "Power", "Exposes": [
   {"Name": "HSC", "Type": "pmbus", "Labels": ["vin", "iout1", "temp1"],
    "Thresholds": [{"Name": "upper critical", "Direction": "less than",
                    "Value": 105, "Label": "temp1", "Severity": 1}]}]},
 {"Name": "Fans", "Exposes": [{"Name": "Fan $index", "Type": "I2CFan"}]}]
"""


@pytest.fixture
def corpus(tmp_path) -> Path:
    root = tmp_path / "configurations"
    (root / "vendor").mkdir(parents=True)
    (root / "board.json").write_text(json.dumps(BOARD))
    (root / "vendor" / "hsc.json").write_text(HSC)
    return root


def _git(cwd: Path, *args: str, **env: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=census", "-c", "user.email=census@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
        env={**os.environ, **env}).stdout.strip()


needs_git = pytest.mark.skipif(shutil.which("git") is None,
                               reason="the revision is asked of git, and git is not installed")


class TestEachCount:
    """Every count, on a corpus small enough to count by eye."""

    def test_files(self, corpus):
        files = take_census(corpus)["files"]
        assert (files["found"], files["not_strict_json"], files["top_level_arrays"],
                files["unreadable"]) == (2, 1, 1, [])

    def test_declarations_by_kind(self, corpus):
        declared = take_census(corpus)["declarations"]
        # Three sensors on the board, three rails and a fan in the array file.
        assert (declared["total"], declared["sensor"], declared["not_a_sensor"],
                declared["unrecognised"]) == (8, 6, 1, 1)
        assert declared["by_type"]["not_a_sensor"] == {"Pid": 1}
        assert declared["by_type"]["unrecognised"] == {"Mystery": 1}
        assert declared["by_type"]["sensor"] == {"pmbus": 3, "TMP75": 2, "I2CFan": 1}

    def test_templates_and_disabled(self, corpus):
        declared = take_census(corpus)["declarations"]
        assert declared["templated"] == 2
        assert declared["template_variables"] == {"$bus": 1, "$index": 1}
        assert declared["unknown_template_tokens"] == {}
        assert declared["disabled"] == 1

    def test_labels(self, corpus):
        assert take_census(corpus)["labels"] == {
            "entries": 1, "rails": 3, "most_in_one_entry": 3, "rails_no_threshold_names": 2}

    def test_thresholds(self, corpus):
        bounds = take_census(corpus)["thresholds"]
        assert bounds["total"] == 3
        assert bounds["by_direction"] == {"less than": 2, "greater than": 1}
        assert bounds["by_name"] == {"upper critical": 2, "lower non critical": 1}
        assert bounds["name_contradicts_direction"] == [{
            "file": "vendor/hsc.json", "entry": "HSC", "label": "temp1",
            "name": "upper critical", "direction": "less than", "value": 105}]

    def test_a_token_naming_no_known_variable_is_counted_apart(self, corpus):
        (corpus / "odd.json").write_text(json.dumps(
            {"Exposes": [{"Name": "$slot Temp", "Type": "TMP75"}]}))
        declared = take_census(corpus)["declarations"]
        assert declared["templated"] == 3
        assert declared["unknown_template_tokens"] == {"$slot": 1}
        assert "$slot" not in declared["template_variables"]


class TestTheCensusReadsAsThisToolReads:
    """On the vendored upstream files: the census and `declare` read one corpus."""

    def test_there_is_a_corpus_here_to_read(self):
        """NON-VACUITY: each equality below holds over nothing as well."""
        census = take_census(UPSTREAM)
        assert census["files"]["found"] >= 13
        assert census["files"]["not_strict_json"] >= 1
        assert census["files"]["top_level_arrays"] >= 1
        assert census["labels"]["entries"] >= 1

    def test_it_declares_what_declare_declares(self):
        census = take_census(UPSTREAM)["declarations"]
        read = load_declaration([UPSTREAM])
        assert census["total"] == len(read.sensors)
        assert census["templated"] == len(read.templated)
        assert census["disabled"] == len(read.disabled)
        assert census["sensor"] + census["not_a_sensor"] + census["unrecognised"] \
            == census["total"]

    def test_its_contradictions_are_the_readers_own(self):
        """The two upstream defects, found by the rule the reader applies when it
        reads a board -- not by a second rule written for counting."""
        census = take_census(UPSTREAM)
        found = sorted((c["file"], c["value"])
                       for c in census["thresholds"]["name_contradicts_direction"])
        assert found == [("meta/fbyv2.json", 105), ("meta/fbyv35.json", 55)]
        assert census["anomalies"]["threshold_direction_conflict"] == len(found)

    def test_every_variable_it_reports_is_one_the_reader_knows(self):
        used = take_census(UPSTREAM)["declarations"]["template_variables"]
        assert used, "the vendored files carry templates; none was counted"
        assert {name[1:] for name in used} <= set(TEMPLATE_VARS)


@needs_git
class TestTheRevision:
    """A count is a claim about a revision, so the census names one only when it
    can, and says when the files are not that revision's."""

    def test_outside_a_checkout_it_names_none(self, corpus, monkeypatch):
        # Discovery stops at the test's own directory, whatever holds it.
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(corpus.parent))
        revision = revision_of(corpus)
        assert revision == {"commit": None, "why": "not inside a git checkout"}
        assert "no revision: not inside a git checkout" in census_as_text(take_census(corpus))

    def test_it_names_the_commit_and_its_commit_date(self, corpus):
        """The COMMIT date, not the author's: a change written on the 13th and
        merged on the 14th puts its files in the tree on the 14th. Dating a corpus
        by its last change's author date dates it to before it existed."""
        _git(corpus, "init", "-q")
        _git(corpus, "add", ".")
        _git(corpus, "commit", "-q", "-m", "corpus",
             GIT_AUTHOR_DATE="2026-08-13T15:20:11+08:00",
             GIT_COMMITTER_DATE="2026-08-14T02:04:26+00:00")
        revision = revision_of(corpus)
        assert revision == {"commit": _git(corpus, "rev-parse", "HEAD"),
                            "committed": "2026-08-14", "modified": 0}
        assert "no file under it modified" in census_as_text(take_census(corpus))

    def test_a_changed_or_added_file_is_said(self, corpus):
        _git(corpus, "init", "-q")
        _git(corpus, "add", ".")
        _git(corpus, "commit", "-q", "-m", "corpus")
        (corpus / "board.json").write_text(json.dumps({"Exposes": []}))
        (corpus / "new.json").write_text(json.dumps(BOARD))
        assert revision_of(corpus)["modified"] == 2
        text = census_as_text(take_census(corpus))
        assert "2 path(s) under it differ from that commit" in text
        assert "not that revision's counts" in text

    def test_a_directory_its_checkout_does_not_track_names_none(self, tmp_path, corpus):
        """Inside somebody else's repository, the commit is theirs, not the corpus's."""
        _git(tmp_path, "init", "-q")
        (tmp_path / "README").write_text("unrelated\n")
        _git(tmp_path, "add", "README")
        _git(tmp_path, "commit", "-q", "-m", "unrelated")
        assert revision_of(corpus)["commit"] is None
        assert "tracks none" in revision_of(corpus)["why"]


class TestTheCommand:

    def test_it_counts_and_exits_clean(self, corpus, capsys):
        assert cli.main(["census", str(corpus)]) == 0
        out = capsys.readouterr().out
        assert "declarations, as this tool reads them" in out
        assert "vendor/hsc.json: HSC temp1 -- named 'upper critical'" in out

    def test_the_json_names_its_format_and_its_reader(self, corpus, capsys):
        from bmc_sensor_audit import __version__
        assert cli.main(["census", str(corpus), "--json"]) == 0
        census = json.loads(capsys.readouterr().out)
        assert census["format"] == CENSUS_FORMAT
        assert census["counted_by"] == f"bmc-sensor-audit {__version__}"
        assert census["declarations"]["total"] == 8

    def test_a_path_that_is_not_a_directory_is_refused(self, corpus, capsys):
        assert cli.main(["census", str(corpus / "board.json")]) == 2
        assert "is not a directory" in capsys.readouterr().err

    def test_a_directory_with_no_configuration_is_refused(self, tmp_path, capsys):
        assert cli.main(["census", str(tmp_path)]) == 2
        assert "no configuration file was found" in capsys.readouterr().err

    def test_files_that_declare_nothing_are_refused(self, tmp_path, capsys):
        (tmp_path / "schema.json").write_text(json.dumps({"$schema": "x", "type": "object"}))
        assert cli.main(["census", str(tmp_path)]) == 2
        assert "none of which declares a sensor" in capsys.readouterr().err

    def test_an_unreadable_file_makes_the_count_partial(self, corpus, capsys):
        (corpus / "broken.json").write_text("{ not json")
        assert cli.main(["census", str(corpus)]) == 2
        captured = capsys.readouterr()
        assert "broken.json: not parseable as JSON" in captured.out
        assert "counts are of part of the corpus" in captured.err
        # Counted, and still counted correctly around it.
        assert take_census(corpus)["declarations"]["total"] == 8
