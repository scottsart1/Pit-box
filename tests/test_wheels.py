from pitwall.udp import normalize_wheels


def test_raw_wheel_order_is_normalized_to_fl_fr_rl_rr():
    assert normalize_wheels([10, 20, 30, 40]) == [30, 40, 10, 20]
