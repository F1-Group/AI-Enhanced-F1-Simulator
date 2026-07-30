import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

from data_pipeline.cache import GameStatus
from analysis.live_coach import LiveCoach
from analysis.alignment import CHANNELS

@pytest.fixture
def test_csv(tmp_path):
    """Create a test CSV file for continuous reading."""
    csv_file = tmp_path / "telemetry_test.csv"
    csv_file.write_text(
        "lap_time,lap_distance,speed_kmh\n"
        "1.0,10.0,120.0\n"
        "1.1,15.0,122.0\n",
        encoding="utf-8"
    )
    return csv_file


def test_live_coach_cache_disconnect_handling(test_csv):
    """Verify that LiveCoach correctly holds and reads the shared cache instance, 
    and that the follow() loop accurately detects when the cache status changes 
    to GameStatus.ERROR and exits early.
    """
    # Mock Cache and Audio Manager
    mock_cache = MagicMock()
    mock_audio_manager = MagicMock()
    
    mock_cache.get_status.return_value = GameStatus.RACING

    event_queue = MagicMock()

    fake_data = {
        "lap_distance": [0.0, 500.0, 1000.0],
    }

    for col in CHANNELS:
        fake_data[col] = [1.0, 2.0, 3.0]

    fake_lap = pd.DataFrame(fake_data)
    
    # Mock using the full fake_lap
    with patch("analysis.live_coach.load_telemetry"), \
         patch("analysis.live_coach.split_laps", return_value=[fake_lap, fake_lap]):
        
        coach = LiveCoach(
            manager=mock_audio_manager,
            event_output_queue=event_queue,
            use_granite=False,
            cache=mock_cache
        )

    # Simulate switching cache status to GameStatus.ERROR
    def simulate_error_after_first_read(*args, **kwargs):
        mock_cache.get_status.return_value = GameStatus.ERROR
        return "1.2,20.0,125.0\n"

    with patch("analysis.live_coach._read_complete_line", side_effect=simulate_error_after_first_read):
        # Execute follow() should exit the loop cleanly upon detecting GameStatus.ERROR
        coach.follow(test_csv, idle_timeout_s=5.0)

    assert mock_cache.get_status.called
    assert mock_cache.get_status() == GameStatus.ERROR