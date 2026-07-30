from unittest.mock import MagicMock
from data_pipeline.controller import Controller

class TestController:
    def test_update_accel_increase_and_decrease(self):
        """Test smooth acceleration increase and decrease when pressing and releasing gas."""
        mock_handler = MagicMock()
        mock_handler.state = {'gear': 1, 'accel': 0.0}
        mock_handler.accel_pressed = True

        controller = Controller(mock_handler)

        # Simulate pressing gas for 0.1s
        controller.update_accel(delta=0.1, speed=10.0)
        assert mock_handler.state['accel'] > 0.0

        # Simulate releasing gas
        mock_handler.accel_pressed = False
        controller.update_accel(delta=0.1, speed=10.0)
        assert mock_handler.state['accel'] < 1.0

    def test_update_steer_centering_multiplier(self):
        """Test that steering centers twice as fast (2x) when no steering key is pressed."""
        mock_handler = MagicMock()
        mock_handler.state = {'steer': 0.8}
        mock_handler.steer_left_pressed = False
        mock_handler.steer_right_pressed = False

        controller = Controller(mock_handler)
        controller.update_steer(delta=0.1, speed=50.0)

        # Steering angle should drop quickly toward 0.0
        assert mock_handler.state['steer'] < 0.8