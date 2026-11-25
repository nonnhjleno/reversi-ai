"""置換表を独立探索、制御時計、実プロトコルへ照合する。"""

import pytest

from harness import protocol, referee
from reversi_player import ai_engine, ntuple, reversi, timed_search, web
from test_ai_phase4 import _comparison_reference, _context
from test_ai_phase3 import CASES
from test_ai_phase8 import _oracle as black_oracle


def _legal(rows, turn):
    return tuple(referee.legal_moves(rows, turn))


@pytest.mark.parametrize('capacity', [-1, True, 3, 32768, 4.0, '4096'])
def test_invalid_capacity(capacity):
    with pytest.raises(ValueError, match='invalid_tt_capacity'):
        ai_engine.validate_tt_capacity(capacity)


@pytest.mark.parametrize(
    'store_window,probe_window,accepted,field',
    [
        (None, (10, 11), True, 'exact'),
        ((0, 2), (0, 2), True, 'lower'),
        ((0, 2), (0, 4), False, 'lower'),
        ((2, 4), (2, 4), True, 'upper'),
        ((2, 4), (1, 4), False, 'upper'),
        ((1, 3), (3, 4), True, 'exact'),
    ],
)
def test_bound_lookup(store_window, probe_window, accepted, field):
    key = (referee.INITIAL_ROWS, 'B')
    stats = ai_engine.TTDiagnostics()
    tt = ai_engine._TranspositionTable(1, stats)
    slot = tt.index(key)
    assert tt.probe(slot, key, 2, 1, 3) is None
    tt.store(slot, key, 2, 2, store_window)
    assert tt.probe(slot, key, 2, *probe_window) == (2 if accepted else None)
    assert stats.state_hits == stats.depth_hits == 1
    assert getattr(stats, field) == accepted
    assert tt.probe(slot, key, 1, *probe_window) is None
    assert stats.depth_misses == 1


def test_forced_collision_and_replacement():
    rows = referee.INITIAL_ROWS
    black, white = (rows, 'B'), (rows, 'W')
    other = (referee.apply_move(rows, 'B', (3, 2)), 'B')
    tt = ai_engine._TranspositionTable(1, None)
    assert tt.index(black) == tt.index(white) == tt.index(other) == 0
    tt.store(0, black, 2, 3, None)
    tt.store(0, black, 2, 99, (0, 1))
    tt.store(0, black, 1, -77, None)
    assert tt.probe(0, black, 2, -100, 100) == 3
    assert tt.probe(0, white, 2, -100, 100) is None
    assert tt.probe(0, other, 2, -100, 100) is None
    tt.store(0, white, 1, -8, None)
    assert tt.probe(0, white, 1, -100, 100) == -8
    assert tt.probe(0, black, 2, -100, 100) is None
    assert (tt.stats.capacity, tt.stats.peak_entries, tt.stats.stores) == (1, 1, 2)
    assert (tt.stats.collisions, tt.stats.replacements) == (3, 1)
    assert rows == referee.INITIAL_ROWS


@pytest.mark.parametrize('label', CASES)
@pytest.mark.parametrize('turn', ['B', 'W'])
def test_fixed_search_matches_independent_fraction_oracle(label, turn):
    rows = tuple(CASES[label]['rows'])
    for depth in range(1, 5):
        (value, best, _), _ = _comparison_reference(rows, turn, depth)
        expected = (best[0] if best else None, float(value))
        direct = ai_engine.search_alpha_beta(rows, turn, depth, _legal, referee.apply_move)
        for slots in (1, 4096):
            stats = ai_engine.TTDiagnostics()
            cached = ai_engine.search_alpha_beta(
                rows, turn, depth, _legal, referee.apply_move, tt_capacity=slots, tt_stats=stats
            )
            assert (cached.move, cached.value) == (direct.move, direct.value) == expected
            assert stats.peak_entries <= slots
            assert stats.probes <= cached.nodes - 1
        assert rows == tuple(CASES[label]['rows'])


@pytest.mark.parametrize('label', ['initial', 'late', 'pass'])
@pytest.mark.parametrize('turn', ['B', 'W'])
def test_ntuple_timed_value_matches_black_oracle(label, turn):
    rows = tuple(CASES[label]['rows'])
    model = ntuple.NtupleModel(
        tuple((index % 7 - 3) / 2000 for index in range(ntuple.WEIGHT_COUNT))
    )
    moves = _legal(rows, turn)
    value, move = black_oracle(rows, turn, 4, model.value)
    stats = ai_engine.TTDiagnostics()
    result = timed_search.search_iterative(
        rows,
        turn,
        moves,
        _legal,
        referee.apply_move,
        command_received_ns=0,
        policy=timed_search.ResponsePolicy(),
        clock=lambda: 0,
        leaf_evaluator=model.value,
        tt_capacity=4096,
        tt_stats=stats,
    )
    if not moves:
        assert result.move is None and result.completed_depth == stats.probes == 0
    else:
        assert (result.move, result.value, result.completed_depth) == (
            move,
            value if turn == 'B' else -value,
            4,
        )
        assert stats.depth_hits + stats.depth_misses == stats.state_hits


