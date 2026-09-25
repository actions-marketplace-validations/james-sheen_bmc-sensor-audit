"""A prediction filed in one run is still there in the next.

The engine has kept a durable prediction ledger since 0.2.3 and
`SqlitePredictionLedger` became a supported top-level name in 0.2.6. Nothing in
this package constructed one, so every prediction it filed lived in memory and
died with the process -- which means `calibration()` never had an earlier
horizon to grade against, and the figure that scores this bridge against a
random walk could not be non-null here however many times it ran.

WHAT THIS TEST IS AND IS NOT. It holds the WIRING: that a ledger handed to a
session persists to a file, and that a second session opened on the same file
reads the first one's rows back. It does NOT claim a calibration figure. That
needs a horizon to elapse against real telemetry, and no test can make time
pass; the number belongs in a run's findings, not here.

NO EXTRA IS DECLARED FOR THIS. Measured, not assumed: the ledger imports
nothing outside the standard library, and the engine publishes no `resident`
extra of its own. An extra that installs nothing would tell a caller they had
gained a capability they already had.
"""

import pathlib

import pytest

#: SKIPPED PER TEST, NOT PER MODULE, and that is a deliberate difference from
#: `test_engine_bridge.py`. A module-level `importorskip` removes the whole
#: file from the collected set when the engine is absent, which changes the gap
#: between this suite's two populations -- and the README states that gap and
#: names the one module that accounts for it. A new file skipping at import
#: would make that sentence false without touching it, so the skip is moved
#: inside where it costs the count nothing.
def _engine():
    return pytest.importorskip(
        "arbiter_engine", reason="the ledger is an engine capability; this "
                                 "package answers Stage 1 without one")


def _ledger(path):
    from arbiter_engine import SqlitePredictionLedger

    return SqlitePredictionLedger(str(path))


class TestTheLedgerIsDurable:

    def test_the_supported_name_is_top_level(self):
        """A deep import would make this wiring rest on a path the engine does
        not promise to keep."""
        assert hasattr(_engine(), "SqlitePredictionLedger")

    def test_a_file_is_created_where_it_was_asked_for(self, tmp_path):
        _engine()
        path = tmp_path / "ledger.sqlite"
        ledger = _ledger(path)
        ledger.close()
        assert path.is_file(), "the ledger kept nothing on disk"

    def test_a_second_session_reads_the_first_one_s_rows(self, tmp_path):
        """THE CLAIM. Two sessions, one file, and the rows survive the gap --
        which is the whole difference between a ledger and a variable."""
        _engine()
        from arbiter_engine.api import EngineSession

        path = tmp_path / "ledger.sqlite"
        first = _ledger(path)
        EngineSession(ledger=first)
        first.record_prediction(
            entity_id="psu1", probability=0.5, horizon_s=3600.0,
            indicator="voltage_v", severity="medium")
        before = len(list(first.records()))
        first.close()
        assert before >= 1, "nothing was filed, so nothing can be read back"

        second = _ledger(path)
        EngineSession(ledger=second)
        after = list(second.records())
        second.close()
        assert len(after) == before, (
            f"the second session sees {len(after)} row(s) where the first "
            f"filed {before}; a ledger that does not outlive its process is a "
            f"variable with a filename")


class TestTheSessionAcceptsIt:

    def test_engine_session_takes_a_ledger(self):
        _engine()
        import inspect

        from arbiter_engine.api import EngineSession

        assert "ledger" in inspect.signature(EngineSession.__init__).parameters

    def test_the_cli_offers_the_flag(self):
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "src/bmc_sensor_audit/cli.py").read_text()
        assert '"--ledger"' in src, "the detect path cannot be asked for one"
        assert "SqlitePredictionLedger" in src
