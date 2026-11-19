"""Phase 7のオンライン更新・合法棋譜・確定局境界再開の独立検証。"""

from itertools import islice
from math import tanh
import json
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest
from test_phase6_ntuple import _oracle_features, _reachable

from harness import referee
from reversi_player import ntuple, ntuple_io, phase6_player, reversi
from tools import phase7_td as td, phase7_train as train
from tools.phase65_data import parse_game, replay

ROOT = Path(__file__).resolve().parents[1]


def _oracle(weights, prior, following, result, rate):
    """独立の特徴と通常加算で、更新前の半勾配を全重みについて計算する。"""
    before = dict(_oracle_features(*prior))
    old_value = tanh(sum(weights[i] * count for i, count in before.items()))
    if result is None:
        goal = tanh(sum(weights[i] * n for i, n in _oracle_features(*following)))
    else:
        goal = result
    revised = [
        w + rate * before.get(i, 0) * (1 - old_value**2) * (goal - old_value)
        for i, w in enumerate(weights)
    ]
    return revised, old_value, goal


@pytest.mark.parametrize('turn', ['B', 'W'])
@pytest.mark.parametrize('reward', [None, -1, 0, 1])
@pytest.mark.parametrize('rate', [0.0, 2.0**-30, 0.00025])
def test_update_snapshot_current_count_and_black_view(turn, reward, rate):
    initial = referee.INITIAL_ROWS
    move = referee.legal_moves(initial, turn)[0]
    current = (initial, turn)
    following = (referee.apply_move(initial, turn, move), 'W' if turn == 'B' else 'B')
    old = [(i % 37 - 18) / 2048 for i in range(1872)]
    expected, prediction, target = _oracle(old, current, following, reward, rate)
    weights = old.copy()
    report = td.update(
        weights, td.State(*current), td.State(*following), reward, td.Config(rate, 0.0, 1)
    )
    assert report.prediction == pytest.approx(prediction, abs=2e-15)
    assert report.target == pytest.approx(target, abs=2e-15)
    assert report.delta == pytest.approx(target - prediction, abs=2e-15)
    assert weights == pytest.approx(expected, abs=2e-15)
    used = dict(_oracle_features(*current))
    assert any(n > 1 for n in used.values())
    assert all(weights[i] == old[i] for i in range(1872) if i not in used)


@pytest.mark.parametrize('turn', ['B', 'W'])
def test_zero_weight_single_step_and_finite_difference(turn):
    initial = (referee.INITIAL_ROWS, turn)
    used = dict(_oracle_features(*initial))
    index, count = max(used.items(), key=lambda item: item[1])
    h = 2.0**-20
    # f has one nonzero entry, so the signed symmetric difference is closed form.
    derivative = (tanh(h * count) - tanh(-h * count)) / (2 * h)
    assert derivative == pytest.approx(count, rel=3e-10)
    weights = [0.0] * 1872
    item = td.update(weights, td.State(*initial), td.State(*initial), 1, td.Config(h, 0.0, 0))
    assert item.prediction == 0 and item.delta == 1
    assert weights[index] == h * count
    assert weights[index] / h == pytest.approx(derivative, rel=3e-10)
    unchanged = weights.copy()
    td.update(weights, td.State(*initial), td.State(*initial), None, td.Config(h, 0, 0))
    assert weights == unchanged
    saturated = [ntuple.MAX_WEIGHT] * 1872
    item = td.update(saturated, td.State(*initial), td.State(*initial), -1, td.Config(0.5, 0.0, 0))
    assert (item.prediction, item.target, item.delta, item.max_change) == (1, -1, -2, 0)


def _tail_fixture():
    """独立乱数とrefereeで、終局直前の勝敗3種と強制パスを探す。"""
    fixtures = {}
    for seed in range(300):
        rng = random.Random(seed)
        rows, turn = referee.INITIAL_ROWS, 'B'
        while referee.result(rows) == 'NG':
            legal = referee.legal_moves(rows, turn)
            other = 'W' if turn == 'B' else 'B'
            if legal:
                move = rng.choice(legal)
                following = referee.apply_move(rows, turn, move)
                final = referee.result(following)
                if final in ('B', 'W', 'D') and len(legal) == 1:
                    fixtures.setdefault(final, (rows, turn))
                if (
                    final in ('B', 'W', 'D')
                    and any('E' in row for row in following)
                    and len(legal) == 1
                ):
                    fixtures.setdefault('nonfull', (rows, turn))
                rows = following
            elif sum(row.count('E') for row in rows) <= 4:
                fixtures.setdefault('pass-' + turn, (rows, turn))
            if len(fixtures) == 6:
                return fixtures
            turn = other
    raise AssertionError('fixtures not reached')


@pytest.fixture(scope='module')
def tails():
    """短い完全対局を共有する。"""
    return _tail_fixture()


