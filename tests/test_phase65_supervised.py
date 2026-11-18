"""原本の形、独立再生、分割、教師更新とcheckpointの回帰。"""

from hashlib import sha256
from io import BytesIO
import json
from math import tanh
import pickle
from random import Random

import pytest

from harness import referee
from reversi_player import ntuple, ntuple_io
from tools import phase65_data as data
from tools import phase65_decode as decoder
from tools import phase65_train as train


def _example(seed):
    """独立refereeの合法手から終局までの試験用棋譜を作る。"""
    rng = Random(seed)
    rows = referee.INITIAL_ROWS
    turn = 'B'
    moves = []
    while referee.result(rows) == 'NG':
        options = referee.legal_moves(rows, turn)
        if options:
            move = rng.choice(options)
            rows = referee.apply_move(rows, turn, move)
        else:
            move = (-1, -1)
        moves.append(list(move))
        turn = 'W' if turn == 'B' else 'B'
    score = ''.join(rows).count('B') - ''.join(rows).count('W')
    return [moves, score]


@pytest.mark.parametrize('games', [1, 2, 3])
def test_data_only_decoder_preserves_references_and_source_order(games):
    move = (1, 7)
    expected = [([move, (-1, -1), move], index - 1) for index in range(games)]
    content = pickle.dumps(expected, protocol=3)
    stream = BytesIO(content)
    _, refs = decoder._inventory(stream)
    assert refs
    stream.seek(0)
    assert list(decoder.decode(stream, refs)) == expected


@pytest.mark.parametrize(
    'encoded,match',
    [
        (b'\x80\x03X\x01\x00\x00\x00a.', 'unsupported_opcode'),
        (pickle.dumps([([(0, 0)], 0)], protocol=3) + b'garbage', 'bytes_after_stop'),
        (pickle.dumps([([(0, 0)], 0)], protocol=4), 'unsupported_opcode'),
    ],
)
def test_data_only_decoder_rejects_foreign_opcodes_and_trailing_bytes(encoded, match):
    with pytest.raises(ValueError, match=match):
        decoder._inventory(BytesIO(encoded))


@pytest.mark.parametrize(
    'raw,reason',
    [
        ([[[True, 3]], 0], 'coordinate_shape'),
        ([[[-1, 0]], 0], 'coordinate_range'),
        ([[[9, 3]], 0], 'coordinate_range'),
        ([[], False], 'margin_shape'),
        ([[], 99], 'margin_shape'),
    ],
)
def test_input_audit_rejects_mislabeled_shapes(raw, reason):
    with pytest.raises(data.GameError, match=reason):
        data.parse_game(raw)


def test_replay_uses_forced_pass_and_excludes_terminal_features():
    for seed in range(30):
        moves, margin = data.parse_game(_example(seed))
        if 64 in moves:
            break
    assert 64 in moves
    score, states = data.replay(moves, margin, collect=True)
    assert score == margin
    i = moves.index(64)
    before, after = states[i : i + 2]
    assert before.rows == after.rows
    assert before.turn != after.turn and before.placements == after.placements
    assert len(states) == len(moves) and states[-1].ply == len(moves) - 1
    assert referee.result(states[-1].rows) == 'NG'
    assert data.replay(moves, None)[0] == score
    with pytest.raises(data.GameError, match='label_mismatch'):
        data.replay(moves, score + 1)
    with pytest.raises(data.GameError, match='pass_after_end'):
        data.replay(moves + b'@', score)
    assert not any(r != 'EEEEEEEE' for r in states[0].rows[:3])


@pytest.mark.parametrize(
    'moves,error',
    [
        (b'@', 'pass_with_legal_move'),
        (b'\x00', 'illegal_move'),
        (b'A', 'coordinate_range'),
        (b'', 'incomplete'),
    ],
)
def test_illegal_incomplete_and_invalid_pass(moves, error):
    with pytest.raises(data.GameError, match=error):
        data.replay(moves, None)


def test_audit_records_exclusions_and_freezes_deterministic_splits(tmp_path):
    raw = [_example(seed) for seed in range(24)]
    base_moves, margin = raw[0]
    mirror = [[7 - x, 7 - y] if x != -1 else [-1, -1] for x, y in base_moves]
    records = raw + [
        raw[0],
        [mirror, margin],
        [base_moves[:-1], margin],
        [[[0, 0]], 0],
        [base_moves, margin + 1],
    ]
    source = tmp_path / 'source.jsonl'
    source.write_text(''.join(json.dumps(obj) + '\n' for obj in records), encoding='utf-8')
    first, second = tmp_path / 'first', tmp_path / 'second'
    report = data.audit(source, first)
    data.audit(source, second)
    assert report['input_games'] == 29
    assert report['usable'] == 24
    assert report['excluded'] == {
        'exact_duplicate': 1,
        'd4_duplicate': 1,
        'incomplete': 1,
        'illegal_move': 1,
        'label_mismatch': 1,
    }
    assert report['incomplete_prefix_matches'] == 1
    assert data.sha_file(first / 'split-manifest.json') == data.sha_file(
        second / 'split-manifest.json'
    )
    groups = [
        {g.game_id for g in data.read_split(first / (name + '.jsonl'))} for name in data.SPLITS
    ]
    assert sum(map(len, groups)) == len(set.union(*groups)) == 24


