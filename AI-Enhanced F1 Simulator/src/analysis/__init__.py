"""Team 2 slow layer: spatial alignment, subtraction and error detection."""

from .alignment import align_nearest, build_baseline, compute_deltas, distance_grid
from .error_detection import detect_corners, detect_errors
from .lap_utils import add_global_columns, load_telemetry, split_laps
