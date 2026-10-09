import copy
import json
import time
from pathlib import Path

import pytest

from harness.evaluate import compare_reports, evaluate
from harness.protocol import HarnessError, Player

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'reversi_player' / 'reversi.py'
FAULT = ROOT / 'tests' / 'fixtures' / 'fault_player.py'


@pytest.fixture(scope='module')
def smoke_reports():
    return [evaluate(SCRIPT, SCRIPT, games=2, seed=31) for _ in range(2)]


@pytest.mark.smoke
def test_paired_complete_games_are_reproducible_and_serializable(smoke_reports):
    before, after = smoke_reports
    assert before['summary']['completed'] == 2
    assert before['summary']['failed'] == 0
    assert before['summary']['A']['black_games'] == before['summary']['A']['white_games'] == 1
    assert before['summary']['A']['wins'] == before['summary']['B']['losses']
    assert before['summary']['A']['point_rate'] + before['summary']['B']['point_rate'] == 1
    for previous, current in zip(before['games'], after['games']):
        assert previous['result'] == current['result']
        assert [move['position'] for move in previous['moves']] == [move['position'] for move in current['moves']]
        assert previous['final_board'] == current['final_board']
        for player in previous['players'].values():
            assert player['exit_code'] == 0
            for command in player['commands']:
                if not command['startup_probe']:
                    assert command['seconds'] <= (10 if command['command'].startswith('genmove ') else 1)
    assert json.loads(json.dumps(before))['summary'] == before['summary']


def test_comparison_rejects_changed_conditions_or_opponent(smoke_reports):
    before, after = smoke_reports
    assert compare_reports(before, after)['comparable']
    changed = copy.deepcopy(after)
    changed['conditions']['seed'] += 1
    changed['targets']['B']['sha256'] = 'different opponent'
    comparison = compare_reports(before, changed)
    assert not comparison['comparable']
    assert comparison['condition_mismatches'] == ['conditions', 'opponent.sha256']


@pytest.mark.parametrize('games', [0, 1, 3, -2])
def test_evaluation_requires_balanced_game_count(games):
    with pytest.raises(ValueError, match='even'):
        evaluate(SCRIPT, SCRIPT, games=games)


@pytest.mark.parametrize('mode', ['timeout', 'abnormal_exit', 'protocol_error', 'illegal_move', 'board_mismatch'])
def test_evaluation_records_faults_and_continues(monkeypatch, mode):
    monkeypatch.setenv('REVERSI_HARNESS_TEST_FAULT', mode)
    report = evaluate(FAULT, SCRIPT, games=2, seed=31, genmove_timeout=0.1)
    assert report['summary']['failed'] == 2
    assert report['summary']['completed'] == 0
    assert report['games'][0]['error']['kind'] == mode
    assert report['games'][0]['error']['player'] == 'A'
    assert report['summary']['A']['win_rate'] is None
    for game in report['games']:
        assert all(player['exit_code'] is not None for player in game['players'].values())


@pytest.mark.parametrize('mode,operation,kind', [
    ('timeout', 'genmove B', 'timeout'),
    ('abnormal_exit', 'genmove B', 'abnormal_exit'),
    ('protocol_error', 'genmove B', 'protocol_error'),
    ('extra_stdout', 'quit', 'protocol_error'),
    ('quit_timeout', 'quit', 'timeout'),
])
def test_bad_player_is_terminated(monkeypatch, mode, operation, kind):
    monkeypatch.setenv('REVERSI_HARNESS_TEST_FAULT', mode)
    player = Player(FAULT, command_timeout=0.1, genmove_timeout=0.1)
    try:
        with pytest.raises(HarnessError) as failure:
            if operation == 'quit':
                player.finish()
            else:
                player.command(operation)
        assert failure.value.kind == kind
        assert player.process.poll() is not None
        assert all(not reader.is_alive() for reader in player._readers)
    finally:
        player.abort()


def test_startup_has_its_own_timeout(monkeypatch):
    monkeypatch.setenv('REVERSI_HARNESS_TEST_FAULT', 'startup_timeout')
    with pytest.raises(HarnessError) as failure:
        Player(FAULT, startup_timeout=0.1)
    assert failure.value.kind == 'timeout'
    assert failure.value.command == 'name'
    assert failure.value.diagnostics['exit_code'] is not None


def test_failed_games_are_not_used_to_claim_performance_improvement(smoke_reports):
    before, after = smoke_reports
    changed = copy.deepcopy(after)
    changed['summary']['failed'] = 1
    assert compare_reports(before, changed)['condition_mismatches'] == ['incomplete_evaluation']


def test_genmove_uses_its_longer_deadline(monkeypatch):
    monkeypatch.setenv('REVERSI_HARNESS_TEST_FAULT', 'slow_genmove')
    with Player(FAULT, command_timeout=0.1, genmove_timeout=0.5) as player:
        assert player.command('genmove B') == ['2 3']
        assert player.commands[-1]['seconds'] > player.command_timeout
        assert player.commands[-1]['seconds'] < player.genmove_timeout


def test_total_response_time_is_checked_after_validation(monkeypatch):
    player = Player(SCRIPT, command_timeout=0.05)
    validate = player._validate

    def slow_validation(command, lines):
        validate(command, lines)
        time.sleep(0.08)

    monkeypatch.setattr(player, '_validate', slow_validation)
    try:
        with pytest.raises(HarnessError) as failure:
            player.command('result')
        assert failure.value.kind == 'timeout'
        assert player.process.poll() is not None
    finally:
        player.abort()
