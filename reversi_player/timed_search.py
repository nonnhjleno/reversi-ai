"""実経過時間で反復深化を協調的に中断する。固定深度の参照探索は変更しない。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import time
from typing import TYPE_CHECKING, Callable, cast

if TYPE_CHECKING:
    from typing import TypeAlias

    from reversi_player import ai_engine
    from reversi_player.bitboard import BitPosition

    BitboardEvaluator: TypeAlias = Callable[[BitPosition, str], float]
    _Snapshot: TypeAlias = ai_engine.BoardRows | BitPosition
    _PrincipalLine: TypeAlias = tuple[ai_engine.Move | None, ...]
elif __package__:
    from . import ai_engine
else:
    import ai_engine

Clock: TypeAlias = Callable[[], int]
_CORNERS = ((0, 0), (7, 0), (0, 7), (7, 7))
DEFAULT_TIMED_DEPTH = 4
MAX_TIMED_DEPTH = 6
SEARCH_DEPTH_ENV = 'REVERSI_MAX_DEPTH'
MOVE_ORDERING_ENV = 'REVERSI_MOVE_ORDERING'
BOARD_BACKEND_ENV = 'REVERSI_BOARD_BACKEND'
MIN_TIME_BUDGET_NS = 1_000_000
MAX_TIME_BUDGET_NS = 6_500_000_000


@dataclass(frozen=True)
class ResponsePolicy:
    """受付からの単一期限と予約分。実測前の値は安全保証ではない。"""

    target_ns: int = 7_000_000_000
    new_depth_margin_ns: int = 1_000_000_000
    stop_margin_ns: int = 500_000_000
    max_depth: int = DEFAULT_TIMED_DEPTH

    def __post_init__(self) -> None:
        """通常intだけを許し、開始限界・途中停止・外部期限の順序を検査する。"""
        if any(
            type(v) is not int
            for v in (self.target_ns, self.new_depth_margin_ns, self.stop_margin_ns, self.max_depth)
        ) or not (
            0 < self.stop_margin_ns < self.new_depth_margin_ns < self.target_ns < 10_000_000_000
            and 1 <= self.max_depth <= MAX_TIMED_DEPTH
        ):
            raise ValueError('invalid_response_policy')


def load_policy(*, max_depth: int | None = None) -> ResponsePolicy:
    """scriptと同じディレクトリの提出設定を読む。欠損や未知項目は伝播・拒否する。"""
    raw: object = json.loads(
        Path(__file__).with_name('response_config.json').read_text(encoding='utf-8')
    )
    keys = {'target_ns', 'new_depth_margin_ns', 'stop_margin_ns', 'max_depth'}
    if not isinstance(raw, dict) or set(raw) != keys:
        raise ValueError('invalid_response_config')
    values = cast('dict[str, object]', raw)
    if any(type(v) is not int for v in values.values()):
        raise ValueError('invalid_response_config')
    policy = ResponsePolicy(**cast('dict[str, int]', values))
    if max_depth is None:
        return policy
    return ResponsePolicy(
        policy.target_ns, policy.new_depth_margin_ns, policy.stop_margin_ns, max_depth
    )


@dataclass(frozen=True)
class TimedResult:
    """完了反復だけの値と着手、途中中断分も含む実作業量を保持する。"""

    move: ai_engine.Move | None
    value: float | None
    completed_depth: int
    max_depth_started: int
    nodes: int
    leaves: int
    passes: int
    cutoffs: int
    stop_reason: str
    stop_deadline_ns: int
    stop_requested_ns: int | None


@dataclass(frozen=True)
class Iteration:
    """実際に開始した反復の経過時間と追加作業量。"""

    depth: int
    completed: bool
    elapsed_ns: int
    nodes: int
    leaves: int
    value: float | None
    move: ai_engine.Move | None


@dataclass
class OrderingDiagnostics:
    """最大深度の診断で、着手順の費用と主変化の利用を実測する。"""

    ordering_ns: int = 0
    first_cutoffs: int = 0
    pv_candidates: int = 0
    pv_legal: int = 0
    pv_promoted: int = 0
    pv_exact_nodes: int = 0
    pv_exact_best: int = 0
    completed_lines: list[_PrincipalLine] = field(default_factory=list)


def validate_move_ordering(value: object, ordered: bool, tt_capacity: int) -> str:
    """従来順と、内部で完了反復の主変化を使う排他的な実験設定を受け付ける。"""
    if not isinstance(value, str) or value not in ('static', 'pv'):
        raise ValueError('invalid_move_ordering')
    if value == 'pv' and (not ordered or tt_capacity):
        raise ValueError('pv_requires_static_order_and_disabled_tt')
    return value


def validate_board_backend(
    value: object, ordered: bool, move_ordering: str, tt_capacity: int
) -> str:
    """盤面方式を検証し、Bitboardに他の探索実験を混ぜる設定を拒否する。"""
    if not isinstance(value, str) or value not in ('array', 'bitboard'):
        raise ValueError('invalid_board_backend')
    if value == 'bitboard' and (not ordered or move_ordering != 'static' or tt_capacity):
        raise ValueError('bitboard_requires_static_order_and_disabled_tt')
    return value


class _SearchInterrupted(Exception):
    """時間確認だけが発生させる内部制御。通常例外の代用にしない。"""


def _moves(value: ai_engine.LegalMoves) -> ai_engine.LegalMoves:
    """callbackの候補形状を検査する。完全性はstatelessルールの契約である。"""
    if (
        type(value) is not tuple
        or any(
            type(m) is not tuple
            or len(m) != 2
            or any(type(n) is not int or not 0 <= n < 8 for n in m)
            for m in value
        )
        or len(set(value)) != len(value)
    ):
        raise ValueError('invalid_legal_moves')
    return value


def search_iterative(
    rows: ai_engine.BoardRows,
    color: str,
    root_moves: ai_engine.LegalMoves,
    legal_moves_for: ai_engine.LegalMovesFor,
    apply_move: ai_engine.ApplyMove,
    *,
    command_received_ns: int,
    policy: ResponsePolicy,
    limit_ns: int | None = None,
    clock: Clock | None = None,
    ordered: bool = True,
    leaf_evaluator: ai_engine.LeafEvaluator | None = None,
    tt_capacity: int = 0,
    tt_stats: ai_engine.TTDiagnostics | None = None,
    iterations: list[Iteration] | None = None,
    move_ordering: str = 'static',
    ordering_stats: OrderingDiagnostics | None = None,
    board_backend: str = 'array',
    bitboard_evaluator: BitboardEvaluator | None = None,
) -> TimedResult:
    """全反復で期限を共有し、完全rootが戻った深度だけを採用する。

    root候補は本体の完全な列挙。rootパス・終局は探索なし。途中で止まった
    深度の値は公開せず、深度1未完了なら最初の(y,x)合法手とvalue=None。
    callback/時計/入力の例外は伝播し、実盤面・乱数・入出力は変更しない。
    """
    ai_engine.validate_position(rows, color)
    _moves(root_moves)
    if (
        not isinstance(policy, ResponsePolicy)
        or not callable(legal_moves_for)
        or not callable(apply_move)
    ):
        raise ValueError('invalid_search_callback_or_policy')
    if type(command_received_ns) is not int or command_received_ns < 0:
        raise ValueError('invalid_command_clock')
    if limit_ns is not None and (
        type(limit_ns) is not int or not MIN_TIME_BUDGET_NS <= limit_ns <= MAX_TIME_BUDGET_NS
    ):
        raise ValueError('unsupported_time_budget')
    if type(ordered) is not bool or (clock is not None and not callable(clock)):
        raise ValueError('invalid_search_order_or_clock')
    now = clock if clock is not None else time.perf_counter_ns
    previous = now()
    if type(previous) is not int or previous < command_received_ns:
        raise ValueError('invalid_search_clock')
    stop_deadline = command_received_ns + policy.target_ns - policy.stop_margin_ns
    start_deadline = command_received_ns + policy.target_ns - policy.new_depth_margin_ns
    if limit_ns is not None:
        # 実測した停止遅れの余裕を確保する。要求上限自体は緩和しない。
        reserve = max(100_000, min(120_000_000, limit_ns * 4 // 5), limit_ns // 20)
        time_stop = previous + limit_ns - reserve
        stop_deadline = min(stop_deadline, time_stop)
        start_deadline = min(
            start_deadline,
            previous + limit_ns * 8 // 10,
            time_stop - min(1_000_000, limit_ns // 20),
        )
    nodes = leaves = passes = cutoffs = started = completed = 0
    stop_requested: int | None = None
    chosen = min(root_moves, key=lambda m: (m[1], m[0])) if root_moves else None
    exact_value: float | None = None
    reason = 'max_depth_completed' if root_moves else 'no_legal_move'
    capacity = ai_engine.validate_tt_capacity(tt_capacity)
    table: ai_engine._TranspositionTable | None = None
    mode = validate_move_ordering(move_ordering, ordered, capacity)
    backend = validate_board_backend(board_backend, ordered, mode, capacity)
    if bitboard_evaluator is not None and (
        backend != 'bitboard' or not callable(bitboard_evaluator)
    ):
        raise ValueError('direct_evaluation_requires_bitboard')
    root_snapshot: _Snapshot = rows
    if backend == 'bitboard' and root_moves:
        # 既存の配布物もarrayを使用できるよう、依存の読込は明示設定時に限る。
        if TYPE_CHECKING or __package__:
            from reversi_player.bitboard import BitPosition
        else:
            from bitboard import BitPosition

        root_snapshot = BitPosition.from_rows(rows)
    # パスは連続しない。placement深度の2倍で実際の再帰段数を保持できる。
    lines: list[_PrincipalLine] | None = [()] * (2 * policy.max_depth + 1) if mode == 'pv' else None
    prior: _PrincipalLine = ()

    def _check() -> int:
        """通常int・単調性を検査し、同じ停止時刻だけを全再帰で使う。"""
        nonlocal previous, stop_requested
        current = now()
        if type(current) is not int or current < previous:
            raise ValueError('invalid_search_clock')
        previous = current
        if current >= stop_deadline:
            stop_requested = current
            raise _SearchInterrupted()
        return current

    def _legal(snapshot: _Snapshot, turn: str) -> ai_engine.LegalMoves:
        """合法手生成の前後で停止を確認する。callback例外は伝播する。"""
        _check()
        moves = (
            _moves(legal_moves_for(snapshot, turn))
            if isinstance(snapshot, tuple)
            else snapshot.legal_moves(turn)
        )
        _check()
        return moves

    def _visit(
        snapshot: _Snapshot,
        turn: str,
        remaining: int,
        alpha: float,
        beta: float,
        known: ai_engine.LegalMoves | None = None,
        ply: int = 0,
        hint: _PrincipalLine = (),
    ) -> tuple[float, ai_engine.Move | None]:
        """root順と厳密改善を保つAlpha-Beta。実呼出しを中断前に計数する。"""
        nonlocal nodes, leaves, passes, cutoffs
        nodes += 1
        _check()
        if lines is not None:
            lines[ply] = ()
        key = None
        index_slot = 0
        window = None
        if table is not None and known is None and isinstance(snapshot, tuple):
            key = (snapshot, turn)
            index_slot = table.index(key)
            cached = table.probe(index_slot, key, remaining, alpha, beta)
            _check()
            if cached is not None:
                return cached, None
            window = (alpha, beta)
        moves = _legal(snapshot, turn) if known is None else known
        other = 'W' if turn == 'B' else 'B'
        if remaining == 0 or (not moves and not _legal(snapshot, other)):
            _check()
            if bitboard_evaluator is not None and not isinstance(snapshot, tuple):
                if not moves and not _legal(snapshot, other):
                    value = ai_engine._terminal_score(snapshot.disc_difference(color))
                else:
                    black_value = bitboard_evaluator(snapshot, turn)
                    if not -1.0 <= black_value <= 1.0:
                        raise ValueError('invalid_leaf_value')
                    value = black_value if color == 'B' else -black_value
            else:
                leaf_legal: ai_engine.LegalMovesFor = _legal
                if isinstance(snapshot, tuple):
                    evaluation_rows = snapshot
                else:
                    evaluation_rows = snapshot.to_rows()

                    def _legal_at_leaf(
                        _rows: ai_engine.BoardRows, leaf_turn: str
                    ) -> ai_engine.LegalMoves:
                        """この葉のBitboardから合法手を返す。評価は従来の行を読む。"""
                        return _legal(snapshot, leaf_turn)

                    leaf_legal = _legal_at_leaf

                value = (
                    ai_engine.evaluate_position(evaluation_rows, color, leaf_legal)
                    if leaf_evaluator is None
                    else ai_engine._evaluate_leaf(
                        evaluation_rows, turn, color, moves, other, leaf_legal, leaf_evaluator
                    )
                )
            leaves += 1
            _check()
            if table is not None and key is not None:
                table.store(index_slot, key, remaining, value, None)
                _check()
            return value, None
        if not moves:
            _check()
            passes += 1
            pass_hint = hint[1:] if hint and hint[0] is None else ()
            value, _ = _visit(snapshot, other, remaining, alpha, beta, ply=ply + 1, hint=pass_hint)
            if lines is not None:
                lines[ply] = (None,) + lines[ply + 1]
            _check()
            if table is not None and key is not None:
                table.store(index_slot, key, remaining, value, window)
                _check()
            return value, None
        order_started = time.perf_counter_ns() if ordering_stats is not None else 0
        sequence = sorted(
            moves,
            key=(
                (lambda m: (m[1], m[0]))
                if known is not None or not ordered
                else (
                    lambda m: (
                        m not in _CORNERS,
                        -ai_engine.POSITION_WEIGHTS[m[1]][m[0]],
                        m[1],
                        m[0],
                    )
                )
            ),
        )
        # 根は行・列順を維持する。旧主変化と一致する内部の合法手だけを優先する。
        preferred = hint[0] if hint else None
        position = -1
        if known is None and preferred is not None and preferred in sequence:
            position = sequence.index(preferred)
            if position:
                sequence.insert(0, sequence.pop(position))
        if ordering_stats is not None:
            ordering_stats.ordering_ns += time.perf_counter_ns() - order_started
            ordering_stats.pv_candidates += known is None and preferred is not None
            ordering_stats.pv_legal += position >= 0
            ordering_stats.pv_promoted += position > 0
        _check()
        entry_alpha, entry_beta = alpha, beta
        maximizing = turn == color
        best = float('-inf') if maximizing else float('inf')
        selected = None
        for index, move in enumerate(sequence):
            _check()
            child: _Snapshot
            if isinstance(snapshot, tuple):
                child = apply_move(snapshot, turn, move)
                ai_engine.validate_position(child, turn)
            else:
                child = snapshot.play(turn, move)
            _check()
            child_hint = hint[1:] if move == preferred else ()
            value, _ = _visit(
                child, other, remaining - 1, alpha, beta, ply=ply + 1, hint=child_hint
            )
            _check()
            if (maximizing and value > best) or (not maximizing and value < best):
                best, selected = value, move
                if lines is not None:
                    lines[ply] = (move,) + lines[ply + 1]
            if maximizing:
                alpha = max(alpha, best)
            else:
                beta = min(beta, best)
            if alpha >= beta and index + 1 < len(sequence):
                cutoffs += 1
                if ordering_stats is not None:
                    ordering_stats.first_cutoffs += index == 0
                break
        if ordering_stats is not None and position >= 0 and entry_alpha < best < entry_beta:
            ordering_stats.pv_exact_nodes += 1
            ordering_stats.pv_exact_best += selected == preferred
        _check()
        if table is not None and key is not None:
            table.store(index_slot, key, remaining, best, window)
            _check()
        return best, selected

    if root_moves:
        for depth in range(1, policy.max_depth + 1):
            before_nodes, before_leaves = nodes, leaves
            try:
                if _check() >= start_deadline:
                    reason = 'new_depth_deadline'
                    break
                if capacity and table is None:
                    table = ai_engine._TranspositionTable(capacity, tt_stats)
                    _check()
                iteration_start = previous
                started = depth
                value, move = _visit(
                    root_snapshot, color, depth, float('-inf'), float('inf'), root_moves, hint=prior
                )
            except _SearchInterrupted:
                reason = 'time_limit'
            else:
                # 部分root値やfail-low同値で完了結果を書き換えない。
                chosen, exact_value, completed = move, value, depth
                if lines is not None:
                    prior = lines[0]
                    if ordering_stats is not None:
                        ordering_stats.completed_lines.append(prior)
            if iterations is not None and started == depth:
                settled = completed == depth
                iterations.append(
                    Iteration(
                        depth,
                        settled,
                        previous - iteration_start,
                        nodes - before_nodes,
                        leaves - before_leaves,
                        exact_value if settled else None,
                        chosen if settled else None,
                    )
                )
            if reason == 'time_limit':
                break
    return TimedResult(
        chosen,
        exact_value,
        completed,
        started,
        nodes,
        leaves,
        passes,
        cutoffs,
        reason,
        stop_deadline,
        stop_requested,
    )


class TimedSelector:
    """コマンド受付時刻だけを受け渡すselector。実Boardを所有しない。"""

    def __init__(
        self,
        legal_moves_for: ai_engine.LegalMovesFor,
        apply_move: ai_engine.ApplyMove,
        policy: ResponsePolicy,
        *,
        limit_ns: int | None = None,
        ordered: bool = True,
        leaf_evaluator: ai_engine.LeafEvaluator | None = None,
        tt_capacity: int = 0,
        move_ordering: str = 'static',
        board_backend: str = 'array',
        bitboard_evaluator: BitboardEvaluator | None = None,
    ) -> None:
        """純粋callbackと設定を保持し、呼出しごとの結果は置き換える。"""
        self._legal, self._apply, self._policy = legal_moves_for, apply_move, policy
        self._limit_ns, self._ordered = limit_ns, ordered
        self._leaf_evaluator = leaf_evaluator
        self._bitboard_evaluator = bitboard_evaluator
        self._tt_capacity = ai_engine.validate_tt_capacity(tt_capacity)
        self._move_ordering = validate_move_ordering(move_ordering, ordered, self._tt_capacity)
        self._board_backend = validate_board_backend(
            board_backend, ordered, self._move_ordering, self._tt_capacity
        )
        self._received: int | None = None
        self.result: TimedResult | None = None

    def begin(self, received_ns: int) -> None:
        """受付ごとに前コマンドの結果と起点を破棄する。"""
        self._received, self.result = received_ns, None

    def select(
        self,
        rows: ai_engine.BoardRows,
        color: str,
        moves: ai_engine.LegalMoves,
        *,
        tt_stats: ai_engine.TTDiagnostics | None = None,
        iterations: list[Iteration] | None = None,
    ) -> ai_engine.Move | None:
        """単一期限で探索する。例外でも前回起点を次回へ持ち越さない。"""
        self.result = None
        received = time.perf_counter_ns() if self._received is None else self._received
        try:
            self.result = search_iterative(
                rows,
                color,
                moves,
                self._legal,
                self._apply,
                command_received_ns=received,
                policy=self._policy,
                limit_ns=self._limit_ns,
                ordered=self._ordered,
                leaf_evaluator=self._leaf_evaluator,
                tt_capacity=self._tt_capacity,
                tt_stats=tt_stats,
                iterations=iterations,
                move_ordering=self._move_ordering,
                board_backend=self._board_backend,
                bitboard_evaluator=self._bitboard_evaluator,
            )
            return self.result.move
        finally:
            self._received = None
