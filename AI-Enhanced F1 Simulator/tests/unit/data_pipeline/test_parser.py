import pytest
from data_pipeline.parser import clean_my_data

def test_clean_my_data_valid_torcs_packet():
    """Check if TORCS UDP data is clean and correct."""
    raw_udp = (
        "(angle 0.0025599)(curLapTime 11.49)(damage 0)"
        "(distFromStart 16.0696)(distRaced 41.0708)"
        "(fuel 99.9914)(gear 1)(lastLapTime 0)(opponents 200 "
        "200 200 200 200 200 200 200 200 200 200 200 200 200 "
        "200 200 200 200 200 200 200200 200 200 200 200 200 "
        "200 200 200 200 200 200 200 200 200)(racePos 1)"
        "(rpm 5235.99)(speedX 31.1387)(speedY 2.09123e-008)"
        "(speedZ 5.12956e-005)(track 3.42104 3.54415 4.47546 "
        "5.98628 10.0733 13.3454 19.9912 40.4351 200 200 200 "
        "73.3396 37.3448 25.1787 19.1014 11.4283 8.56986 "
        "6.80641 6.579)(trackPos 0.315795)(wheelSpinVel "
        "28.6041 28.6041 27.4512 27.4512)(z 0.292439)(focus "
        "-1 -1 -1 -1 -1)"
    )

    cleaned = clean_my_data(raw_udp)

    assert cleaned["lap_time"] == 11.49
    assert cleaned["lap_distance"] == 16.0696
    assert cleaned["gear"] == 1
    assert cleaned["race_pos"] == 1
    assert cleaned["fuel"] == 99.9914
    assert cleaned["track_pos"] == 0.315795
    assert cleaned["angle"] == 0.0025599
    assert cleaned["rpm"] == 5235.99

    # speed_kmh = speedX (31.1387) * 3.6 = 112.09932
    assert cleaned["speed_kmh"] == pytest.approx(112.09932, abs=1e-4)

    # wheel_spin = (28.6041 + 28.6041 + 27.4512 + 27.4512) / 4 = 28.02765
    assert cleaned["wheel_spin"] == pytest.approx(28.02765, abs=1e-4)


def test_clean_my_data_empty_or_malformed():
    """Test that empty or invalid data returns an empty dictionary to prevent crashes."""
    assert clean_my_data("") == {}
    assert clean_my_data("invalid_udp_string") == {}