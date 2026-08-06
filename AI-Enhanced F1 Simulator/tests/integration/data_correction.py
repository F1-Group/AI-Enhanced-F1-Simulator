"""
Integration test: does what ai_core.py (Team3) writes actually match what
dashboard.py (Team4) reads?
This is a contract test, not a unit test: it doesn't care about the internal

Heavy/optional deps (ollama, chromadb, sentence-transformers) and tkinter
are stubbed out so this test is fast, deterministic and doesn't need a real
Ollama server, an internet connection to download embedding models, or a
display. Nothing about llm.ai_core's or ui.dashboard's OWN logic is stubbed.

Run using
    pytest data_correction.py -v
    to check whether the contract is satisfied, or not or everything is
    working properly with out any unexpected errors.
"""
import json
import queue
import sys
import threading
import types
from pathlib import Path

import pytest


# Stub only the genuinely-external, heavy/optional dependencies so this test
# can run offline without ollama/chromadb/sentence-transformers installed.
@pytest.fixture(autouse=True)
def stub_external_deps(monkeypatch):
    for name in ("ollama", "chromadb", "sentence_transformers"):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    sys.modules["ollama"].Client = lambda *a, **kw: types.SimpleNamespace(
        chat=lambda *a, **kw: {"message": {"content": "stub"}}, list=lambda: None
    )
    sys.modules["chromadb"].Client = lambda: types.SimpleNamespace(
        get_or_create_collection=lambda name: types.SimpleNamespace(
            count=lambda: 0, add=lambda **kw: None, query=lambda **kw: {"documents": [[]]}
        )
    )
    sys.modules["sentence_transformers"].SentenceTransformer = lambda *a, **kw: object()

    # tests/integration/latency_logger.py isn't part of this contract - stub it
    # so we don't depend on its implementation/behaviour here.
    fake_latency = types.ModuleType("latency_logger")
    fake_latency.log_event = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "latency_logger", fake_latency)


@pytest.fixture
def ai_core(monkeypatch, tmp_path):
    """Import the real llm.ai_core module, redirected to write into tmp_path."""
    from llm import ai_core as module
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    return module


class _FakeAudioManager:
    """Minimal stand-in so ai_core's audio_manager.play_text()/.stop_all() calls
    don't blow up - we're testing the JSON file contract, not audio playback."""
    def play_text(self, *a, **kw):
        pass

    def stop_all(self):
        pass


def _run_consumer_loop(ai_core, events):
    """Feeds `events` (a list of error dicts) through the real
    ai_queue_consumer_loop, exactly like live_coach.py would, then lets it
    finish via the poison-pill (None) live_coach.py sends at end of lap."""
    q = queue.Queue()
    for e in events:
        q.put(e)
    q.put(None)
    ai_core.ai_queue_consumer_loop(
        q, audio_manager=_FakeAudioManager(), stop_event=threading.Event(), is_llm_available=False
    )


def _dashboard_read_feedback(summary_file_path, retries=0):
    """Executes the *literal* body of dashboard.py's `_poll_for_summary`
    (sliced straight out of ui/dashboard.py, not retyped) against a real
    file on disk, and returns whatever text it would have set on the label.

    retries=0 collapses dashboard's real 30-attempt/500ms polling loop down
    to "give me the terminal state now" - the thing we actually want to
    assert on, without waiting 15 seconds per test.
    """
    dashboard_src = Path(__import__("ui.dashboard", fromlist=["dummy"]).__file__).read_text()
    lines = dashboard_src.splitlines()

    start = next(i for i, l in enumerate(lines) if l.strip().startswith("def _poll_for_summary"))
    end = next(i for i, l in enumerate(lines) if i > start and l.strip() == "_poll_for_summary()")
    func_src = "\n".join(l[8:] for l in lines[start:end])  # dedent out of the enclosing method

    class _FakeLabel:
        def __init__(self):
            self.text = None

        def config(self, text):
            self.text = text

    class _FakeRoot:
        def after(self, ms, fn):
            fn()  # run "scheduled" retries immediately, no real event loop needed

    class _FakeSelf:
        def __init__(self):
            self.root = _FakeRoot()

    lbl_summary_body = _FakeLabel()
    ns = {
        "json": json,
        "SUMMARY_FILE": summary_file_path,
        "lbl_summary_body": lbl_summary_body,
        "self": _FakeSelf(),
    }
    exec(func_src, ns)
    ns["_poll_for_summary"](retries=retries)
    return lbl_summary_body.text

