"""明示指定のn-tuple資産を使う参考対局用のプロトコル入口。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reversi_player import ai_engine, ntuple, ntuple_io, reversi
elif __package__:
    from . import ai_engine, ntuple, ntuple_io, reversi
else:
    import ntuple_io
    import reversi


MODEL_ENV = 'REVERSI_NTUPLE_MODEL'


def _child_score(model: ntuple.NtupleModel, rows: ai_engine.BoardRows, turn: str) -> float:
    """着手後の黒視点の比較スコアを返す。確定勝敗は推定範囲の外に置く。"""
    opponent = 'W' if turn == 'B' else 'B'
    if reversi.legal_moves_for(rows, turn) or reversi.legal_moves_for(rows, opponent):
        return model.value(rows, turn)
    difference = sum(row.count('B') - row.count('W') for row in rows)
    if difference > 0:
        return 2 + difference / 64
    if difference < 0:
        return -2 + difference / 64
    return 0.0


def make_selector(model: ntuple.NtupleModel) -> ai_engine.MoveSelector:
    """Boardの合法手集合を比較し、同点は行・列順の先頭を選ぶ。"""

    def select(
        rows: ai_engine.BoardRows, color: str, moves: ai_engine.LegalMoves
    ) -> ai_engine.Move | None:
        """コピーに適用した着手を評価し、実盤面には触れない。"""
        next_turn = 'W' if color == 'B' else 'B'
        sign = -1 if color == 'B' else 1

        def rank(move: ai_engine.Move) -> tuple[float, int, int]:
            """黒は最大化、白は最小化し、行・列を同点判定に使う。"""
            child = reversi.apply_move(rows, color, move)
            return sign * _child_score(model, child, next_turn), move[1], move[0]

        return min(moves, key=rank, default=None)

    return select


def main() -> int:
    """起動時にモデルを一度読み、既存のプロトコルで応答する。"""
    try:
        config_path = os.environ.get('REVERSI_EVAL_CONFIG')
        if config_path:
            config = json.loads(Path(config_path).read_text(encoding='utf-8'))
            # ハーネスの非計測設定だけを受け入れ、traceは生成しない。
            if config != {'format_version': 2, 'budget': {'kind': 'unknown'}, 'search': {}} or (
                type(config['format_version']) is not int
            ):
                raise ValueError('phase6_does_not_support_search_config')
        path = Path(os.environ.get(MODEL_ENV, ''))
        if not path.is_absolute():
            raise ValueError(MODEL_ENV + '_requires_absolute_path')
        artifact = ntuple_io.load_model(path)
    except (OSError, ValueError) as failure:
        print(
            f'phase6_model_load_error: {type(failure).__name__}: {failure}',
            file=sys.stderr,
            flush=True,
        )
        return 2

    board = reversi.Board(selector=make_selector(artifact.model))
    for line in sys.stdin:
        cmd = line.split()
        if cmd and not reversi._dispatch(board, cmd):
            break
    return 0


if __name__ == '__main__':
    sys.exit(main())
