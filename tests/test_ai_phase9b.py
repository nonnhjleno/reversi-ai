"""深度拡張の範囲、独立全幅探索との一致、途中停止を確認する。"""

import json
from pathlib import Path
import random

import pytest

from harness import protocol, referee
from reversi_player import ntuple, reversi, timed_search
from tests.test_ai_phase3 import _oracle as fraction_oracle
from tests.test_ai_phase4 import CASES, _context
from tests.test_ai_phase8 import _oracle


def _legal(rows, color):
    """独立refereeの着手を時刻探索の不変tupleへ変換する。"""
    return tuple(referee.legal_moves(rows, color))


def _near_end():
    """合法履歴から空き6の局面を固定し、独立全幅探索の費用を抑える。"""
    rng = random.Random(9125)
    rows, turn = referee.INITIAL_ROWS, 'B'
    while sum(row.count('E') for row in rows) > 6:
        moves = _legal(rows, turn)
        other = 'W' if turn == 'B' else 'B'
        assert moves or _legal(rows, other)
        if moves:
            rows = referee.apply_move(rows, turn, rng.choice(moves))
        turn = other
    return rows


def _search(rows, turn, depth, **options):
    """独立ルールで反復深化を起動する。"""
    return timed_search.search_iterative(
        rows,
        turn,
        _legal(rows, turn),
        _legal,
        options.pop('apply', referee.apply_move),
        command_received_ns=0,
        policy=timed_search.ResponsePolicy(max_depth=depth),
        clock=options.pop('clock', lambda: 0),
        **options,
    )


@pytest.mark.parametrize('cap', [4, 5, 6])
@pytest.mark.parametrize('turn', ['B', 'W'])
@pytest.mark.parametrize('label', ['near_end', 'pass', 'terminal_with_empty', 'wipe'])
def test_deep_values_against_independent_full_width(label, turn, cap):
    """手作業評価と黒視点n-tupleを独立の全幅値に照合する。"""
    if label == 'near_end':
        rows = _near_end()
    elif label == 'wipe':
        rows = ('BWEEEEEE',) + ('EEEEEEEE',) * 7
    else:
        rows = tuple(CASES[label]['rows'])
    model = ntuple.NtupleModel(tuple((i % 7 - 3) / 2000 for i in range(ntuple.WEIGHT_COUNT)))
    fraction, best, _ = fraction_oracle(rows, turn, cap)
    value, move = _oracle(rows, turn, cap, model.value)
    for evaluator, expected in (
        (None, (best[0] if best else None, float(fraction))),
        (model.value, (move, value if turn == 'B' else -value)),
    ):
        measurements = []
        answer = _search(rows, turn, cap, leaf_evaluator=evaluator, iterations=measurements)
        if not _legal(rows, turn):
            assert answer.move is answer.value is None
            assert answer.completed_depth == answer.max_depth_started == 0 and not measurements
            continue
        assert (answer.move, answer.value) == expected
        assert answer.completed_depth == answer.max_depth_started == cap
        assert [step.depth for step in measurements] == list(range(1, cap + 1))
        assert all(step.completed and step.elapsed_ns == 0 for step in measurements)
        assert sum(step.nodes for step in measurements) == answer.nodes
        assert sum(step.leaves for step in measurements) == answer.leaves
        assert (measurements[-1].move, measurements[-1].value) == expected


@pytest.mark.parametrize('depth', [5, 6])
@pytest.mark.parametrize('refuse_start', [False, True])
def test_interruption_never_uses_partial_value(depth, refuse_start):
    """深い反復で同じ時計の開始期限・停止期限と最後の確定結果を照合する。"""
    rows, turn = _near_end(), 'B'
    if not _legal(rows, turn):
        turn = 'W'

    finished = _search(rows, turn, depth - 1)
    seen = 0

    def moves_counted(snapshot, color, move):
        """ノードを一段進めるごとに実着手callbackの呼び出し数を記録する。"""
        nonlocal seen
        seen += 1
        return referee.apply_move(snapshot, color, move)

    # この終盤ではパスもあるため、ノード数と仮着手数を同一視しない。
    _search(rows, turn, depth - 1, apply=moves_counted)
    cutoff = seen + (0 if refuse_start else 1)
    seen = 0

    def clock():
        """開始拒否のテストでは直前反復の終端checkだけを期限前に通す。"""
        if seen < cutoff:
            return 0
        if not refuse_start:
            return 6_500_000_000
        return 6_000_000_000

    records = []
    actual = _search(rows, turn, depth, apply=moves_counted, clock=clock, iterations=records)
    assert (actual.move, actual.value, actual.completed_depth) == (
        finished.move,
        finished.value,
        depth - 1,
    )
    assert actual.move in _legal(rows, turn)
    assert actual.max_depth_started == (depth - 1 if refuse_start else depth)
    if not refuse_start:
        assert actual.stop_reason == 'time_limit'
        assert records[-1].depth == depth and not records[-1].completed
        assert records[-1].value is records[-1].move is None
    else:
        assert actual.stop_reason == 'new_depth_deadline'
        assert records[-1].depth == depth - 1 and records[-1].completed


@pytest.mark.parametrize(
    'kind,cap,accepted',
    [
        ('time', 4, True),
        ('time', 5, True),
        ('time', 6, True),
        ('time', 7, False),
        ('depth', 5, False),
        ('depth', 6, False),
    ],
)
def test_real_process_depth_configuration(kind, cap, accepted, tmp_path):
    """時間探索の拡張と固定深度の従来範囲を実プロセスで切り分ける。"""
    if kind == 'time':
        budget, search = {'kind': 'time', 'limit_ns': 6_500_000_000}, {'max_depth': cap}
    else:
        budget, search = {'kind': 'depth', 'limit_depth': cap}, {}
    config = {'format_version': 2, 'budget': budget, 'search': search}
    cfg, trace = tmp_path / 'config.json', tmp_path / 'trace.jsonl'
    cfg.write_text(json.dumps(config), encoding='utf-8')
    script = Path(reversi.__file__).resolve()

    def player():
        """同じ実設定とtrace先を指定して子を起動する。"""
        return protocol.Player(
            script,
            evaluation_config=cfg,
            evaluation_trace=trace,
            evaluation_context=_context(config),
        )

    if not accepted:
        with pytest.raises(protocol.HarnessError):
            player()
        assert not trace.exists()
        return
    # 終盤を再生し、実コマンドの拡張深度と各反復を短時間で測る。
    case = CASES['pass']
    turn = 'W' if case['color'] == 'B' else 'B'
    with player() as process:
        for step in case['trace']:
            if step['move'] is not None:
                assert process.command('play {} {} {}'.format(step['color'], *step['move'])) == [
                    'OK'
                ]
        move = tuple(map(int, process.command('genmove ' + turn)[0].split()))
        assert move in _legal(tuple(case['rows']), turn)
    events = [json.loads(line) for line in trace.read_bytes().splitlines()]
    (event,) = [line for line in events if line['type'] == 'genmove']
    assert events[0]['effective_config'] == config
    assert (event['completed_depth'], event['max_depth_started']) == (cap, cap)
    assert len(event['iterations']) == cap and all(row['completed'] for row in event['iterations'])