@pytest.mark.parametrize('outcome', [-1, 0, 1])
@pytest.mark.parametrize('turn', ['B', 'W'])
def test_oracle_gradient_vs_phase6_finite_difference(outcome, turn):
    rows = referee.INITIAL_ROWS
    model = ntuple.NtupleModel(tuple((i % 7 - 3) * 1e-3 for i in range(1872)))
    item = train.encode(data.State(rows, turn, 0, 0), model)
    assert train.predict(model.weights, item.codes) == pytest.approx(
        model.value(rows, turn), abs=1e-14
    )
    idx, count = next(pair for pair in model.features(rows, turn) if pair[1] > 1)
    delta = 1e-6
    weights = list(model.weights)
    weights[idx] += delta
    upper = (ntuple.NtupleModel(tuple(weights)).value(rows, turn) - outcome) ** 2 / 2
    weights[idx] -= 2 * delta
    lower = (ntuple.NtupleModel(tuple(weights)).value(rows, turn) - outcome) ** 2 / 2
    weights = list(model.weights)
    rate = 1e-6
    predicted, gradient = train.update(weights, item.codes, outcome, rate)
    exact = (predicted - outcome) * (1 - predicted**2) * count
    assert exact == pytest.approx((upper - lower) / (2 * delta), abs=2e-9)
    assert (model.weights[idx] - weights[idx]) / rate == pytest.approx(exact, abs=2e-9)
    assert all(
        weights[i] == model.weights[i] for i in (range(936, 1872) if turn == 'B' else range(936))
    )
    assert (train.predict(weights, item.codes) - outcome) ** 2 <= (predicted - outcome) ** 2


def test_single_weight_hand_calculation_and_saturation():
    codes = (0, 8 * 936 + 2)
    weights = [0.0] * 1872
    assert train.update(weights, codes, 1, 0.01) == (0.0, -1.0)
    assert weights[0] == 0.01 and weights[936] == 0.03 and weights[1] == 0
    assert train.predict(weights, codes) == tanh(0.01 + 3 * 0.03)
    kept = weights[:]
    train.update(weights, codes, -1, 0.0)
    assert weights == kept
    weights[936] = 20
    assert train.update(weights, codes, -1, 1) == (1.0, 0.0)
    assert weights[936] == 20
    for _ in range(50):
        train.update(kept, codes, 1, 0.01)
    assert abs(1 - train.predict(kept, codes)) < 0.25


def test_features_do_not_depend_on_outcome_and_keep_d4_vs_turn_separate():
    moves, margin = data.parse_game(_example(7))
    actual, snapshots = data.replay(moves, margin, collect=True)
    assert data.label(actual) == (actual > 0) - (actual < 0)
    key = sha256(data.canonical(moves)).hexdigest()
    samples = data.sample_states(snapshots, key)
    assert samples == data.sample_states(snapshots, key)
    assert [state.bucket for state in samples] == list(range(6))
    state = samples[3]
    rotated = data.State(
        tuple(row[::-1] for row in state.rows[::-1]), state.turn, state.ply, state.placements
    )
    model = ntuple.NtupleModel.zeros()
    assert train.encode(rotated, model) == train.encode(state, model)
    same_board_other_turn = data.State(state.rows, 'B', state.ply, state.placements)
    assert (
        train.encode(same_board_other_turn, model).state_id != train.encode(state, model).state_id
    )


def test_fixture_learning_cache_checkpoint_and_test_freeze(tmp_path):
    source = tmp_path / 'sample.jsonl'
    source.write_text(
        ''.join(json.dumps(_example(seed)) + '\n' for seed in range(48)), encoding='utf-8'
    )
    dataset = tmp_path / 'dataset'
    initial = tmp_path / 'init.json'
    ntuple_io.save_model(initial, ntuple.NtupleModel.zeros())
    data.audit(source, dataset)
    freeze = tmp_path / 'freeze.json'
    with pytest.raises(ValueError, match='test_requires_frozen_candidate'):
        train.prepare(dataset, 'test', tmp_path / 'test', None)
    for name in ('train', 'validation'):
        train.prepare(dataset, name, tmp_path / name, None)
    for run in ('run1', 'run2'):
        train.fit(
            tmp_path / 'train',
            tmp_path / 'validation',
            initial,
            tmp_path / run,
            rate=0.0005,
            epochs=2,
            seed=41,
            constant=0.0,
        )
    first, second = (tmp_path / run / 'epoch-2.json' for run in ('run1', 'run2'))
    learned = ntuple_io.load_model(first)
    assert learned.model_id != ntuple_io.load_model(initial).model_id
    assert first.read_bytes() == second.read_bytes()
    train.freeze_candidate(freeze, initial, first, dataset / 'split-manifest.json', 0.0, 'fixture')
    train.prepare(dataset, 'test', tmp_path / 'test', None, freeze=freeze)
    result = train.evaluate_test(tmp_path / 'test', freeze, tmp_path / 'test-result')
    assert result['games'] > 0 and result['freeze_sha256'] == data.sha_file(freeze)
    first.write_bytes(first.read_bytes() + b' ')
    with pytest.raises(ValueError, match='frozen_checkpoint_changed'):
        train.evaluate_test(tmp_path / 'test', freeze, tmp_path / 'extra')
    states = tmp_path / 'train' / 'states.bin'
    states.write_bytes(states.read_bytes() + b'corrupt')
    with pytest.raises(ValueError, match='cache_hash_mismatch'):
        with train.open_cache(tmp_path / 'train'):
            pytest.fail('must verify before opening cache')
