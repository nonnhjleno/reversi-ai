"""リバーシAIの着手選択を行う。盤面規則や入出力には依存しない。"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

type BoardRows = tuple[str, ...]
type Move = tuple[int, int]
type LegalMoves = tuple[Move, ...]
type MoveSelector = Callable[[BoardRows, str, LegalMoves], Move | None]
type LegalMovesFor = Callable[[BoardRows, str], LegalMoves]
type ApplyMove = Callable[[BoardRows, str, Move], BoardRows]

POSITION_WEIGHTS = (
    (120, -20, 20, 5, 5, 20, -20, 120),
    (-20, -40, -5, -5, -5, -5, -40, -20),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (-20, -40, -5, -5, -5, -5, -40, -20),
    (120, -20, 20, 5, 5, 20, -20, 120),
)


def select_move(rows: BoardRows, color: str, legal_moves: LegalMoves) -> Move | None:
    """順序付き合法手の複製をshuffleし、先頭の座標を返す。

    rowsとcolorは将来の選択方式と共通の入力契約として受け取る。
    入力は変更せず、候補が空なら乱数を消費せずNoneを返す。
    """
    candidates = list(legal_moves)
    if not candidates:
        return None

    random.shuffle(candidates)
    return candidates[0]


def select_positional_move(rows: BoardRows, color: str, legal_moves: LegalMoves) -> Move | None:
    """着手先の固定位置重みを比較し、同点は(y, x)昇順で返す。

    ``legal_moves``はBoardが列挙した完全な合法手集合とする。盤面・候補・乱数状態は変更しない。
    空候補ではNoneを返し、重みは学習済みではない暫定的な人手設定を使う。
    """
    if not legal_moves:
        return None

    return min(
        legal_moves,
        key=lambda move: (-POSITION_WEIGHTS[move[1]][move[0]], move[1], move[0]),
    )


_CORNERS = ((0, 0), (7, 0), (0, 7), (7, 7))


def validate_position(rows: BoardRows, color: str) -> None:
    """通常tupleの8×8 B/W/E snapshotと通常strのB/Wを検査する。

    callbackと評価で同じ値域を使用し、違反はValueErrorにする。
    盤面の到達可能性や過去の手番はこの静的snapshot境界では検査しない。
    """
    if (
        type(rows) is not tuple
        or len(rows) != 8
        or any(
            type(row) is not str or len(row) != 8 or any(cell not in 'BWE' for cell in row)
            for row in rows
        )
    ):
        raise ValueError('invalid_board_rows')
    if type(color) is not str or color not in ('B', 'W'):
        raise ValueError('invalid_color')


@dataclass(frozen=True)
class _PositionFeatures:
    """固定視点の整数特徴と終局判定。外部の勝ち点率や探索予算ではない。"""

    corner_difference: int
    mobility_difference: int
    position_difference: int
    disc_difference: int
    terminal: bool

    @property
    def normalized(self) -> tuple[float, float, float]:
        """角・mobility・位置を固定分母で正規化して返す。"""
        return (
            self.corner_difference / 4,
            self.mobility_difference / 64,
            self.position_difference / 696,
        )

    @property
    def contributions(self) -> tuple[float, float, float]:
        """非終局式の3項の寄与を診断用に返す。終局得点とは区別する。"""
        return (
            10.0 * self.corner_difference,
            40 * self.mobility_difference / 64,
            20 * self.position_difference / 696,
        )


def _effective_position_weights(rows: BoardRows) -> tuple[tuple[int, ...], ...]:
    """角の二重加点と取得後の隣接負点を除いた独立の表を作る。"""
    weights = [list(row) for row in POSITION_WEIGHTS]
    for x, y in _CORNERS:
        weights[y][x] = 0
        if rows[y][x] != 'E':
            adjacent_x, adjacent_y = (1 if x == 0 else 6), (1 if y == 0 else 6)
            for nx, ny in ((adjacent_x, y), (x, adjacent_y), (adjacent_x, adjacent_y)):
                weights[ny][nx] = 0
    return tuple(tuple(row) for row in weights)


def _extract_features(
    rows: BoardRows, viewpoint: str, legal_moves_for: LegalMovesFor
) -> _PositionFeatures:
    """両色の完全合法手数と盤面を観測し、固定視点の整数特徴を返す。

    callbackはstatelessで盤面を変更せず完全な合法手tupleを返す契約とする。
    callbackの故障は呼出側へ伝播し、不完全な結果を補完しない。
    """
    validate_position(rows, viewpoint)
    opponent = 'W' if viewpoint == 'B' else 'B'
    own_moves = legal_moves_for(rows, viewpoint)
    opponent_moves = legal_moves_for(rows, opponent)
    weights = _effective_position_weights(rows)
    corner_difference = sum(
        (rows[y][x] == viewpoint) - (rows[y][x] == opponent) for x, y in _CORNERS
    )
    position_difference = sum(
        weights[y][x] * ((cell == viewpoint) - (cell == opponent))
        for y, row in enumerate(rows)
        for x, cell in enumerate(row)
    )
    return _PositionFeatures(
        corner_difference,
        len(own_moves) - len(opponent_moves),
        position_difference,
        sum(row.count(viewpoint) - row.count(opponent) for row in rows),
        not own_moves and not opponent_moves,
    )


def _score_features(features: _PositionFeatures) -> float:
    """確定勝敗を優先し、非終局は整数分子を一度だけfloatへ変換する。"""
    if features.terminal:
        difference = features.disc_difference
        if difference > 0:
            return float(1000 + difference)
        if difference < 0:
            return float(-1000 + difference)
        return 0.0

    # 40*c/4 + 40*m/64 + 20*p/696 と同値。同じ有理数の丸め方を統一する。
    numerator = (
        6960 * features.corner_difference
        + 435 * features.mobility_difference
        + 20 * features.position_difference
    )
    return numerator / 696


def evaluate_position(rows: BoardRows, viewpoint: str, legal_moves_for: LegalMovesFor) -> float:
    """指定色の固定視点で盤面価値を返す。盤面・乱数・標準出力は変更しない。

    両色に合法手がなければ終局。一方だけのパスは非終局として採点する。
    非終局の絶対値は100以下で、勝敗確定値はその範囲を超える。
    この得点は勝率ではなく、人手で固定した候補比較用の値である。
    """
    return _score_features(_extract_features(rows, viewpoint, legal_moves_for))


def make_one_ply_selector(legal_moves_for: LegalMovesFor, apply_move: ApplyMove) -> MoveSelector:
    """statelessなルールcallback2個を保持する決定的な3引数selectorを作る。"""

    def _select_one_ply(rows: BoardRows, color: str, legal_moves: LegalMoves) -> Move | None:
        """各候補を仮着手してroot色で採点し、同点は(y,x)順で選ぶ。

        rootの完全な合法手集合はBoardが保証する。空候補ではcallbackを呼ばない。
        仮着手は新snapshotを返す契約で、実Boardの確定着手はこの関数では行わない。
        """
        if not legal_moves:
            return None
        validate_position(rows, color)
        return min(
            legal_moves,
            key=lambda move: (
                -evaluate_position(apply_move(rows, color, move), color, legal_moves_for),
                move[1],
                move[0],
            ),
        )

    return _select_one_ply


DEFAULT_SEARCH_DEPTH = 2
MAX_SEARCH_DEPTH = 4
SEARCH_COUNTING_DEFINITION = 'minimax-call-v1'


@dataclass(frozen=True)
class SearchResult:
    """root視点の最善値・着手と、1探索だけの参考計数を保持する。

    depthはplacement ply。moveは深度0またはroot合法手なしならNone。
    nodesはroot/leaf/pass後も含む再帰呼出し数、leavesは評価返却数、
    passesは深度が残る非終局でパス辺を実際にたどった回数。
    正式trace4の予算・計数証拠ではない。
    """

    move: Move | None
    value: float
    depth: int
    nodes: int
    leaves: int
    passes: int


def _validate_search_depth(depth: int, minimum: int) -> None:
    """浅い参照探索の通常int深度を検査し、boolや範囲外はValueErrorにする。"""
    if type(depth) is not int or not minimum <= depth <= MAX_SEARCH_DEPTH:
        raise ValueError('invalid_search_depth')


def _run_minimax(
    rows: BoardRows,
    color: str,
    depth: int,
    legal_moves_for: LegalMovesFor,
    apply_move: ApplyMove,
    root_moves: LegalMoves,
) -> SearchResult:
    """完全なroot候補から全木を探索する。入力検査は公開入口が担当する。"""
    nodes = leaves = passes = 0

    def _visit(
        snapshot: BoardRows,
        turn: str,
        remaining: int,
        known_moves: LegalMoves | None = None,
    ) -> tuple[float, Move | None]:
        """root固定視点で最大化/最小化し、パスでは残placement数を保つ。"""
        nonlocal nodes, leaves, passes
        nodes += 1
        moves = legal_moves_for(snapshot, turn) if known_moves is None else known_moves
        other = 'W' if turn == 'B' else 'B'
        if remaining == 0 or (not moves and not legal_moves_for(snapshot, other)):
            leaves += 1
            return evaluate_position(snapshot, color, legal_moves_for), None
        if not moves:
            passes += 1
            value, _ = _visit(snapshot, other, remaining)
            return value, None

        maximizing = turn == color
        best_value = float('-inf') if maximizing else float('inf')
        best_move = None
        for move in sorted(moves, key=lambda candidate: (candidate[1], candidate[0])):
            value, _ = _visit(apply_move(snapshot, turn, move), other, remaining - 1)
            # 厳密に改善した場合だけ更新し、同点の先頭(y,x)を保持する。
            if (maximizing and value > best_value) or (not maximizing and value < best_value):
                best_value, best_move = value, move
        return best_value, best_move

    value, move = _visit(rows, color, depth, root_moves)
    return SearchResult(move, value, depth, nodes, leaves, passes)


def search_minimax(
    rows: BoardRows,
    color: str,
    depth: int,
    legal_moves_for: LegalMovesFor,
    apply_move: ApplyMove,
) -> SearchResult:
    """通常intの深度0〜4で剪定なしMinimaxを行い、不変の診断値を返す。

    root色の評価視点を保ち、相手は最小化する。rootパスでも残深度を
    減らさず後続価値を探索するが、返却moveはNone。深度0は評価のみ。
    snapshot/色/深度の違反はValueError、callbackの例外は伝播する。
    入力・実Board・乱数・stdout/stderrを変更せず、自動保存もしない。
    """
    _validate_search_depth(depth, 0)
    validate_position(rows, color)
    return _run_minimax(
        rows, color, depth, legal_moves_for, apply_move, legal_moves_for(rows, color)
    )


def make_minimax_selector(
    legal_moves_for: LegalMovesFor,
    apply_move: ApplyMove,
    *,
    depth: int = DEFAULT_SEARCH_DEPTH,
) -> MoveSelector:
    """通常int深度1〜4の決定的selectorを作る。既定は固定深度2。

    callbackは完全な合法手と独立した新snapshotを返す契約。実盤面への
    bound methodを渡さない。時間中断やfallbackはこの参照実装に含めない。
    """
    _validate_search_depth(depth, 1)

    def _select_minimax(rows: BoardRows, color: str, legal_moves: LegalMoves) -> Move | None:
        """Boardの完全候補から選び、空候補ではcallbackなしNoneを返す。"""
        if not legal_moves:
            return None
        validate_position(rows, color)
        return _run_minimax(rows, color, depth, legal_moves_for, apply_move, legal_moves).move

    return _select_minimax


@dataclass(frozen=True)
class AlphaBetaResult(SearchResult):
    """参照と同じ実呼出し計数に、兄弟を省略した節点数を加える。"""

    cutoffs: int


def _run_alpha_beta(
    rows: BoardRows,
    color: str,
    depth: int,
    legal_moves_for: LegalMovesFor,
    apply_move: ApplyMove,
    root_moves: LegalMoves,
    ordered: bool,
) -> AlphaBetaResult:
    """root順を固定し、内部だけ並べ替えるfail-soft Alpha-Beta。"""
    nodes = leaves = passes = cutoffs = 0

    def _visit(
        snapshot: BoardRows,
        turn: str,
        remaining: int,
        alpha: float,
        beta: float,
        known_moves: LegalMoves | None = None,
    ) -> tuple[float, Move | None]:
        """同じplacement深度とroot視点で実際に訪問した呼出しを数える。"""
        nonlocal nodes, leaves, passes, cutoffs
        nodes += 1
        moves = legal_moves_for(snapshot, turn) if known_moves is None else known_moves
        other = 'W' if turn == 'B' else 'B'
        if remaining == 0 or (not moves and not legal_moves_for(snapshot, other)):
            leaves += 1
            return evaluate_position(snapshot, color, legal_moves_for), None
        if not moves:
            passes += 1
            value, _ = _visit(snapshot, other, remaining, alpha, beta)
            return value, None
        # rootを並べ替えない。後続子のfail-low同値では先行の厳密最善手を保つ。
        if ordered and known_moves is None:
            sequence = sorted(
                moves,
                key=lambda m: (m not in _CORNERS, -POSITION_WEIGHTS[m[1]][m[0]], m[1], m[0]),
            )
        else:
            sequence = sorted(moves, key=lambda m: (m[1], m[0]))
        maximizing = turn == color
        best = float('-inf') if maximizing else float('inf')
        selected = None
        for index, move in enumerate(sequence):
            value, _ = _visit(apply_move(snapshot, turn, move), other, remaining - 1, alpha, beta)
            if (maximizing and value > best) or (not maximizing and value < best):
                best, selected = value, move
            if maximizing:
                alpha = max(alpha, best)
            else:
                beta = min(beta, best)
            if alpha >= beta and index + 1 < len(sequence):
                cutoffs += 1
                break
        return best, selected

    value, move = _visit(rows, color, depth, float('-inf'), float('inf'), root_moves)
    return AlphaBetaResult(move, value, depth, nodes, leaves, passes, cutoffs)


def search_alpha_beta(
    rows: BoardRows,
    color: str,
    depth: int,
    legal_moves_for: LegalMovesFor,
    apply_move: ApplyMove,
    *,
    ordered: bool = True,
) -> AlphaBetaResult:
    """深度0〜4で厳密なroot値と従来同点順の最善手を返す。

    パスはplacement深度を消費しない。orderedは内部だけの単純順序。
    入力・乱数・実Boardは変更せず、callback例外は伝播する。
    """
    _validate_search_depth(depth, 0)
    if type(ordered) is not bool:
        raise ValueError('invalid_search_order')
    validate_position(rows, color)
    return _run_alpha_beta(
        rows, color, depth, legal_moves_for, apply_move, legal_moves_for(rows, color), ordered
    )


def make_alpha_beta_selector(
    legal_moves_for: LegalMovesFor,
    apply_move: ApplyMove,
    *,
    depth: int = DEFAULT_SEARCH_DEPTH,
) -> MoveSelector:
    """固定深度1〜4の決定的な内部順序付きselectorを作る。"""
    _validate_search_depth(depth, 1)

    def _select_alpha_beta(rows: BoardRows, color: str, legal_moves: LegalMoves) -> Move | None:
        """完全な候補集合から選択し、実盤面の確定は本体へ委ねる。"""
        if not legal_moves:
            return None
        validate_position(rows, color)
        return _run_alpha_beta(
            rows, color, depth, legal_moves_for, apply_move, legal_moves, True
        ).move

    return _select_alpha_beta