def test_transposition_and_root_tie_on_known_tree():
    states = [
        (f'{i:08b}'.replace('0', 'E').replace('1', 'B'),) + ('EEEEEEEE',) * 7 for i in range(8)
    ]
    a, b = (0, 0), (1, 0)
    graph = {
        0: {a: 1, b: 2},
        1: {a: 3, b: 4},
        2: {a: 3, b: 5},
        3: {a: 6},
        4: {a: 6},
        5: {a: 7},
        6: {a: 7},
    }
    scores = {3: 0.3, 4: 0.8, 5: -0.1}

    def moves(rows, turn):
        return tuple(graph.get(states.index(rows), {}))

    def apply(rows, turn, move):
        return states[graph[states.index(rows)][move]]

    def evaluate(rows, turn):
        return scores[states.index(rows)]

    stats = ai_engine.TTDiagnostics()
    answer = ai_engine.search_alpha_beta(
        states[0],
        'B',
        2,
        moves,
        apply,
        leaf_evaluator=evaluate,
        tt_capacity=4096,
        tt_stats=stats,
    )
    # 両子の最小値は min(.3,.8) と min(.3,-.1)。後者は .3 で枝刈り可能。
    assert (answer.move, answer.value) == (a, 0.3)
    assert stats.exact == 1 and stats.depth_hits == 1
    assert answer.nodes == 6 and answer.leaves == 2


@pytest.mark.parametrize('interrupt', ['entry', 'probe', 'before_store', 'after_store', 'depth2'])
def test_clock_interrupt_and_valid_completed_stores(monkeypatch, interrupt):
    rows, turn = referee.INITIAL_ROWS, 'B'
    moves = _legal(rows, turn)
    moment = 6_500_000_000 if interrupt == 'entry' else 0
    completed_stores = []
    probe = ai_engine._TranspositionTable.probe
    store = ai_engine._TranspositionTable.store
    correct_depth1 = ai_engine.search_alpha_beta(rows, turn, 1, _legal, referee.apply_move)

    def probe_with_clock(self, index, key, depth, alpha, beta):
        nonlocal moment
        if interrupt == 'probe' or (interrupt == 'depth2' and depth == 1):
            moment = 6_500_000_000
        return probe(self, index, key, depth, alpha, beta)

    def store_with_clock(self, index, key, depth, value, window):
        nonlocal moment
        completed_stores.append((key, depth, value, window))
        store(self, index, key, depth, value, window)
        if interrupt == 'after_store':
            moment = 6_500_000_000

    def apply_with_clock(snapshot, color, move):
        nonlocal moment
        if interrupt == 'before_store':
            moment = 6_500_000_000
        return referee.apply_move(snapshot, color, move)

    monkeypatch.setattr(ai_engine._TranspositionTable, 'probe', probe_with_clock)
    monkeypatch.setattr(ai_engine._TranspositionTable, 'store', store_with_clock)
    result = timed_search.search_iterative(
        rows,
        turn,
        moves,
        _legal,
        apply_with_clock,
        command_received_ns=0,
        policy=timed_search.ResponsePolicy(),
        clock=lambda: moment,
        tt_capacity=1024,
    )
    expected = (
        (correct_depth1.move, correct_depth1.value, 1)
        if interrupt == 'depth2'
        else (moves[0], None, 0)
    )
    assert (result.move, result.value, result.completed_depth) == expected
    assert result.stop_reason == 'time_limit'
    assert len(completed_stores) == {'after_store': 1, 'depth2': len(moves)}.get(interrupt, 0)
    assert all(depth == 0 and window is None for _, depth, _, window in completed_stores)


def test_independent_table_per_selector_call():
    selector = timed_search.TimedSelector(
        _legal, referee.apply_move, timed_search.ResponsePolicy(max_depth=3), tt_capacity=64
    )
    rows = referee.INITIAL_ROWS
    one = ai_engine.TTDiagnostics()
    two = ai_engine.TTDiagnostics()
    first = selector.select(rows, 'B', _legal(rows, 'B'), tt_stats=one)
    second = selector.select(rows, 'B', _legal(rows, 'B'), tt_stats=two)
    assert one == two and one.probes > 0 and first == second


def test_real_trace_and_gui(monkeypatch, tmp_path):
    import json
    from pathlib import Path

    monkeypatch.delenv(ai_engine.TT_CAPACITY_ENV, raising=False)
    cfg = {
        'format_version': 2,
        'budget': {'kind': 'depth', 'limit_depth': 3},
        'search': {'tt_capacity': 1024},
    }
    config, trace = tmp_path / 'cfg.json', tmp_path / 'trace.jsonl'
    config.write_text(json.dumps(cfg), encoding='utf-8')
    script = Path(reversi.__file__).resolve()
    with protocol.Player(
        script,
        evaluation_config=config,
        evaluation_trace=trace,
        evaluation_context=_context(cfg),
    ) as child:
        assert child.name == 'ranger'
        move = tuple(map(int, child.command('genmove B')[0].split()))
        assert move in _legal(referee.INITIAL_ROWS, 'B')
    events = [json.loads(line) for line in trace.read_bytes().splitlines()]
    (event,) = [event for event in events if event['type'] == 'genmove']
    assert event['tt_diagnostics']['capacity'] == 1024
    assert 0 < event['tt_diagnostics']['probes'] < event['nodes']
    assert events[0]['effective_config'] == cfg
    monkeypatch.setenv(ai_engine.TT_CAPACITY_ENV, '1024')
    with web.Process() as gui:
        assert gui.command('name') == 'ranger'
        reply = tuple(map(int, gui.command('genmove B').split()))
        assert reply in _legal(referee.INITIAL_ROWS, 'B')
