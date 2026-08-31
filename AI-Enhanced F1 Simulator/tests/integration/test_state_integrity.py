import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path
import queue
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "llm"))

# Import module under test
import ai_core


class TestStateAndDataIntegrity(unittest.TestCase):

    def setUp(self):
        """Reset the LLM Circuit Breaker before each test run."""
        ai_core._reset_llm_breaker()

    def test_circuit_breaker_resets_when_new_race_starts(self):
        """Verify that starting a new race resets the LLM circuit breaker from the previous session."""
        # Simulate an LLM outage/failure from a previous race session that tripped the breaker
        ai_core._trip_llm_breaker()
        self.assertTrue(
            ai_core._llm_breaker_is_open(),
            "Circuit breaker should be open (tripped) after a simulated failure."
        )

        # Simulate user starting a new race session via ai_queue_consumer_loop
        test_queue = queue.Queue()
        stop_event = threading.Event()
        mock_audio = MagicMock()

        # Pre-set stop_event so the consumer loop completes initialization and exits immediately
        stop_event.set()

        # Pass is_llm_available=True as happens during a fresh session startup
        ai_core.ai_queue_consumer_loop(
            event_queue=test_queue,
            audio_manager=mock_audio,
            stop_event=stop_event,
            is_llm_available=True
        )

        # Assert that the circuit breaker state was reset
        self.assertFalse(
            ai_core._llm_breaker_is_open(),
            "Starting a new race session should clear the residual circuit breaker state."
        )

    @patch("ai_core.apply_guardrail")
    @patch("ai_core.retrieve")
    @patch("ai_core.ask_race_engineer")
    def test_process_error_queries_llm_after_breaker_reset(
        self, mock_ask_llm, mock_retrieve, mock_apply_guardrail
    ):
        """Verify that newly queued events successfully call the LLM once the breaker is reset."""
        # Setup mock behavior
        mock_ask_llm.return_value = "Brake slightly earlier into Turn 1 to maximize exit speed."
        mock_retrieve.return_value = ["Focus on smooth throttle application on exit."]
        mock_apply_guardrail.return_value = {
            "is_valid": True,
            "feedback": "Brake slightly earlier into Turn 1 to maximize exit speed."
        }

        # Trip breaker from previous session
        ai_core._trip_llm_breaker()

        # Reset breaker for the new session
        ai_core._reset_llm_breaker()

        # Construct a complete mock error event with all required telemetry fields
        sample_error_event = {
            "type": "poor_corner_exit",
            "tag": "exit_err",
            "session_id": "session_race_02",
            "lap_number": 1,
            "corner": "Turn 1",
            "message": "Low exit speed detected.",
            "coaching_hint": "Apply throttle earlier.",
            "telemetry": {
                "lap_distance": 1250.5,
                "speed_kmh": 145.2,
                "track_pos": 0.05,
                "angle": 0.01,
                "wheel_spin": 2.1,
                "lap_time": 42.8,
                "throttle": 0.85,
                "brake": 0.0,
                "steer": -0.02,
                "gear": 3,
                "rpm": 6800
            }
        }

        mock_audio = MagicMock()
        system_prompt = "You are an F1 race engineer providing concise feedback."

        # Process the single infraction error
        result = ai_core.process_single_error(
            error=sample_error_event,
            system_prompt=system_prompt,
            audio_manager=mock_audio,
            force_fallback=False
        )

        # Assertions
        mock_ask_llm.assert_called_once()
        self.assertIsNotNone(result)
        self.assertTrue(result.get("is_valid"))


if __name__ == "__main__":
    unittest.main()