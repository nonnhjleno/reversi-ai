"""Phase 9-Eの参照列、評価、探索、起動設定を独立計算と照合する。"""

from functools import partial
from itertools import product
from math import fsum, tanh
from pathlib import Path
import json
import subprocess
import sys

import pytest

from harness import protocol, referee
from reversi_player import bitboard, ntuple, ntuple_bitboard, ntuple_io, reversi
from reversi_player import search_trace, timed_search, web
from tests.test_ai_phase4 import _context
from tests.test_ai_phase8 import _oracle as ntuple_oracle
from tests.test_ai_phase9b import _legal, _near_end
from tests.test_phase6_ntuple import _boards, _LINES, _TERNARY, _reachable, _weight_model


def _ordered_oracle(rows, turn):
    """独立に列挙した線と盤面の回転像から、出現順の参照番号を得る。"""
    # _boardsの転置・反転の順を既存契約のD4順に並べ替える。
    images = tuple(_boards(rows))
    offset = {'B': 0, 'W': 936}[turn]
    for coords in _LINES:
        codes = _TERNARY[len(coords)]
        for i in (0, 6, 3, 5, 1, 2, 4, 7):
            image = images[i]
            yield offset + codes[tuple(image[y][x] for x, y in coords)]
        offset += len(codes)


def test_cell_digits_and_all_pattern_codes():
    """各マスの3状態と、両長さの全符号を手作りの盤面で調べる。"""
    for i in range(64):
        for cell, want in (('E', 0), ('B', 1), ('W', 2)):
            flat = ['E'] * 64
            flat[i] = cell
            rows = tuple(''.join(flat[j : j + 8]) for j in range(0, 64, 8))
            p = bitboard.BitPosition.from_rows(rows)
            digits = ntuple_bitboard._digits(p)
            assert len(digits) == 64 and digits[i] == want and sum(digits) == want
    for length, index, position, offset in ((2, 0, 0, 0), (3, 32, 256, 288)):
        for code, states in enumerate(product('EBW', repeat=length)):
            board = [list('EEEEEEEE') for _ in range(8)]
            for (x, y), value in zip(_LINES[index], states, strict=True):
                board[y][x] = value
            p = bitboard.BitPosition.from_rows(tuple(''.join(row) for row in board))
            assert tuple(ntuple_bitboard.references(p, 'B'))[position] == offset + code


@pytest.mark.parametrize('seed', [0, 17, 99])
def test_reference_sequence_and_binary64(seed):
    """合法棋譜・D4像・重複に対し、順、重み、総和、価値を照合する。"""
    if seed == 0:
        model = _weight_model()
    else:
        weights = []
        for i in range(ntuple.WEIGHT_COUNT):
            j = (i * 839 + seed) % 1777
            weights.append((j - 889) / 301 * (2.0 ** ((j % 183) - 91)))
        model = ntuple.NtupleModel(tuple(weights))
    before = model.weights
    # 全進行段階を残しつつ、通常品質ゲートの比較量を抑える。
    positions = [(r, t) for n, (r, t, _) in enumerate(_reachable()) if n % 11 == seed % 11]
    positions.extend((tuple(''.join(r) for r in image), 'W') for image in _boards(_near_end()))
    positions.extend([(('EEEEEEEE',) * 8, 'B'), (('BBBBBBBB',) * 8, 'W')])
    for rows, turn in positions:
        p = bitboard.BitPosition.from_rows(rows)
        expected = tuple(_ordered_oracle(rows, turn))
        refs = tuple(ntuple_bitboard.references(p, turn))
        assert len(refs) == 448
        assert refs == expected == tuple(ntuple._references(rows, turn))
        expected_weights = [model.weights[i] for i in expected]
        assert [model.weights[i] for i in refs] == expected_weights
        score = fsum(expected_weights)
        assert ntuple_bitboard.logit(p, turn, model=model).hex() == score.hex()
        assert ntuple_bitboard.value(p, turn, model=model).hex() == model.value(rows, turn).hex()
        assert ntuple_bitboard.value(p, turn, model=model).hex() == tanh(score).hex()
        assert p.to_rows() == rows
    assert model.weights is before


@pytest.mark.parametrize(
    'position,turn',
    [((), 'B'), (bitboard.BitPosition(0, 0), 'X'), (bitboard.BitPosition(0, 0), True)],
)
def test_invalid_direct_position(position, turn):
    """新しい入口でも盤面や手番の誤りを拒む。"""
    with pytest.raises(ValueError):
        ntuple_bitboard.references(position, turn)


def _search(rows, turn, depth, model, direct, *, clock=lambda: 0, budget=None):
    """Phase 9-Dと直接評価で、ホットパス以外を固定する。"""
    return timed_search.search_iterative(
        rows,
        turn,
        _legal(rows, turn),
        reversi.legal_moves_for,
        reversi.apply_move,
        command_received_ns=0,
        policy=timed_search.ResponsePolicy(max_depth=depth),
        clock=clock,
        limit_ns=budget,
        leaf_evaluator=model.value,
        board_backend='bitboard',
        bitboard_evaluator=partial(ntuple_bitboard.value, model=model) if direct else None,
    )


