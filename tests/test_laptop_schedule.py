from datetime import datetime
import json
from unittest.mock import patch

import pytest

from scripts import laptop_tick as tick


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=tick.IST)


def previous(day, hour, slot="morning"):
    return {"slot": f"2026-11-{day:02}-{slot}", "started_at": at(day, hour).timestamp()}


def test_only_current_window_after_two_days_off():
    assert tick.decision(at(5, 10), previous(2, 9))[0] == "2026-11-05-morning"
    assert tick.decision(at(5, 20), previous(2, 9))[0] == "2026-11-05-evening"
    assert tick.decision(at(5, 8), previous(2, 9))[0] is None


def test_one_attempt_per_window_even_when_failed_or_interrupted():
    for status in ("started", "failed", "finished"):
        assert tick.decision(at(2, 18), dict(previous(2, 9), status=status))[0] is None


def test_late_catchup_does_not_run_again_at_evening_boundary():
    assert tick.decision(at(2, 19), previous(2, 18))[0] is None
    assert tick.decision(at(2, 19), previous(2, 13))[0] == "2026-11-02-evening"
    assert tick.decision(at(3, 0), previous(2, 18))[0] is None


def test_clock_backwards_does_not_repeat():
    assert tick.decision(at(2, 19), previous(3, 9))[0] is None


def test_corrupt_state_fails_closed(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{")
    with patch.object(tick, "STATE", state), pytest.raises(json.JSONDecodeError):
        tick.read_state()


def test_claim_persisted_before_process_launch_and_second_tick_skips(tmp_path):
    (tmp_path / ".env").touch()
    (tmp_path / "profile.yaml").touch()
    data = tmp_path / "data"
    state = data / "state.json"
    clock = type("Clock", (), {"now": staticmethod(lambda tz: at(2, 10))})

    def launch(*args, **kwargs):
        assert json.loads(state.read_text())["status"] == "started"
        raise OSError("simulated launch failure")

    with patch.object(tick, "ROOT", tmp_path), patch.object(tick, "DATA", data), patch.object(tick, "STATE", state), patch.object(tick, "datetime", clock), patch.object(tick, "online", return_value=True), patch.object(tick.subprocess, "run", side_effect=launch) as run, patch("sys.argv", ["laptop_tick.py"]):
        assert tick.main() == 1
        assert tick.main() == 0
        assert run.call_count == 1


def test_offline_does_not_consume_window(tmp_path):
    clock = type("Clock", (), {"now": staticmethod(lambda tz: at(2, 10))})
    state = tmp_path / "state.json"
    with patch.object(tick, "DATA", tmp_path), patch.object(tick, "STATE", state), patch.object(tick, "datetime", clock), patch.object(tick, "online", return_value=False), patch.object(tick.subprocess, "run") as run, patch("sys.argv", ["laptop_tick.py"]):
        assert tick.main() == 0
        assert not state.exists()
        run.assert_not_called()


def test_busy_pipeline_skips_without_consuming_window(tmp_path):
    state = tmp_path / "state.json"
    with (tmp_path / "pipeline.lock").open("a") as lock:
        tick.fcntl.flock(lock, tick.fcntl.LOCK_EX | tick.fcntl.LOCK_NB)
        with patch.object(tick, "DATA", tmp_path), patch.object(tick, "STATE", state), patch.object(tick.subprocess, "run") as run, patch("sys.argv", ["laptop_tick.py"]):
            assert tick.main() == 0
            assert not state.exists()
            run.assert_not_called()


def test_status_does_not_consume_window_or_contact_network(tmp_path):
    state = tmp_path / "state.json"
    with patch.object(tick, "DATA", tmp_path), patch.object(tick, "STATE", state), patch.object(tick, "online") as network, patch.object(tick.subprocess, "run") as run, patch("sys.argv", ["laptop_tick.py", "--status"]):
        assert tick.main() == 0
        assert not state.exists()
        network.assert_not_called()
        run.assert_not_called()
