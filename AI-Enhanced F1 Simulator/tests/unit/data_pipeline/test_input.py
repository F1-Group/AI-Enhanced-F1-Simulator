from unittest.mock import MagicMock
from data_pipeline.input import InputHandler

class TestInputHandler:
    def test_gear_shift_limits(self):
        """Test that gear shifts do not exceed min (-1) and max (6) limits."""
        handler = InputHandler()
        handler.state['gear'] = 6

        handler.shift_up()
        assert handler.state['gear'] == 6

        handler.state['gear'] = -1
        handler.shift_down()
        assert handler.state['gear'] == -1

    def test_update_from_queue_event(self):
        """Test that keyboard events from the queue correctly update state."""
        handler = InputHandler()
        mock_msg = {
            'up': True,
            'down': False,
            'left': True,
            'right': False,
            'shift_up': True,
            'shift_down': False,
        }

        # Mock queue to simulate getting data without real process delay
        mock_queue = MagicMock()
        mock_queue.empty.side_effect = [False, True]
        mock_queue.get_nowait.return_value = mock_msg
        handler._queue = mock_queue

        state = handler.update()

        assert handler.accel_pressed is True
        assert handler.steer_left_pressed is True
        assert state['gear'] == 2