"""Bitboardを独立referee、既存配列、全幅探索と照合する。"""

import json
from pathlib import Path
import random
import subprocess
import sys

import pytest

from harness import protocol, referee
from reversi_player import bitboard, ntuple_io, reversi, search_trace, timed_search, web
from tests.test_ai_phase3 import _oracle as manual_oracle
from tests.test_ai_phase4 import CASES, _context
from tests.test_ai_phase8 import _oracle as ntuple_oracle
from tests.test_ai_phase9b import _legal, _near_end
from tests.test_ai_phase9c import MODEL


def _other(turn):
    """独立refereeに渡す相手色を返す。"""
    return 'W' if turn == 'B' else 'B'


def _mask(positions):
    """refereeの座標集合を、比較するビット番号に写す。"""
    return sum(2 ** (8 * y + x) for x, y in positions)


def _test_positions():
    """合法な4棋譜から、進行度とパスの異なる盤面を収集する。"""
    result = set()
    for seed in (27, 298, 351, 701):
        randomizer = random.Random(seed)
        rows, turn, plies = referee.INITIAL_ROWS, 'B', 0
        while True:
            choices = referee.legal_moves(rows, turn)
            if plies % 3 == 0 or not choices:
                result.add(rows)
            if choices:
                rows = referee.apply_move(rows, turn, randomizer.choice(choices))
                plies += 1
            elif not referee.legal_moves(rows, _other(turn)):
                break
            turn = _other(turn)
    return sorted(result)


def test_board_coordinate_bijection():
    """端列・対角・四隅を含む全64ビットを両色で照合する。"""
    for x in range(8):
        for y in range(8):
            for turn in ('B', 'W'):
                rows = tuple(
                    'E' * x + turn + 'E' * (7 - x) if row == y else 'E' * 8 for row in range(8)
                )
                p = bitboard.BitPosition.from_rows(rows)
                expected = 1 << (8 * y + x)
                assert (p.black, p.white) == ((expected, 0) if turn == 'B' else (0, expected))
                assert p.to_rows() == rows
                assert p.disc_difference(turn) == 1
                assert not p.legal_moves('B') and not p.legal_moves('W')


@pytest.mark.parametrize('black,white', [(1, 1), (-1, 0), (0, 1 << 64), (True, 0), (0, 'W')])
def test_invalid_bit_position(black, white):
    """重複、盤外、符号、真偽値を位置に使用できない。"""
    with pytest.raises(ValueError, match='invalid_bit_position'):
        bitboard.BitPosition(black, white)


@pytest.mark.parametrize('move', [(8, 0), (-1, -1), (True, 0), (0, 0), (3, 3)])
def test_illegal_move_is_rejected(move):
    """盤外・占有・非反転を合法な着手として扱わない。"""
    p = bitboard.BitPosition.from_rows(referee.INITIAL_ROWS)
    with pytest.raises(ValueError, match='_move'):
        p.play('B', move)


def test_board_and_every_legal_child_match_both_oracles():
    """複数棋譜と既存の境界局面で、順序と全合法子を三者で照合する。"""
    positions = _test_positions()
    assert len(positions) >= 65
    special = [
        tuple(CASES[key]['rows']) for key in ('multiple_directions', 'pass', 'terminal_with_empty')
    ]
    positions += [*special, ('BWEEEEEE',) + ('EEEEEEEE',) * 7]
    for rows in positions:
        p = bitboard.BitPosition.from_rows(rows)
        assert p.to_rows() == rows
        for turn in ('B', 'W'):
            expected = tuple(referee.legal_moves(rows, turn))
            assert p.legal_moves(turn) == reversi.legal_moves_for(rows, turn) == expected
            assert p.legal_mask(turn) == _mask(expected)
            assert p.disc_difference(turn) == sum(
                row.count(turn) - row.count(_other(turn)) for row in rows
            )
            for move in expected:
                assert p.flips(turn, move) == _mask(referee.flipped_cells(rows, turn, move))
                child = p.play(turn, move)
                assert (
                    child.to_rows()
                    == reversi.apply_move(rows, turn, move)
                    == referee.apply_move(rows, turn, move)
                )
            assert p.to_rows() == rows


