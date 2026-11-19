"""資料の独立性を保ったPhase 7対局・集計の回帰検証。"""

import pytest

from tools.phase65_data import parse_game
from tools.phase7_eval import _fixed, paired_interval
from tools.phase7_results import _contrast, _prefix


def test_real_baseline_manifests_keep_explicit_fixed_work():
    script, assets, config = _fixed('positional_v1')
    assert script.name == 'reversi.py'
    assert {file.name for file in assets} == {'manifest.json', 'reversi.py', 'ai_engine.py'}
    assert config is None
    script, assets, config = _fixed('search_v1')
    assert script.name == 'reversi.py'
    assert config is not None
    assert config.name == 'eval-depth2.json' and config in assets


def test_pair_contrast_matches_cases_and_retains_independent_seed_dispersion():
    scores = {701: {'x': 0.0, 'y': 1.0}, 702: {'y': 0.0, 'x': 0.5}}
    output = _contrast(scores)
    assert output['by_case'] == {'x': 0.25, 'y': 0.5}
    assert output['by_seed'] == {701: 0.5, 702: 0.25}
    assert output['mean'] == 0.375
    assert output['seed_sample_stdev'] == pytest.approx(0.125 * (2.0**0.5))
    assert output['opening_pair_bootstrap_95_fixed_seeds'] == [0.25, 0.5]
    assert paired_interval([0.5] * 12) == [0.5, 0.5]
    with pytest.raises(ValueError, match='case_mismatch'):
        _contrast({701: {'x': 0.0}, 702: {'z': 1.0}})


def test_prefix_counts_placements_and_keeps_pass_under_symmetry():
    moves = [[0, k] for k in range(4)] + [[-1, -1]] + [[1, k] for k in range(5)]
    mirrored = [[7 - x, y] if x >= 0 else [x, y] for x, y in moves]
    a, _ = parse_game([moves, None])
    b, _ = parse_game([mirrored, None])
    prefix = _prefix(a)
    assert prefix == _prefix(b)
    assert len(prefix) == 9
    assert prefix[4] == 64
