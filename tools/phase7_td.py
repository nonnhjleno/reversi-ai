"""Rangerの黒視点オンラインTD(0)と学習専用の1手方策。"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from harness import referee
from reversi_player import ai_engine, ntuple, reversi

PASS = (-1, -1)
_INITIAL = (
    'EEEEEEEE',
    'EEEEEEEE',
    'EEEEEEEE',
    'EEEWBEEE',
    'EEEBWEEE',
    'EEEEEEEE',
    'EEEEEEEE',
    'EEEEEEEE',
)
_FEATURE_MODEL = ntuple.NtupleModel.zeros()
Features = tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class State:
    """学習時点の盤面と次に行動する手番。"""

    rows: ai_engine.BoardRows
    turn: str

    @property
    def opponent(self) -> str:
        """反対の手番を返す。"""
        return 'W' if self.turn == 'B' else 'B'


INITIAL = State(_INITIAL, 'B')


@dataclass(frozen=True)
class Config:
    """一定の学習率・探索率・専用乱数seed。割引率は1。"""

    rate: float
    epsilon: float
    seed: int

    def __post_init__(self) -> None:
        """不正値を実行開始前に拒む。"""
        if type(self.rate) not in (int, float) or not 0.0 <= self.rate <= 1.0:
            raise ValueError('invalid_td_rate')
        if type(self.epsilon) not in (int, float) or not 0.0 <= self.epsilon <= 1.0:
            raise ValueError('invalid_td_epsilon')
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError('invalid_td_seed')


@lru_cache(maxsize=4096)
def features(state: State) -> Features:
    """Phase 6の同じ盤面・手番の特徴だけをキャッシュする。"""
    return _FEATURE_MODEL.features(state.rows, state.turn)


def value(weights: list[float], counts: Features) -> float:
    """Phase 6と同じ回数だけ重みを加算し黒視点の値を求める。"""
    return math.tanh(math.fsum(weights[index] for index, count in counts for _ in range(count)))


@dataclass(frozen=True)
class Update:
    """1遷移の更新前の値、目標、最大パラメータ変化。"""

    prediction: float
    target: float
    delta: float
    max_change: float


def update(
    weights: list[float],
    state: State,
    following: State,
    terminal_reward: int | None,
    config: Config,
) -> Update:
    """検証済みの1遷移について現在状態だけを半勾配で更新する。"""
    counts = features(state)
    prediction = value(weights, counts)
    if terminal_reward is None:
        target = value(weights, features(following))
    elif type(terminal_reward) is int and terminal_reward in (-1, 0, 1):
        target = float(terminal_reward)
    else:
        raise ValueError('invalid_td_terminal_reward')
    delta = target - prediction
    step = config.rate * delta * (1.0 - prediction * prediction)
    max_change = 0.0
    if step:
        for index, count in counts:
            old = weights[index]
            new = old + step * count
            if not -ntuple.MAX_WEIGHT <= new <= ntuple.MAX_WEIGHT:
                raise ValueError('invalid_td_weight')
            max_change = max(max_change, abs(new - old))
            weights[index] = new
    return Update(prediction, target, delta, max_change)


def _margin(rows: ai_engine.BoardRows) -> int:
    """黒から見た盤上の石差を返す。"""
    return sum(row.count('B') - row.count('W') for row in rows)


def _terminal(state: State) -> bool:
    """合法手の有無を作業用Boardで調べる。"""
    board = reversi._board_from_rows(state.rows, state.turn)
    return not (board._has_legal_move(state.turn) or board._has_legal_move(state.opponent))


def _child(state: State, move: ai_engine.Move) -> State:
    """着手の反転を含む次状態を作る。"""
    return State(reversi.apply_move(state.rows, state.turn, move), state.opponent)


def _selection_score(weights: list[float], child: State, terminal: bool) -> float:
    """Phase 6の選択契約に従う。終局時の比較用の値はTD目標に渡さない。"""
    if not terminal:
        return value(weights, features(child))
    margin = _margin(child.rows)
    if margin:
        return math.copysign(2.0, margin) + margin / 64.0
    return 0.0


def choose(
    weights: list[float],
    state: State,
    moves: ai_engine.LegalMoves,
    config: Config,
    rng: random.Random,
) -> tuple[ai_engine.Move, State, bool]:
    """行・列順の合法手から1手選ぶ。強制パスと終局は呼出側で扱う。"""
    if not moves:
        raise ValueError('td_policy_requires_placement')
    if config.epsilon and rng.random() < config.epsilon:
        move = rng.choice(moves)
        next_state = _child(state, move)
        return move, next_state, _terminal(next_state)
    direction = 1 if state.turn == 'B' else -1
    best: tuple[ai_engine.Move, State, bool] | None = None
    best_score = -math.inf
    for move in moves:
        next_state = _child(state, move)
        terminal = _terminal(next_state)
        score = direction * _selection_score(weights, next_state, terminal)
        if score > best_score:
            best = move, next_state, terminal
            best_score = score
    if best is None:
        raise ValueError('invalid_td_policy_value')
    return best


@dataclass
class Diagnostics:
    """遷移ごとの誤差・価値・変化量を固定区間で集計する。"""

    observations: int = 0
    delta_sum: float = 0.0
    abs_delta_sum: float = 0.0
    max_abs_delta: float = 0.0
    max_change: float = 0.0
    saturated: int = 0
    delta_bins: Counter[str] = field(default_factory=Counter)
    value_bins: Counter[str] = field(default_factory=Counter)
    change_bins: Counter[str] = field(default_factory=Counter)

    def add(self, item: Update, turn: str) -> None:
        """手番ごとに、誤差・値・最大重み変化のbinを加える。"""
        self.observations += 1
        self.delta_sum += item.delta
        self.abs_delta_sum += abs(item.delta)
        self.max_abs_delta = max(self.max_abs_delta, abs(item.delta))
        self.max_change = max(self.max_change, item.max_change)
        self.saturated += abs(item.prediction) >= 0.99
        self.delta_bins[f'{turn}:{min(15, int((item.delta + 2.0) * 4))}'] += 1
        self.value_bins[f'{turn}:{min(15, int((item.prediction + 1.0) * 8))}'] += 1
        band = 'zero' if not item.max_change else str(math.floor(math.log10(item.max_change)))
        self.change_bins[f'{turn}:{band}'] += 1


@dataclass(frozen=True)
class Game:
    """独立refereeと一致した完局の棋譜と結果。"""

    moves: tuple[ai_engine.Move, ...]
    margin: int
    passes: int


def play(
    weights: list[float],
    config: Config,
    rng: random.Random,
    diagnostics: Diagnostics,
    *,
    initial: State = INITIAL,
) -> Game:
    """合法な1局に対して逐次更新し、全着手と終局を独立refereeと照合する。"""
    state = initial
    moves: list[ai_engine.Move] = []
    passes = 0
    if _terminal(state):
        raise ValueError('td_terminal_start')
    while True:
        legal = reversi.legal_moves_for(state.rows, state.turn)
        if legal:
            move, following, terminal = choose(weights, state, legal, config, rng)
            if referee.apply_move(state.rows, state.turn, move) != following.rows:
                raise ValueError('td_flip_mismatch')
        else:
            if referee.legal_moves(state.rows, state.turn) or not referee.legal_moves(
                state.rows, state.opponent
            ):
                raise ValueError('td_illegal_pass')
            move = PASS
            following = State(state.rows, state.opponent)
            terminal = False
            passes += 1
        margin = _margin(following.rows) if terminal else 0
        reward = (int(margin > 0) - int(margin < 0)) if terminal else None
        # 審判は各着手の合法性と反転、全パス、最終盤面を照合する。
        if reward is not None and referee.result(following.rows) != ('W', 'D', 'B')[reward + 1]:
            raise ValueError('td_result_mismatch')
        item = update(weights, state, following, reward, config)
        diagnostics.add(item, state.turn)
        moves.append(move)
        if terminal:
            return Game(tuple(moves), margin, passes)
        state = following