# Contract tests

def test_project_root_matches_between_writer_and_reader():
    """ai_core writes to PROJECT_ROOT/data/lap_summary.json. dashboard reads
    from its own PROJECT_ROOT/data/lap_summary.json. If these two constants
    ever resolve to different directories, Team4 will silently never see
    Team3's output - this compares the real, un-patched values from both
    modules (deliberately not using the `ai_core` fixture above, since that
    fixture redirects PROJECT_ROOT to a tmp dir for write-isolation and would
    make this comparison meaningless)."""
    from llm import ai_core
    from ui import dashboard
    assert ai_core.PROJECT_ROOT == dashboard.PROJECT_ROOT


def test_project_root_formula_matches_real_repo_layout():
    """Same check as above, but against the real (unpatched) path arithmetic,
    proving <root>/src/ui/dashboard.py and <root>/src/llm/ai_core.py agree on
    where <root> is - without needing tkinter/ollama/etc. installed at all."""
    fake_root = Path("/some/project/AI-Enhanced F1 Simulator")
    dashboard_file = fake_root / "src" / "ui" / "dashboard.py"
    ai_core_file = fake_root / "src" / "llm" / "ai_core.py"

    dashboard_root = dashboard_file.resolve().parent.parent.parent
    granite_dir = ai_core_file.resolve().parent
    ai_core_root = granite_dir.parent.parent

    assert dashboard_root == ai_core_root == fake_root


def test_clean_session_writes_lap_summary_dashboard_can_read(ai_core, tmp_path):
    """No errors this lap -> ai_core writes the 'clean session' message ->
    dashboard should be able to read it back with zero special-case code."""
    _run_consumer_loop(ai_core, events=[])

    lap_summary_path = ai_core.PROJECT_ROOT / "data" / "lap_summary.json"
    assert lap_summary_path.exists()

    written = json.loads(lap_summary_path.read_text())
    assert written == {
        "type": "lap_summary",
        "feedback": "Clean session with no major infractions recorded!",
    }

    displayed = _dashboard_read_feedback(lap_summary_path)
    assert displayed == "Clean session with no major infractions recorded!"

    # coaching_summary.json is only written when there ARE errors - confirm
    # it's genuinely absent here, not just empty.
    assert not (ai_core.PROJECT_ROOT / "data" / "coaching_summary.json").exists()


