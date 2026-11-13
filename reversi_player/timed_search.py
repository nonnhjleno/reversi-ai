"""実経過時間で反復深化を協調的に中断する。固定深度の参照探索は変更しない。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import TYPE_CHECKING, Callable, cast

if TYPE_CHECKING:
    from typing import TypeAlias

    from reversi_player import ai_engine
elif __package__:
    from . import ai_engine
else:
    import ai_engine

Clock: TypeAlias = Callable[[], int]
_CORNERS = ((0, 0), (7, 0), (0, 7), (7, 7))
DEFAULT_TIMED_DEPTH = 4
MAX_TIMED_DEPTH = 5
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


def load_policy() -> ResponsePolicy:
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
    return ResponsePolicy(
        *(
            cast(int, values[k])
            for k in ('target_ns', 'new_depth_margin_ns', 'stop_margin_ns', 'max_depth')
        )
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

    def _legal(snapshot: ai_engine.BoardRows, turn: str) -> ai_engine.LegalMoves:
        """合法手生成の前後で停止を確認する。callback例外は伝播する。"""
        _check()
        moves = _moves(legal_moves_for(snapshot, turn))
        _check()
        return moves

    def _visit(
        snapshot: ai_engine.BoardRows,
        turn: str,
        remaining: int,
        alpha: float,
        beta: float,
        known: ai_engine.LegalMoves | None = None,
    ) -> tuple[float, ai_engine.Move | None]:
        """root順と厳密改善を保つAlpha-Beta。実呼出しを中断前に計数する。"""
        nonlocal nodes, leaves, passes, cutoffs
        nodes += 1
        _check()
        moves = _legal(snapshot, turn) if known is None else known
        other = 'W' if turn == 'B' else 'B'
        if remaining == 0 or (not moves and not _legal(snapshot, other)):
            _check()
            value = ai_engine.evaluate_position(snapshot, color, _legal)
            leaves += 1
            _check()
            return value, None
        if not moves:
            _check()
            passes += 1
            value, _ = _visit(snapshot, other, remaining, alpha, beta)
            _check()
            return value, None
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
        _check()
        maximizing = turn == color
        best = float('-inf') if maximizing else float('inf')
        selected = None
        for index, move in enumerate(sequence):
            _check()
            child = apply_move(snapshot, turn, move)
            ai_engine.validate_position(child, turn)
            _check()
            value, _ = _visit(child, other, remaining - 1, alpha, beta)
            _check()
            if (maximizing and value > best) or (not maximizing and value < best):
                best, selected = value, move
            if maximizing:
                alpha = max(alpha, best)
            else:
                beta = min(beta, best)
            if alpha >= beta and index + 1 < len(sequence):
                cutoffs += 1
                break
        _check()
        return best, selected

    if root_moves:
        for depth in range(1, policy.max_depth + 1):
            try:
                if _check() >= start_deadline:
                    reason = 'new_depth_deadline'
                    break
                started = depth
                value, move = _visit(rows, color, depth, float('-inf'), float('inf'), root_moves)
            except _SearchInterrupted:
                reason = 'time_limit'
                break
            # 部分root値やfail-low同値で完了結果を書き換えない。
            chosen, exact_value, completed = move, value, depth
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
    ) -> None:
        """純粋callbackと設定を保持し、呼出しごとの結果は置き換える。"""
        self._legal, self._apply, self._policy = legal_moves_for, apply_move, policy
        self._limit_ns, self._ordered = limit_ns, ordered
        self._received: int | None = None
        self.result: TimedResult | None = None

    def begin(self, received_ns: int) -> None:
        """受付ごとに前コマンドの結果と起点を破棄する。"""
        self._received, self.result = received_ns, None

    def select(
        self, rows: ai_engine.BoardRows, color: str, moves: ai_engine.LegalMoves
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
            )
            return self.result.move
        finally:
            self._received = None
