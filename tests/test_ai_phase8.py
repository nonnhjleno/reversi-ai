"""Phase 8の葉評価を独立refereeと全幅探索へ照合する。"""

from dataclasses import asdict
import json
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

from baselines.search_v1 import ai_engine as frozen_engine
from baselines.search_v1 import timed_search as frozen_time
from harness import referee, search_budget
from reversi_player import ai_engine, ntuple, ntuple_io, phase6_player, reversi, search_trace
from reversi_player import timed_search, web
from test_ai_phase4 import _context
from tools.phase8_compare import _positions


def _legal(rows, turn):
    """独立refereeの盤面規則を利用する。"""
    return tuple(referee.legal_moves(rows, turn))


def _oracle(rows, turn, depth, evaluate):
    """枝刈りなしの黒視点minimax。根の色への変換は呼出側で行う。"""
    other = 'BW'[turn == 'B']
    result = referee.result(rows)
    if result != 'NG':
        stones = ''.join(rows)
        diff = stones.count('B') - stones.count('W')
        return {'B': 1000, 'W': -1000, 'D': 0}[result] + diff, None
    if not depth:
        return evaluate(rows, turn), None
    moves = referee.legal_moves(rows, turn)
    if not moves:
        return _oracle(rows, other, depth, evaluate)[0], None
    values = [
        (_oracle(referee.apply_move(rows, turn, move), other, depth - 1, evaluate)[0], move)
        for move in moves
    ]
    return (max if turn == 'B' else min)(values, key=lambda entry: entry[0])


@pytest.fixture(scope='module')
def model():
    """正負が混在する固定fixture。"""
    rng = random.Random(804)
    return ntuple.NtupleModel(tuple(rng.uniform(-0.03, 0.03) for _ in range(ntuple.WEIGHT_COUNT)))


@pytest.mark.parametrize('stage', ['opening', 'middle', 'late', 'pass'])
@pytest.mark.parametrize('depth', [0, 1, 2, 3])
@pytest.mark.parametrize('turn', ['B', 'W'])
def test_search_matches_black_view_full_width(model, stage, depth, turn):
    rows, _ = _positions()[stage]
    value, move = _oracle(rows, turn, depth, model.value)
    expected = value if turn == 'B' else -value
    args = (rows, turn, depth, _legal, referee.apply_move)
    for ordered in (True, False):
        result = ai_engine.search_alpha_beta(*args, ordered=ordered, leaf_evaluator=model.value)
        minimax = ai_engine.search_minimax(*args, leaf_evaluator=model.value)
        assert (result.move, result.value) == (minimax.move, minimax.value) == (move, expected)
        assert result.nodes <= minimax.nodes
        if stage == 'middle' and depth == 3:
            assert result.cutoffs > 0 and result.nodes < minimax.nodes
    if depth == 1 and _legal(rows, turn):
        assert move == phase6_player.make_selector(model)(rows, turn, _legal(rows, turn))


@pytest.mark.parametrize('stage', ['initial', 'opening', 'middle', 'late', 'pass'])
@pytest.mark.parametrize('turn', ['B', 'W'])
def test_manual_search_matches_frozen_phase5(stage, turn):
    rows, _ = _positions()[stage]

    def clock():
        return 0

    for ordered in (True, False):
        args = (rows, turn, _legal(rows, turn), _legal, referee.apply_move)
        fixed = (rows, turn, 2, _legal, referee.apply_move)
        params = dict(command_received_ns=0, clock=clock, ordered=ordered)
        assert asdict(ai_engine.search_alpha_beta(*fixed, ordered=ordered)) == asdict(
            frozen_engine.search_alpha_beta(*fixed, ordered=ordered)
        )
        assert asdict(
            timed_search.search_iterative(
                *args, **params, policy=timed_search.ResponsePolicy(max_depth=2)
            )
        ) == asdict(
            frozen_time.search_iterative(
                *args, **params, policy=frozen_time.ResponsePolicy(max_depth=2)
            )
        )


@pytest.mark.parametrize(
    ('rows', 'black_value'),
    [
        (('BBBBBBBB',) * 8, 1064.0),
        (('EEEEEEEE',) * 7 + ('EEEEEEEW',), -1001.0),
        (('BBBBBBBB',) * 4 + ('WWWWWWWW',) * 4, 0.0),
    ],
)
def test_terminal_score_precedes_model(rows, black_value):
    def forbidden(_rows, _turn):
        raise AssertionError('terminal model called')

    for turn, expected in [('B', black_value), ('W', -black_value)]:
        result = ai_engine.search_alpha_beta(
            rows, turn, 0, _legal, referee.apply_move, leaf_evaluator=forbidden
        )
        assert result.value == expected and result.move is None


@pytest.mark.parametrize('score', [float('nan'), float('inf'), float('-inf'), 1.01, -1.01])
def test_invalid_leaf_scores_fail(score):
    with pytest.raises(ValueError, match='invalid_leaf_value'):
        ai_engine.search_alpha_beta(
            referee.INITIAL_ROWS,
            'B',
            1,
            _legal,
            referee.apply_move,
            leaf_evaluator=lambda _rows, _turn: score,
        )


