import pandas as pd
from unittest.mock import patch, mock_open, MagicMock
from data_pipeline.logger import CSVLogger

def test_logger_initialization_defaults():
    """Test default attributes of CSVLogger on initialization."""
    logger = CSVLogger()
    assert logger.filename is None
    assert logger.writer is None
    assert logger.csv_file is None
    assert logger.row_count == 0

def test_log_row_handles_none_or_empty_safely():
    """Test that log_row safely skips None or empty dict without crashing."""
    logger = CSVLogger()

    logger.log_row(None)
    logger.log_row({})
    
    assert logger.row_count == 0

def test_safe_read_csv_when_no_file_created():
    """Test that safe_read_csv returns an empty DataFrame if no file is created."""
    logger = CSVLogger()
    df = logger.safe_read_csv()
    
    assert isinstance(df, pd.DataFrame)
    assert df.empty

@patch("os.path.exists", return_value=False)
def test_safe_read_csv_when_file_does_not_exist(mock_exists):
    """Test that safe_read_csv returns an empty DataFrame if file does not exist."""
    logger = CSVLogger()
    logger.filename = "/test/test00.csv"
    
    df = logger.safe_read_csv()
    
    assert df.empty

@patch("builtins.open", new_callable=mock_open)
@patch("os.makedirs")
@patch("os.path.abspath", side_effect=lambda x: f"/abs/{x}")
def test_create_file_prepares_directories_and_pointer(mock_abs, mock_makedirs, mock_file):
    """Test that create_file sets up directories, opens CSV, and writes latest_data.txt."""
    logger = CSVLogger(output_dir="/test/dir")
    logger.create_file()

    mock_makedirs.assert_called_once_with("/test/dir", exist_ok=True)

    assert mock_file.call_count == 2
    assert logger.csv_file is not None


def test_log_row_writes_header_on_first_row():
    """Test that the first row writes the CSV header based on dict keys."""
    logger = CSVLogger()
    mock_file = MagicMock()
    logger.csv_file = mock_file

    sample_data = {"lap_time": 11.49, "speed_kmh": 112.1, "gear": 1}
    logger.log_row(sample_data)

    assert logger.row_count == 1
    assert logger.writer is not None
    mock_file.flush.assert_called_once()

def test_close_without_creating_file_does_not_crash():
    """Test that calling close before create_file does not crash."""
    logger = CSVLogger()
    logger.close()

@patch("os.path.exists", return_value=True)
@patch("os.remove")
def test_close_deletes_file_if_row_count_less_than_50(mock_remove, mock_exists):
    """Test that close deletes the file if row count is less than 50."""
    logger = CSVLogger()
    mock_file = MagicMock()
    logger.csv_file = mock_file
    logger.filename = "/test/telemetry_test.csv"
    logger.row_count = 10

    logger.close()

    mock_file.close.assert_called_once()
    mock_remove.assert_called_once_with("/test/telemetry_test.csv")

@patch("os.path.exists", return_value=True)
@patch("os.remove")
def test_close_keeps_file_if_row_count_is_50_or_more(mock_remove, mock_exists):
    """Test that close keeps the file if row count is 50 or more."""
    logger = CSVLogger()
    mock_file = MagicMock()
    logger.csv_file = mock_file
    logger.filename = "/test/telemetry_test.csv"
    logger.row_count = 50

    logger.close()

    mock_file.close.assert_called_once()
    mock_remove.assert_not_called()