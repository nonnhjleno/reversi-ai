"""角接続辺石の確定性を独立レフェリーで検査する。"""

import random

from harness import referee
from reversi_player import ai_engine


def _reachable_small_endgame(seed):
    """固定seedの合法ランダム棋譜から、空き4以下の非終局局面を得る。"""
    rng = random.Random(seed)
    rows, color = referee.INITIAL_ROWS, 'B'
    while True:
        moves = referee.legal_moves(rows, color)
        other = 'W' if color == 'B' else 'B'
        if not moves and not referee.legal_moves(rows, other):
            return None
        if sum(row.count('E') for row in rows) <= 4:
            stable = ai_engine._corner_connected_edge_stones(rows)
            if stable:
                return rows, color, stable
        if moves:
            rows = referee.apply_move(rows, color, rng.choice(moves))
        color = other


def _all_legal_descendants(rows, color):
    """参照レフェリーだけで、パスを含む到達可能な全子局面を列挙する。"""
    pending = [(rows, color)]
    seen = set()
    while pending:
        current, turn = pending.pop()
        state = (current, turn)
        if state in seen:
            continue
        seen.add(state)
        moves = referee.legal_moves(current, turn)
        other = 'W' if turn == 'B' else 'B'
        if moves:
            pending.extend((referee.apply_move(current, turn, move), other) for move in moves)
        elif referee.legal_moves(current, other):
            pending.append((current, other))
    return tuple(board for board, _ in seen)


def test_corner_connected_edge_subset_never_flips_on_all_legal_continuations():
    """8つの合法到達局面で、全進行の各中間盤面における不変性を確認する。"""
    positions = []
    for seed in range(128):
        position = _reachable_small_endgame(seed)
        if position is not None:
            positions.append(position)
        if len(positions) == 8:
            break

    assert len(positions) == 8
    total_descendants = 0
    for rows, color, stable in positions:
        descendants = _all_legal_descendants(rows, color)
        total_descendants += len(descendants)
        for board in descendants:
            for x, y in stable:
                assert board[y][x] == rows[y][x]
    assert total_descendants >= len(positions)


def test_edge_subset_excludes_corners_and_never_claims_other_stable_discs():
    """角を別特徴として扱い、検出しない石を不安定とも安定とも判定しない。"""
    rows = ('BBBBBBBB',) + ('BEEEEEEB',) * 6 + ('BBBBBBBB',)
    stable = ai_engine._corner_connected_edge_stones(rows)
    assert len(stable) == 24
    assert all((x, y) not in ai_engine._CORNERS for x, y in stable)
    assert all(rows[y][x] == 'B' for x, y in stable)
    assert (3, 3) not in stable