@pytest.mark.parametrize('depth', [4, 5, 6])
@pytest.mark.parametrize('turn', ['B', 'W'])
def test_fixed_search_and_full_width_oracle(depth, turn):
    """固定探索値・手・全計数と独立全幅の最善手を一致させる。"""
    rows = _near_end()
    model = _weight_model()
    actual = _search(rows, turn, depth, model, True)
    assert actual == _search(rows, turn, depth, model, False)
    if _legal(rows, turn):
        value, move = ntuple_oracle(rows, turn, depth, model.value)
        assert (actual.value.hex(), actual.move) == (
            float(value if turn == 'B' else -value).hex(),
            move,
        )
    else:
        assert actual.move is None


def test_terminal_and_tie():
    """終局の厳密値、片側パス、同点順を保つ。"""
    p = _weight_model()
    rows = ('BWEEEEEE',) + ('EEEEEEEE',) * 7
    for turn in ('B', 'W'):
        assert _search(rows, turn, 6, p, True) == _search(rows, turn, 6, p, False)
    result = _search(referee.INITIAL_ROWS, 'B', 2, ntuple.NtupleModel.zeros(), True)
    assert result.move == _legal(referee.INITIAL_ROWS, 'B')[0]


@pytest.mark.parametrize('interval', [100_000, 1_000_000])
def test_deadline_and_fallback(interval):
    """同じ時計に対して直前の反復結果や代替手を保つ。"""
    snapshots = []
    for flag in (False, True):
        queue = iter(range(0, 1_000_000_000, interval))
        x = _search(
            referee.INITIAL_ROWS,
            'B',
            5,
            _weight_model(),
            flag,
            clock=lambda: next(queue),
            budget=10_000_000,
        )
        snapshots.append(x)
    assert snapshots[0] == snapshots[1]
    assert snapshots[1].move in _legal(referee.INITIAL_ROWS, 'B')


@pytest.fixture
def artifact(tmp_path):
    """テスト内だけで使う凍結モデルと同一性情報を用意する。"""
    path = tmp_path / 'fixture.json'
    ntuple_io.save_model(path, _weight_model())
    return path, ntuple_io.load_model(path)


@pytest.mark.parametrize(
    'backend,kind,include_model,success',
    [
        ('bitboard', 'time', True, True),
        ('array', 'time', True, False),
        ('bitboard', 'time', False, False),
        ('bitboard', 'depth', True, False),
    ],
)
def test_trace_setting(backend, kind, include_model, success, artifact, tmp_path):
    """直接評価の設定が既存のtrace4に残り、不適切な組み合わせでは起動を止める。"""
    path, model = artifact
    budget = (
        {'kind': kind, 'limit_ns': 6_500_000_000}
        if kind == 'time'
        else {'kind': kind, 'limit_depth': 4}
    )
    search = {'ntuple_backend': 'bitboard', 'board_backend': backend}
    if include_model:
        search.update(model_path=str(path), model_id=model.model_id, model_sha256=model.file_sha256)
    cfg = {'format_version': 2, 'budget': budget, 'search': search}
    config, trace = tmp_path / 'configuration.json', tmp_path / 'trace.jsonl'
    config.write_text(json.dumps(cfg), encoding='utf-8')

    def connect():
        """既存ハーネスの子プロセス入口を使う。"""
        return protocol.Player(
            Path(reversi.__file__).resolve(),
            evaluation_config=config,
            evaluation_trace=trace,
            evaluation_context=_context(cfg),
        )

    if success:
        with connect() as player:
            assert tuple(map(int, player.command('genmove B')[0].split())) in _legal(
                referee.INITIAL_ROWS, 'B'
            )
        assert json.loads(trace.read_bytes().splitlines()[0])['effective_config'] == cfg
    else:
        with pytest.raises(protocol.HarnessError):
            connect()
        assert not trace.exists()


def test_standalone_and_gui(artifact, monkeypatch, tmp_path):
    """通常の直接起動とGUI入口にモデル・Bitboardの明示設定が通る。"""
    model_path, _ = artifact
    monkeypatch.setenv(search_trace.MODEL_ENV, str(model_path))
    monkeypatch.setenv(timed_search.BOARD_BACKEND_ENV, 'bitboard')
    monkeypatch.setenv(search_trace.NTUPLE_BACKEND_ENV, 'bitboard')
    monkeypatch.setenv(timed_search.SEARCH_DEPTH_ENV, '2')
    with web.Process() as gui:
        assert gui.command('name') == 'ranger'
        move = tuple(map(int, gui.command('genmove B').split()))
        assert move in _legal(referee.INITIAL_ROWS, 'B')
    done = subprocess.run(
        [sys.executable, '-B', str(Path(reversi.__file__).resolve())],
        cwd=tmp_path,
        input='name\ngenmove B\nquit\n',
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    name, answer = done.stdout.splitlines()
    assert name == 'ranger' and not done.stderr
    assert tuple(map(int, answer.split())) in _legal(referee.INITIAL_ROWS, 'B')