@pytest.mark.parametrize('depth,expected', [(0, 1), (1, 4), (2, 12), (3, 56), (4, 244)])
def test_initial_perft_counts(depth, expected):
    """初期盤面の小深度手順数を独立refereeと既知件数で照合する。"""
    assert _perft(referee.INITIAL_ROWS, 'B', depth, _legal, referee.apply_move) == expected
    assert (
        _perft(
            bitboard.BitPosition.from_rows(referee.INITIAL_ROWS),
            'B',
            depth,
            bitboard.BitPosition.legal_moves,
            bitboard.BitPosition.play,
        )
        == expected
    )


def _perft(state, turn, depth, legal, play):
    """着手のみで深さを減らし、深さ0と早期終局は葉1件と数える。"""
    if not depth:
        return 1
    moves = legal(state, turn)
    opponent = _other(turn)
    if not moves:
        return _perft(state, opponent, depth, legal, play) if legal(state, opponent) else 1
    return sum(_perft(play(state, turn, move), opponent, depth - 1, legal, play) for move in moves)


def test_perft_pass_and_early_terminal():
    """パスと空きのある終局で残り着手数を誤って数えない。"""
    for rows in [tuple(CASES['pass']['rows']), ('BWEEEEEE',) + ('EEEEEEEE',) * 7]:
        p = bitboard.BitPosition.from_rows(rows)
        for turn in ('B', 'W'):
            exact = _perft(rows, turn, 3, _legal, referee.apply_move)
            assert (
                _perft(p, turn, 3, bitboard.BitPosition.legal_moves, bitboard.BitPosition.play)
                == exact
            )
    assert _perft(p, 'W', 6, bitboard.BitPosition.legal_moves, bitboard.BitPosition.play) == 1


def _search(rows, turn, depth, *, backend='array', leaf=None, clock=lambda: 0):
    """凍結時計で同じ時間探索を最後まで実行する。"""
    return timed_search.search_iterative(
        rows,
        turn,
        _legal(rows, turn),
        reversi.legal_moves_for,
        reversi.apply_move,
        command_received_ns=0,
        policy=timed_search.ResponsePolicy(max_depth=depth),
        clock=clock,
        leaf_evaluator=leaf,
        board_backend=backend,
    )


@pytest.mark.parametrize('depth', [5, 6])
@pytest.mark.parametrize('turn', ['B', 'W'])
@pytest.mark.parametrize('model', [False, True])
def test_search_depth_value_and_counters(depth, turn, model):
    """評価、探索値、根の手、枝刈りまで配列・全幅探索と一致する。"""
    rows = _near_end()
    leaf = MODEL.value if model else None
    expected = _search(rows, turn, depth, leaf=leaf)
    actual = _search(rows, turn, depth, backend='bitboard', leaf=leaf)
    assert actual == expected
    if not _legal(rows, turn):
        assert actual.move is None
    elif model:
        value, move = ntuple_oracle(rows, turn, depth, MODEL.value)
        assert (actual.move, actual.value) == (move, value if turn == 'B' else -value)
    else:
        value, tied, _ = manual_oracle(rows, turn, depth)
        assert (actual.move, actual.value) == (tied[0], float(value))


def test_leaf_rows_and_root_tie_keep_existing_semantics():
    """既存評価に渡る全葉の行・手番・値の順と、根の同点規則を保つ。"""
    data = []
    rows = referee.INITIAL_ROWS

    def leaf(snapshot, turn):
        """入力を記録し、全葉で同じ既定の点を返す。"""
        data.append((snapshot, turn, MODEL.value(snapshot, turn)))
        return 0.0

    a = _search(rows, 'B', 3, leaf=leaf)
    visited = list(data)
    data.clear()
    b = _search(rows, 'B', 3, backend='bitboard', leaf=leaf)
    assert b == a and data == visited
    assert b.move == _legal(rows, 'B')[0]