def test_timed_model_keeps_completed_depth_and_legal_timeout_fallback(model):
    rows, turn = _positions()['middle']
    moves = _legal(rows, turn)
    clock_time = 0
    depth_one = ai_engine.search_alpha_beta(
        rows, turn, 1, _legal, referee.apply_move, leaf_evaluator=model.value
    )
    count = 0

    def slow_leaf(snapshot, color):
        nonlocal count, clock_time
        count += 1
        if count > len(moves):
            clock_time = 6_500_000_000
        return model.value(snapshot, color)

    args = (rows, turn, moves, _legal, referee.apply_move)
    result = timed_search.search_iterative(
        *args,
        command_received_ns=0,
        policy=timed_search.ResponsePolicy(),
        clock=lambda: clock_time,
        leaf_evaluator=slow_leaf,
    )
    assert result.completed_depth == 1 and result.max_depth_started == 2
    assert (result.move, result.value) == (depth_one.move, depth_one.value)
    assert result.stop_reason == 'time_limit'
    expired = timed_search.search_iterative(
        *args,
        command_received_ns=0,
        policy=timed_search.ResponsePolicy(),
        clock=lambda: 7_000_000_000,
        leaf_evaluator=slow_leaf,
    )
    assert expired.completed_depth == 0 and expired.value is None
    assert expired.move == moves[0]


@pytest.fixture
def artifact(model, tmp_path):
    """日本語・空白を含む経路の凍結モデルを用意する。"""
    path = tmp_path / 'Phase 8 モデル.json'
    ntuple_io.save_model(path, model)
    return path, ntuple_io.load_model(path)


def _child(tmp_path, commands, env):
    """通常の入口を異なるcwdから独立して動かす。"""
    return subprocess.run(
        [sys.executable, '-B', str(Path(reversi.__file__).resolve())],
        input=commands,
        encoding='utf-8',
        capture_output=True,
        cwd=tmp_path,
        env=env,
        timeout=10,
    )


def test_explicit_model_keeps_protocol_and_gui(artifact, tmp_path, monkeypatch):
    path, _ = artifact
    monkeypatch.setenv(search_trace.MODEL_ENV, str(path))
    child = _child(
        tmp_path,
        'name\nask B 3 2\nplay B 3 2\ngenmove W\nshowboard\nresult\nquit\n',
        os.environ.copy(),
    )
    assert child.returncode == 0 and not child.stderr
    lines = child.stdout.splitlines()
    rows = referee.apply_move(referee.INITIAL_ROWS, 'B', (3, 2))
    move = tuple(map(int, lines[3].split()))
    assert lines[:3] == ['ranger', 'OK', 'OK']
    assert tuple(lines[4:12]) == referee.apply_move(rows, 'W', move)
    assert lines[12] == 'NG'
    with web.Process() as gui:
        assert gui.command('name') == 'ranger'
        answer = tuple(map(int, gui.command('genmove B').split()))
        assert answer in referee.legal_moves(referee.INITIAL_ROWS, 'B')
    assert _child(tmp_path, 'name\n', os.environ.copy()).stdout == 'ranger\n'


@pytest.mark.parametrize('mode', ['missing', 'relative', 'corrupt'])
def test_model_errors_fail_closed(tmp_path, mode):
    path = tmp_path / 'missing.json'
    if mode == 'relative':
        path = Path('relative.json')
    if mode == 'corrupt':
        path.write_text('corrupt', encoding='utf-8')
    child = _child(tmp_path, 'name\n', {**os.environ, search_trace.MODEL_ENV: str(path)})
    assert child.returncode != 0 and child.stderr and not child.stdout


@pytest.mark.parametrize('mode', ['valid', 'wrong_hash', 'wrong_id', 'incomplete'])
def test_trace_binds_requested_model(artifact, tmp_path, mode):
    path, saved = artifact
    config = {
        'format_version': 2,
        'budget': {'kind': 'depth', 'limit_depth': 2},
        'search': {
            'model_id': saved.model_id,
            'model_sha256': saved.file_sha256,
            'model_path': str(path),
        },
    }
    if mode == 'wrong_hash':
        config['search']['model_sha256'] = '0' * 64
    if mode == 'wrong_id':
        config['search']['model_id'] = 'sha256:' + '0' * 64
    if mode == 'incomplete':
        del config['search']['model_id']
    settings, trace = tmp_path / 'config.json', tmp_path / 'trace.jsonl'
    settings.write_text(json.dumps(config), encoding='utf-8')
    env = {
        **os.environ,
        'REVERSI_EVAL_CONFIG': str(settings),
        'REVERSI_EVAL_TRACE': str(trace),
        'REVERSI_EVAL_TRACE_CONTEXT': json.dumps(
            {
                **_context(config),
                'process_session_id': 'phase8-test',
                'process_generation': 0,
                'clock_domain_id': 'local',
                'host_clock': search_trace.clock_descriptor(),
            }
        ),
    }
    child = _child(tmp_path, 'name\ngenmove B\nquit\n', env)
    if mode != 'valid':
        assert child.returncode != 0 and not child.stdout and not trace.exists()
        return
    assert child.returncode == 0 and not child.stderr
    header, _, genmove, *_ = map(json.loads, trace.read_text(encoding='utf-8').splitlines())
    assert header['model_id'] == genmove['model_id'] == saved.model_id
    assert (
        header['model_load_status'] == 'startup' and genmove['model_load_status'] == 'not_required'
    )
    assert (
        header['child_entry_ns']
        <= header['model_load_start_ns']
        <= header['model_load_end_ns']
        <= header['protocol_ready_ns']
    )
    assert genmove['fallback'] is False
    assert search_budget.load_config(settings) == config