@pytest.mark.parametrize('kind', ['B', 'W', 'D', 'nonfull', 'pass-B', 'pass-W'])
def test_complete_short_games_against_independent_update_sequence(tails, kind):
    rows, turn = tails[kind]
    original = [(i % 17 - 8) * 2.0**-13 for i in range(1872)]
    weights = original.copy()
    diagnostics = td.Diagnostics()
    rng = random.Random(33)
    before_rng = rng.getstate()
    outcome = td.play(
        weights, td.Config(0.00025, 0.0, 33), rng, diagnostics, initial=td.State(rows, turn)
    )
    expected = original
    pass_count = 0
    for i, move in enumerate(outcome.moves, 1):
        moves = referee.legal_moves(rows, turn)
        next_turn = 'W' if turn == 'B' else 'B'
        if move == (-1, -1):
            assert not moves and referee.legal_moves(rows, next_turn)
            after = rows
            pass_count += 1
        else:
            assert move in moves
            after = referee.apply_move(rows, turn, move)
        result = referee.result(after)
        assert (result != 'NG') == (i == len(outcome.moves))
        reward = {'B': 1, 'D': 0, 'W': -1, 'NG': None}[result]
        expected, _, _ = _oracle(expected, (rows, turn), (after, next_turn), reward, 0.00025)
        rows, turn = after, next_turn
    assert outcome.margin == sum(r.count('B') - r.count('W') for r in rows)
    assert pass_count == outcome.passes
    if kind.startswith('pass'):
        assert outcome.moves[0] == (-1, -1)
    if kind in ('B', 'W', 'D'):
        assert referee.result(rows) == kind
    assert diagnostics.observations == len(outcome.moves)
    assert rng.getstate() == before_rng
    assert weights == pytest.approx(expected, abs=2e-15)
    with pytest.raises(ValueError, match='td_terminal_start'):
        td.play(
            weights, td.Config(0.00025, 0.0, 33), rng, diagnostics, initial=td.State(rows, turn)
        )


@pytest.mark.parametrize('seed', [12, 51])
def test_greedy_protocol_equivalence_and_explicit_rng(seed):
    rng = random.Random(seed)
    weight_model = ntuple.NtupleModel(tuple(rng.uniform(-0.05, 0.05) for _ in range(1872)))
    choose = phase6_player.make_selector(weight_model)
    for rows, turn, _ in islice(_reachable(), 0, 65, 7):
        moves = reversi.legal_moves_for(rows, turn)
        if not moves:
            continue
        state = td.State(rows, turn)
        expected, actual = random.Random(seed), random.Random(seed)
        weights = list(weight_model.weights)
        greedy = td.choose(weights, state, moves, td.Config(0, 0, seed), actual)
        assert greedy[0] == choose(rows, turn, moves)
        assert greedy[1].rows == referee.apply_move(rows, turn, greedy[0])
        assert greedy[2] == (referee.result(greedy[1].rows) != 'NG')
        assert td.value(weights, td.features(state)) == weight_model.value(rows, turn)
        assert actual.getstate() == expected.getstate()
        expected.random()
        assert td.choose(weights, state, moves, td.Config(0, 1, seed), actual)[0] == (
            expected.choice(moves)
        )
        assert actual.getstate() == expected.getstate()
        assert weights == list(weight_model.weights)


@pytest.mark.parametrize(
    'rate,epsilon,seed',
    [(float('nan'), 0, 2), (0, float('inf'), 0), (0, -0.01, 0), (1.01, 0, 0), (0, 0, True)],
)
def test_invalid_settings(rate, epsilon, seed):
    with pytest.raises(ValueError, match='invalid_td_'):
        td.Config(rate, epsilon, seed)


def _child(initial, output, until, resume=None):
    """実プロセスを別cwdで起動し、再開情報のシリアライズを試す。"""
    args = [
        sys.executable,
        '-B',
        '-m',
        'tools.phase7_train',
        str(initial),
        str(output),
        '--until',
        str(until),
        '--seed',
        '701',
        '--rate',
        '0.00025',
        '--epsilon',
        '0.23',
    ]
    if resume is not None:
        args += ['--resume', str(resume)]
    return subprocess.run(
        args,
        env={**os.environ, 'PYTHONPATH': str(ROOT)},
        cwd=output.parent,
        capture_output=True,
        check=True,
        timeout=45,
    )


def test_bit_exact_fresh_process_resume_and_evidence(tmp_path):
    initial = tmp_path / 'initial.json'
    ntuple_io.save_model(initial, ntuple.NtupleModel.zeros())
    same = initial.read_bytes()
    continuous, first, resumed = (tmp_path / name for name in ('continuous', 'first', 'resumed'))
    for subdir, count, previous in [(continuous, 4, None), (first, 2, None), (resumed, 4, first)]:
        result = _child(initial, subdir, count, previous)
        assert not result.stderr
    assert (continuous / 'model.json').read_bytes() == (resumed / 'model.json').read_bytes()
    full_games = (continuous / 'games.jsonl').read_bytes()
    assert (
        full_games == (first / 'games.jsonl').read_bytes() + (resumed / 'games.jsonl').read_bytes()
    )
    a, b = [json.loads((p / 'state.json').read_bytes())['payload'] for p in (continuous, resumed)]
    for key in ('counts', 'rng', 'model'):
        assert a[key] == b[key]
    assert b['previous']['games'] == 2
    updates = 0
    for line in full_games.splitlines():
        game = json.loads(line)
        moves, margin = parse_game([game['moves'], game['margin']])
        assert replay(moves, margin)[0] == margin
        updates += len(moves)
    assert updates == a['counts']['updates']
    assert initial.read_bytes() == same

    config = td.Config(0.00025, 0.23, 701)
    with pytest.raises(FileNotFoundError):
        train.run(initial, tmp_path / 'missing', config, 5, resume=tmp_path / 'invalid')
    with pytest.raises(FileExistsError):
        train.run(initial, first, config, 1)
    with pytest.raises(ValueError, match='incompatible_td_checkpoint'):
        train.run(initial, tmp_path / 'incompatible', td.Config(0.001, 0.23, 701), 5, resume=first)
    (first / 'games.jsonl').write_bytes(b'\n')
    with pytest.raises(ValueError, match='checkpoint_games_mismatch'):
        train.run(initial, tmp_path / 'corrupted', config, 5, resume=first)
    state_path = resumed / 'state.json'
    state_path.write_bytes(state_path.read_bytes()[:-30])
    with pytest.raises(json.JSONDecodeError):
        train.run(initial, tmp_path / 'partial', config, 5, resume=resumed)
