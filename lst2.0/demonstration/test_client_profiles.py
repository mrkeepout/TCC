#!/usr/bin/env python3
# Offline tests for the benign client profiles -- pure, no Docker/network.
# Mirrors the testable-function style of edos_labeling: we drive build_session()
# directly instead of running the HTTP lifecycle.
#
# Run: python3 -m pytest test_client_profiles.py   (or: python3 test_client_profiles.py)

from client_profiles import (ClientConfig, MixedUser, Browsing, Mailing,
                             Printing, ACTIVITY_WEIGHTS, build_profile)


def _activities_over_n(cfg, n):
    """The sequence of activities a MixedUser picks over n sessions."""
    user = MixedUser(cfg)
    picked = []
    for _ in range(n):
        user.build_session()        # sets user._last to the chosen activity
        picked.append(user._last)
    return picked


def test_mixed_emits_all_three_activities():
    cfg = ClientConfig(seed=1337, client_id=1)
    picked = set(_activities_over_n(cfg, 500))
    assert picked == {'browsing', 'mailing', 'printing'}, picked


def test_mixed_is_deterministic_per_seed_and_client():
    a = _activities_over_n(ClientConfig(seed=1337, client_id=1), 200)
    b = _activities_over_n(ClientConfig(seed=1337, client_id=1), 200)
    assert a == b, 'same seed+client_id must reproduce the same sequence'


def test_mixed_differs_across_clients():
    a = _activities_over_n(ClientConfig(seed=1337, client_id=1), 200)
    b = _activities_over_n(ClientConfig(seed=1337, client_id=2), 200)
    assert a != b, 'different client_id must give a different sequence'


def test_mixed_shares_one_rng_so_requests_are_reproducible():
    # Two identical MixedUsers must also produce identical request bursts, not
    # just identical activity labels -- proves the composed activities consume
    # the SAME rng (determinism nota in the plan).
    def burst_shapes(cfg):
        user = MixedUser(cfg)
        out = []
        for _ in range(50):
            reqs = user.build_session()
            out.append([(r.method, r.path, r.body_size) for r in reqs])
        return out
    assert burst_shapes(ClientConfig(seed=7, client_id=3)) == \
           burst_shapes(ClientConfig(seed=7, client_id=3))


def test_single_activity_profiles_still_work():
    cfg = ClientConfig(seed=1, client_id=1)
    for cls in (Browsing, Mailing, Printing):
        reqs = cls(cfg).build_session()
        assert reqs and all(r.method in ('GET', 'POST') for r in reqs)


def test_registry_exposes_mixed():
    assert isinstance(build_profile('mixed', ClientConfig()), MixedUser)


def test_weights_cover_the_three_activities():
    assert set(ACTIVITY_WEIGHTS) == {'browsing', 'mailing', 'printing'}


if __name__ == '__main__':
    import sys
    funcs = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(funcs) - failed}/{len(funcs)} passed")
    sys.exit(1 if failed else 0)
