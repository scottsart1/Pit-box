"""The isolated replay harness must fail when race completion was not observed."""

from copy import deepcopy

import pytest

from tools.strategy_app_smoke import settle_replay


RESULT = {
    'session_uid': 42, 'packet_format': 2026, 'player_car_index': 0,
    'final_classification': {'position': 7, 'laps': 25, 'pit_stops': 1},
}


class ReplayObservation:
    def __init__(self, states, archives=None):
        self.states = states
        self.archives = archives or [{}]
        self.now = 0.0
        self.poll = -1

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def get(self, path):
        if path == '/api/state':
            self.poll += 1
            return {
                **deepcopy(RESULT), 'current_lap': 26, 'packets_received': 100,
                'packet_queue_depth': 0,
                **deepcopy(self.states[min(self.poll, len(self.states) - 1)]),
            }
        if path == '/api/health':
            return {'full_field_archive': {
                'queue_depth': 0, 'invalidation_queue_depth': 0,
                'submitted': 10, 'persisted_laps': 10, 'invalidations': 0,
                **self.archives[min(self.poll, len(self.archives) - 1)],
            }}
        assert path == '/api/v1/network/status'
        return {'diagnostics': 'retained even when completion fails'}

    def settle(self, summary, expected=RESULT, timeout=8):
        return settle_replay(self.get, summary, expected, timeout_s=timeout,
                             clock=self.clock, sleep=self.sleep)


@pytest.mark.parametrize('observed', [
    {'current_lap': 18, 'final_classification': {}},
    {'session_uid': 99},
    {'final_classification': {'position': 3, 'laps': 25, 'pit_stops': 1}},
])
def test_partial_or_wrong_race_cannot_pass_even_with_empty_queues(observed):
    replay = ReplayObservation([observed])
    summary = {}
    with pytest.raises(RuntimeError, match='Replay did not settle'):
        replay.settle(summary, timeout=3)
    assert replay.now == 3
    assert summary['race_completion_verified'] is False
    assert summary['state_final']['final_classification'] == observed.get('final_classification', RESULT['final_classification'])
    assert summary['network_after']['diagnostics']


def test_delayed_final_result_and_archive_in_flight_must_finish_before_shutdown():
    replay = ReplayObservation(
        [{'final_classification': {}}, {}, {'packet_queue_depth': 1}, {}],
        [{}, {'queue_depth': 0, 'persisted_laps': 9}, {}, {}],
    )
    summary = {}
    state = replay.settle(summary)
    assert replay.now >= 3.5
    assert state['final_classification'] == RESULT['final_classification']
    assert summary['race_completion_verified'] is True
    assert summary['validation_scope'] == 'complete_demo_race'
    assert not summary['settlement_pending']


def test_in_flight_archive_batch_cannot_pass_when_queue_depth_is_zero():
    replay = ReplayObservation([{}], [{'persisted_laps': 9}])
    with pytest.raises(RuntimeError, match='archive has queued or in-flight work'):
        replay.settle({}, timeout=3)


def test_in_flight_history_update_cannot_pass_using_number_of_reconciled_laps():
    replay = ReplayObservation([{}], [{'submitted': 12, 'history_updates_processed': 1,
                                      'history_laps_reconciled': 31}])
    with pytest.raises(RuntimeError, match='archive has queued or in-flight work'):
        replay.settle({}, timeout=3)


def test_processed_noop_and_discarded_old_epoch_history_complete_work_accounting():
    replay = ReplayObservation([{}], [{'submitted': 12, 'history_updates_processed': 1,
                                      'history_updates_discarded': 1, 'history_laps_reconciled': 0}])
    summary = {}
    replay.settle(summary)
    assert summary['race_completion_verified'] is True


@pytest.mark.parametrize('failure', ['queue_drops', 'write_errors', 'invalidation_queue_drops'])
def test_lost_archive_work_fails_instead_of_being_reported_as_settled(failure):
    replay = ReplayObservation([{}], [{failure: 1}])
    with pytest.raises(RuntimeError, match='Archive lost or failed work'):
        replay.settle({})
    assert replay.now == 0


def test_capture_without_expected_result_only_claims_observed_ingestion_settled():
    replay = ReplayObservation([{'current_lap': 8, 'final_classification': {}}])
    summary = {}
    replay.settle(summary, expected=None)
    assert summary['race_completion_verified'] is False
    assert summary['validation_scope'] == 'capture_observation_only'
    assert replay.now >= 2


def test_newly_arriving_packets_reset_the_required_quiet_period():
    replay = ReplayObservation([{}, {}, {}, {'packets_received': 101}])
    summary = {}
    replay.settle(summary)
    assert replay.now >= 4
