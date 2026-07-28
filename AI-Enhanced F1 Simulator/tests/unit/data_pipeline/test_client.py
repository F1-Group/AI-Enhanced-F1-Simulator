import pytest
import socket
from unittest.mock import MagicMock, patch
from data_pipeline.client import Client
from data_pipeline.cache import GameStatus

# Fixtures
@pytest.fixture
def mock_deps():
    """Create mock objects for Handler, Logger, and Cache."""
    handler = MagicMock()
    handler.state = {'accel': 1.0, 'brake': 0.0, 'steer': -0.1, 'gear': 1}
    logger = MagicMock()
    cache = MagicMock()
    return handler, logger, cache

@pytest.fixture
def client(mock_deps):
    handler, logger, cache = mock_deps
    return Client(handler, logger, cache)

# Tests
def test_status_property_syncs_with_cache(client):
    """Test that updating status automatically syncs with cache."""
    client.status = GameStatus.RACING

    assert client.status == GameStatus.RACING
    client.cache.set_status.assert_called_with(GameStatus.RACING)


def test_build_action_format(client):
    """Test that action string matches TORCS command format."""
    state = {'accel': 0.8, 'brake': 0.0, 'steer': 0.25, 'gear': 2}
    action_str = client._build_action(state)

    expected = "(accel 0.800) (brake 0.000) (steer 0.250) (gear 2) (focus 360) (meta 0)"
    assert action_str == expected

def test_handshake_success(client):
    """Test that receiving ***identified*** switches status to RACING."""
    mock_socket = MagicMock()
    mock_socket.recvfrom.return_value = (b'***identified***', ('127.0.0.1', 3001))
    client.socket = mock_socket

    client._handshake()

    assert client.status == GameStatus.RACING
    mock_socket.sendto.assert_called()

@patch("time.time")
def test_handshake_timeout_fails(mock_time, client):
    """Test that handshake times out after 10 seconds and sets ERROR status."""
    mock_time.side_effect = [0.0, 11.0]

    mock_socket = MagicMock()
    mock_socket.recvfrom.side_effect = socket.timeout
    client.socket = mock_socket

    client._handshake()

    assert client.status == GameStatus.ERROR


def test_loop_handles_shutdown_signal(client):
    """Test that receiving ***shutdown*** sets status to FINISHED and stops the loop."""
    client.status = GameStatus.RACING

    mock_socket = MagicMock()
    # First call clears buffer (BlockingIOError), second receives shutdown
    mock_socket.recvfrom.side_effect = [
        BlockingIOError(),
        (b"***shutdown***", ('127.0.0.1', 3001))
    ]
    client.socket = mock_socket

    client._loop()

    assert client.status == GameStatus.FINISHED


def test_loop_processes_telemetry_packet(client):
    """Test that receiving a valid telemetry packet updates handler, cache, and logger."""
    client.status = GameStatus.RACING
    valid_udp_packet = b"(curLapTime 11.49)(distFromStart 16.0)(speedX 30.0)(gear 1)"

    mock_socket = MagicMock()
    # 1st: clear buffer, 2nd: real data, 3rd: shutdown signal to exit loop
    mock_socket.recvfrom.side_effect = [
        BlockingIOError(),
        (valid_udp_packet, ('127.0.0.1', 3001)),
        (b"***shutdown***", ('127.0.0.1', 3001))
    ]
    client.socket = mock_socket

    client._loop()

    client.handler.update.assert_called()
    client.cache.update_telemetry.assert_called()
    client.logger.log_row.assert_called()


@patch("time.time")
def test_loop_timeout_over_5_seconds_triggers_error(mock_time, client):
    """Test that receiving no packets for over 5 seconds triggers ERROR status."""
    client.status = GameStatus.RACING
    mock_time.side_effect = [100.0, 106.0]

    mock_socket = MagicMock()
    mock_socket.recvfrom.side_effect = [
        BlockingIOError(),
        socket.timeout
    ]
    client.socket = mock_socket

    client._loop()

    assert client.status == GameStatus.ERROR
    client.cache.update_telemetry.assert_called_with(None)

def test_clean_up_calls_logger_and_handler_stop(client):
    """Test that _clean_up correctly releases logger and handler resources."""
    client._clean_up()

    client.logger.close.assert_called_once()
    client.handler.stop.assert_called_once()