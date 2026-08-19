from app.geo import haversine_m, within_geofence


def test_zero_distance():
    assert haversine_m(6.6745, -1.5716, 6.6745, -1.5716) == 0.0


def test_known_short_distance():
    # ~111 m per 0.001 deg latitude near the equator.
    d = haversine_m(6.6745, -1.5716, 6.6755, -1.5716)
    assert 100 < d < 125


def test_within_and_outside_geofence():
    inside, d_in = within_geofence(6.6745, -1.5716, 6.67452, -1.57162, 70.0)
    assert inside and d_in <= 70

    outside, d_out = within_geofence(6.6745, -1.5716, 6.6759, -1.5716, 70.0)
    assert not outside and d_out > 70