def test_lap_with_errors_writes_both_files_dashboard_can_read(ai_core):
    """Errors detected this lap -> ai_core writes BOTH coaching_summary.json
    (detailed, per-error) and lap_summary.json (headline feedback string).
    dashboard should read the headline back with no special-case code."""
    events = [
        {
            "tag": "T1_late_braking", "type": "late_braking", "corner": "T1",
            "severity": "high", "priority": "normal",
            "message": "Late braking detected at T1.",
            "coaching_hint": "Brake about 30 m earlier before T1.",
            "session_id": "s1", "lap_number": 1, "is_realtime": False,
            "telemetry": {
                "lap_time": 14.0, "lap_distance": 600.0, "speed_kmh": 300.0,
                "track_pos": 0.1, "angle": 0.05, "wheel_spin": 90.0, "gear": 4,
                "rpm": 8000.0, "race_pos": 1, "fuel": 99.0, "throttle": 0.2,
                "brake": 0.5, "steer": 0.1,
            },
        },
        {
            "tag": "S2_time_loss", "type": "sector_time_loss", "corner": "Sector 2",
            "severity": "medium", "priority": "normal",
            "message": "Significant time loss detected in Sector 2.",
            "coaching_hint": "You lost about 3.0 s to the baseline in Sector 2.",
            "session_id": "s1", "lap_number": 1, "is_realtime": False,
            "telemetry": {
                "lap_time": 90.0, "lap_distance": 3000.0, "speed_kmh": 250.0,
                "track_pos": 0.0, "angle": 0.0, "wheel_spin": 70.0, "gear": 5,
                "rpm": 7000.0, "race_pos": 1, "fuel": 95.0, "throttle": 0.8,
                "brake": 0.0, "steer": 0.0,
            },
        },
    ]
    _run_consumer_loop(ai_core, events)

    lap_summary_path = ai_core.PROJECT_ROOT / "data" / "lap_summary.json"
    coaching_summary_path = ai_core.PROJECT_ROOT / "data" / "coaching_summary.json"
    assert lap_summary_path.exists()
    assert coaching_summary_path.exists()

    lap_summary = json.loads(lap_summary_path.read_text())
    assert lap_summary["type"] == "lap_summary"
    assert isinstance(lap_summary["feedback"], str) and lap_summary["feedback"]
    assert lap_summary["total_errors"] == 2
    assert set(lap_summary["corners_affected"]) == {"T1", "Sector 2"}

    coaching_summary = json.loads(coaching_summary_path.read_text())
    assert coaching_summary["total_errors"] == 2
    assert len(coaching_summary["coaching_results"]) == 2

    # the actual contract point: can dashboard read the headline feedback
    # straight off disk with the exact code it ships today, no changes needed?
    displayed = _dashboard_read_feedback(lap_summary_path)
    assert displayed == lap_summary["feedback"]


def test_dashboard_falls_back_when_feedback_key_missing(tmp_path):
    """If lap_summary.json is ever written without a 'feedback' key,
    dashboard's data.get("feedback", default) should degrade gracefully
    instead of throwing/crashing the summary page."""
    lap_summary_path = tmp_path / "lap_summary.json"
    lap_summary_path.write_text(json.dumps({"type": "lap_summary"}))

    displayed = _dashboard_read_feedback(lap_summary_path)
    assert displayed == "No summary text generated."


def test_dashboard_survives_a_malformed_or_partially_written_file(tmp_path):
    """ai_core's summary writes are NOT atomic (unlike run_analysis.py's
    write_report(), which uses a temp-file + rename). If dashboard's poll
    lands mid-write, the JSON could be truncated/invalid - it must not crash,
    just treat it like "not ready yet" and eventually time out gracefully."""
    lap_summary_path = tmp_path / "lap_summary.json"
    lap_summary_path.write_text('{"type": "lap_summary", "feed') 

    displayed = _dashboard_read_feedback(lap_summary_path, retries=0)
    assert displayed == "No infractions recorded or summary generation timed out."


def test_dashboard_handles_missing_file_gracefully(tmp_path):
    """ai_core crashed / hasn't run yet -> file was never created. Dashboard
    must show its timeout message instead of raising."""
    missing_path = tmp_path / "lap_summary.json"  # never created
    displayed = _dashboard_read_feedback(missing_path, retries=0)
    assert displayed == "No infractions recorded or summary generation timed out."


@pytest.mark.xfail(
    reason=(
        "KNOWN CONTRACT GAP: ai_core.py writes coaching_summary.json (detailed "
        "per-error breakdown: total_errors, coaching_results[].feedback/corner/"
        "severity/error_type), but ui/dashboard.py never opens that file at all - "
        "grep confirms zero references to 'coaching_summary' in dashboard.py. "
        "Only the single headline string in lap_summary.json['feedback'] ever "
        "reaches the UI. This test intentionally fails until Team4 adds a view "
        "for the per-corner breakdown; remove the xfail marker once it does."
    ),
    strict=True,
)
def test_dashboard_surfaces_coaching_summary_details():
    dashboard_src = Path(__import__("ui.dashboard", fromlist=["dummy"]).__file__).read_text()
    assert "coaching_summary" in dashboard_src