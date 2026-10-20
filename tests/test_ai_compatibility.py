"""Phase 1Aの選択器分離前後で維持するランダムAIの互換性を検証する。"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import contextlib
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.protocol import Player
from harness import referee
from reversi_player import ai_engine
from reversi_player import reversi as reversi_module
from reversi_player.reversi import Board, Pos

ROOT = Path(__file__).resolve().parents[1]
CURRENT_SCRIPT = ROOT / 'reversi_player' / 'reversi.py'
BASELINE_SCRIPT = ROOT / 'baselines' / 'random_v0' / 'reversi.py'
BASELINE_SHA256 = 'a485400c335a8731a0cafd1d77d98fbbcda1289194c4e13147cbaf3135b12b41'
COMPATIBILITY_SEEDS = (0, 7, 31, 73, 20261016)
pytestmark = pytest.mark.skipif(
    sys.platform != 'win32' or sys.version_info[:3] != (3, 13, 5),
    reason='random_v0の完全一致基準はWindows CPython 3.13.5に固定',
)


def _rows(board: Board) -> tuple[str, ...]:
    """盤面をセル[y][x]順の不変な8行へ変換する。"""
    return tuple(''.join(row) for row in board.cell)


def _play_seeded_game(script: Path, seed: int) -> tuple[tuple[object, ...], ...]:
    if script == CURRENT_SCRIPT:
        return _play_seeded_random_board(seed)

    """seed付きプロセスを終局まで動かし、時間値を除く応答列を記録する。"""
    trace: list[tuple[object, ...]] = []
    color = 'B'
    consecutive_passes = 0

    with Player(script, seed=seed) as player:
        trace.append(('initial', tuple(player.command('showboard'))))

        for _ in range(128):
            response = tuple(player.command('genmove ' + color))
            rows = tuple(player.command('showboard'))
            result = tuple(player.command('result'))
            trace.append((color, response, rows, result))

            if response == ('NG',):
                consecutive_passes += 1
            else:
                consecutive_passes = 0

            if result in (('B',), ('W',), ('D',)):
                assert consecutive_passes >= 1 or response != ('NG',)
                break

            color = 'W' if color == 'B' else 'B'
        else:
            pytest.fail('128着手以内に対局が終局しなかった')

        assert player.command('result') == list(result)

    return tuple(trace)


def _play_seeded_random_board(seed: int) -> tuple[tuple[object, ...], ...]:
    """旧ランダムselectorを明示注入し、基準版と比較する棋譜を作る。"""
    trace: list[tuple[object, ...]] = []
    color = 'B'
    consecutive_passes = 0
    previous_state = random.getstate()

    try:
        random.seed(seed)
        board = Board(selector=ai_engine.select_move)
        trace.append(('initial', _rows(board)))

        for _ in range(128):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                board.genmove(color)
            response = (output.getvalue().strip(),)
            rows = _rows(board)
            result = (board.result(),)
            trace.append((color, response, rows, result))

            if response == ('NG',):
                consecutive_passes += 1
            else:
                consecutive_passes = 0

            if result in (('B',), ('W',), ('D',)):
                assert consecutive_passes >= 1 or response != ('NG',)
                break

            color = 'W' if color == 'B' else 'B'
        else:
            pytest.fail('128着手以内に対局が終局しなかった')
    finally:
        random.setstate(previous_state)

    return tuple(trace)


def test_random_v0_reference_copy_matches_its_manifest() -> None:
    """棋譜比較の参照実装が固定manifestの内容から変わっていないことを確認する。"""
    manifest = json.loads((BASELINE_SCRIPT.parent / 'manifest.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(BASELINE_SCRIPT.read_bytes()).hexdigest()

    assert digest == BASELINE_SHA256
    assert manifest['baseline_id'] == 'random_v0'
    assert manifest['frozen_copy']['sha256'] == digest
    assert manifest['runtime']['version'] == '3.13.5'


@pytest.mark.parametrize('seed', COMPATIBILITY_SEEDS)
def test_seeded_full_game_matches_frozen_random_v0(seed: int) -> None:
    """同一seedの全着手・盤面・終局結果を固定random_v0と照合する。"""
    assert _play_seeded_random_board(seed) == _play_seeded_game(BASELINE_SCRIPT, seed)


def test_genmove_preserves_candidate_order_shuffle_count_and_rng_state() -> None:
    """初期黒番のy/x候補順とshuffle一回分の乱数状態を固定する。"""
    seed = 20261016
    expected_candidates = [(3, 2), (2, 3), (5, 4), (4, 5)]
    expected_rng = random.Random(seed)
    expected_rng.shuffle(expected_candidates)
    original_shuffle = random.shuffle
    seen_candidates: list[tuple[int, int]] | None = None

    def capture_shuffle(candidates: list[tuple[int, int]]) -> None:
        nonlocal seen_candidates
        seen_candidates = candidates[:]
        original_shuffle(candidates)

    previous_state = random.getstate()
    try:
        random.seed(seed)
        board = Board(selector=ai_engine.select_move)
        with patch.object(reversi_module.random, 'shuffle', side_effect=capture_shuffle) as shuffle:
            selected = board._genmove('B')

        assert shuffle.call_count == 1
        assert seen_candidates == [(3, 2), (2, 3), (5, 4), (4, 5)]
        assert selected is not None
        assert (selected.x, selected.y) == expected_candidates[0]
        assert _rows(board) == referee.apply_move(
            referee.INITIAL_ROWS,
            'B',
            expected_candidates[0],
        )
        assert random.getstate() == expected_rng.getstate()
    finally:
        random.setstate(previous_state)


def test_no_legal_move_does_not_shuffle_or_consume_random_state() -> None:
    """合法手がない場合はNoneを返し、盤面と乱数状態を保つ。"""
    previous_state = random.getstate()
    try:
        random.seed(73)
        board = Board()
        board.cell = [list('W' * 8) for _ in range(8)]
        before = _rows(board)
        state_before = random.getstate()

        with patch.object(reversi_module.random, 'shuffle') as shuffle:
            assert board._genmove('B') is None

        assert shuffle.call_count == 0
        assert _rows(board) == before
        assert random.getstate() == state_before
    finally:
        random.setstate(previous_state)


def test_board_construction_and_rule_operations_do_not_consume_random_state() -> None:
    """選択以外の盤面操作でmodule-level random状態を進めない。"""
    previous_state = random.getstate()
    try:
        random.seed(31)
        state_before = random.getstate()
        board = Board()
        assert board._ask('B', Pos(2, 3))
        assert board._play('B', Pos(2, 3))
        assert board.result() == 'NG'
        assert random.getstate() == state_before
    finally:
        random.setstate(previous_state)
