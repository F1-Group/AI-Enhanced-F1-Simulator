from data_pipeline.cache import DataCache, GameStatus

def test_cache_telemetry_copy():
    """Test that update_telemetry and get_telemetry return independent copies."""
    cache = DataCache()
    sample_data = {"speed_kmh": 100.0, "gear": 2}

    cache.update_telemetry(sample_data)
    retrieved = cache.get_telemetry()

    # Modifying the returned dict should not affect internal cache
    retrieved["speed_kmh"] = 999.0
    assert cache.get_telemetry()["speed_kmh"] == 100.0


def test_cache_telemetry_none():
    """Test that passing None to update_telemetry returns None in get_telemetry."""
    cache = DataCache()
    cache.update_telemetry(None)
    assert cache.get_telemetry() is None


def test_cache_status_setting():
    """Test setting and getting GameStatus."""
    cache = DataCache()
    cache.set_status(GameStatus.RACING)
    assert cache.get_status() == GameStatus.RACING