@pytest.mark.parametrize('depth', [5, 6])
def test_incomplete_depth_keeps_last_result(depth):
    """仮想時計でD5・D6の中断を再現し、直前反復に戻す。"""
    rows = _near_end()
    turn = 'B' if _legal(rows, 'B') else 'W'
    ticks, threshold = 0, float('inf')

    def clock():
        """直前の完全反復より少し先で期限を発火させる。"""
        nonlocal ticks
        ticks += 1
        return 0 if ticks <= threshold else 7_000_000_000

    before = _search(rows, turn, depth - 1, backend='bitboard', leaf=MODEL.value, clock=clock)
    threshold, ticks = ticks + 7, 0
    after = _search(rows, turn, depth, backend='bitboard', leaf=MODEL.value, clock=clock)
    assert after.stop_reason == 'time_limit'
    assert after.max_depth_started == depth
    assert (after.completed_depth, after.move, after.value) == (
        before.completed_depth,
        before.move,
        before.value,
    )


@pytest.mark.parametrize(
    'kind,mode,extra,accepted',
    [
        ('time', 'bitboard', {}, True),
        ('time', 'array', {}, True),
        ('time', 'wrong', {}, False),
        ('time', 'bitboard', {'tt_capacity': 4}, False),
        ('time', 'bitboard', {'move_ordering': 'pv'}, False),
        ('time', 'bitboard', {'ordered': False}, False),
        ('depth', 'bitboard', {}, False),
    ],
)
def test_external_backend_config(kind, mode, extra, accepted, tmp_path):
    """実プロセスのtime設定だけがBitboardを明示できる。"""
    budget = (
        {'kind': 'time', 'limit_ns': 6_500_000_000}
        if kind == 'time'
        else {'kind': 'depth', 'limit_depth': 4}
    )
    cfg = {'format_version': 2, 'budget': budget, 'search': {'board_backend': mode, **extra}}
    path, trace = tmp_path / 'config.json', tmp_path / 'trace.jsonl'
    path.write_text(json.dumps(cfg), encoding='utf-8')

    def connect():
        """trace4の検証用設定で子を起動する。"""
        return protocol.Player(
            Path(reversi.__file__).resolve(),
            evaluation_config=path,
            evaluation_trace=trace,
            evaluation_context=_context(cfg),
        )

    if not accepted:
        with pytest.raises(protocol.HarnessError):
            connect()
        assert not trace.exists()
        return
    with connect() as p:
        move = tuple(map(int, p.command('genmove B')[0].split()))
        assert move in _legal(referee.INITIAL_ROWS, 'B')
    events = [json.loads(line) for line in trace.read_bytes().splitlines()]
    assert events[0]['effective_config'] == cfg
    assert sum(e['type'] == 'genmove' for e in events) == 1


def test_gui_and_direct_model_process(tmp_path, monkeypatch):
    """GUI入口とcwdを外した直接起動でモデル指定と連続着手を照合する。"""
    model_path = tmp_path / 'model.json'
    ntuple_io.save_model(model_path, MODEL)
    monkeypatch.setenv(timed_search.BOARD_BACKEND_ENV, 'bitboard')
    monkeypatch.setenv(timed_search.SEARCH_DEPTH_ENV, '3')
    monkeypatch.setenv(search_trace.MODEL_ENV, str(model_path))
    with web.Process() as gui:
        assert gui.command('name') == 'ranger'
        response = tuple(map(int, gui.command('genmove B').split()))
        assert response in _legal(referee.INITIAL_ROWS, 'B')
    result = subprocess.run(
        [sys.executable, '-B', str(Path(reversi.__file__).resolve())],
        cwd=tmp_path,
        input='name\ngenmove B\ngenmove W\nshowboard\n',
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    lines = result.stdout.splitlines()
    assert len(lines) == 11 and lines[0] == 'ranger' and not result.stderr
    first, second = (tuple(map(int, line.split())) for line in lines[1:3])
    after = referee.apply_move(referee.INITIAL_ROWS, 'B', first)
    assert tuple(lines[3:]) == referee.apply_move(after, 'W', second)
