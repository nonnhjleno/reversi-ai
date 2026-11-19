"""Phase 6.5の局数と学習率を事前条件に沿って拡大し、testを凍結後に評価する。"""

from argparse import ArgumentParser
from dataclasses import dataclass
import json
from pathlib import Path
import platform
import sys
from time import perf_counter

from harness import referee
from reversi_player import ntuple_io
from tools import phase65_data as data, phase65_train as train
from tools.quality import Step, run_step

_ROOT = Path(__file__).resolve().parents[1]
_RATES = (0.000125, 0.0005, 0.001)
_SEEDS = (65, 66, 67)


@dataclass(frozen=True, slots=True)
class Candidate:
    """validationで選んだcheckpointの比較に必要な値。"""

    name: str
    games: int
    seed: int
    rate: float
    epoch: int
    checkpoint: Path
    mse: float
    constant_mse: float
    saturated_fraction: float


def _invoke(name: str, args: list[str], output: Path) -> None:
    """各試行の実argv、終了値、ログを保存する。"""
    result = run_step(
        Step(name, (sys.executable, '-B', '-m', 'tools.phase65_train', *args), 900),
        _ROOT,
        output / (name + '.log'),
    )
    train._json(output / (name + '-step.json'), result)
    print(name, result['exit_code'], result['seconds'], flush=True)
    if not result['passed']:
        raise RuntimeError('child_failed:' + name)


def _has_disjoint_groups(dataset: Path) -> dict[str, int]:
    """固定分割に、同じD4グループが二重に存在しないことを確認する。"""
    seen: set[str] = set()
    counts = {}
    for name in data.SPLITS:
        identifiers = {game.game_id for game in data.read_split(dataset / (name + '.jsonl'))}
        if seen & identifiers:
            raise ValueError('duplicated_split_group')
        seen.update(identifiers)
        counts[name] = len(identifiers)
    return counts


def run(dataset: Path, initial: Path, output: Path) -> dict[str, object]:
    """小規模から段階実験を行い、凍結後のtestと推論コストを保存する。"""
    start = perf_counter()
    output.mkdir()
    audit = json.loads((dataset / 'audit.json').read_bytes())
    counts = _has_disjoint_groups(dataset)
    if any(counts[name] != audit['splits'][name]['games'] for name in counts):
        raise ValueError('group_count_mismatch')
    available = counts['train']
    labels = audit['splits']['train']['labels']
    constant = (labels.get('1', 0) - labels.get('-1', 0)) / available
    init = ntuple_io.load_model(initial)
    if any(init.model.weights):
        raise ValueError('expected_zero_initial_model')
    _json = train._json
    _json(
        output / 'plan.json',
        {
            'argv': sys.argv,
            'python': sys.version,
            'os': platform.platform(),
            'processor': platform.processor(),
            'counts': counts,
            'rates': _RATES,
            'seeds': _SEEDS,
            'constant': constant,
            'split_manifest_sha256': data.sha_file(dataset / 'split-manifest.json'),
            'source_sha256': {
                **train.training_sources(),
                'tools/phase65_experiment.py': data.sha_file(Path(__file__)),
            },
            'initial_sha256': init.file_sha256,
            'initial_id': init.model_id,
        },
    )
    validation = output / 'validation'
    _invoke('cache-validation', ['prepare', str(dataset), 'validation', str(validation)], output)

    def cache(games: int) -> Path:
        """決定済みtrain順のprefixだけからcacheを作る。"""
        directory = output / f'train-{games}'
        _invoke(
            f'cache-{games}',
            ['prepare', str(dataset), 'train', str(directory), '--limit', str(games)],
            output,
        )
        return directory

    def fit(games: int, rate: float, seed: int, epochs: int, *, repeat: bool = False) -> Candidate:
        """指定した設定を1回走らせ、validation最低のepochを返す。"""
        name = f'fit-{games}-{rate:g}-{seed}' + ('-repeat' if repeat else '')
        path = output / name
        _invoke(
            name,
            [
                'fit',
                str(output / f'train-{games}'),
                str(validation),
                str(initial),
                str(path),
                '--rate',
                str(rate),
                '--epochs',
                str(epochs),
                '--seed',
                str(seed),
                '--constant',
                str(constant),
            ],
            output,
        )
        result = json.loads((path / 'training.json').read_bytes())
        best = min(result['history'], key=lambda row: row['validation']['all']['mse'])
        metrics = best['validation']['all']
        return Candidate(
            name,
            games,
            seed,
            rate,
            best['epoch'],
            Path(best['checkpoint']),
            metrics['mse'],
            result['baselines']['constant']['all']['mse'],
            metrics['saturated'] / metrics['samples'],
        )

    cache(1000)
    stage1 = [fit(1000, rate, _SEEDS[0], 3) for rate in _RATES]
    best1 = min(stage1, key=lambda item: item.mse)
    repeated = fit(1000, best1.rate, _SEEDS[0], 3, repeat=True)
    if best1.mse != repeated.mse or best1.epoch != repeated.epoch:
        raise ValueError('best_epoch_not_reproducible')
    for epoch in range(1, 4):
        if (output / best1.name / f'epoch-{epoch}.json').read_bytes() != (
            output / repeated.name / f'epoch-{epoch}.json'
        ).read_bytes():
            raise ValueError('checkpoint_not_bit_reproducible')

    cache(5000)
    stage2 = [fit(5000, best1.rate, seed, 4) for seed in _SEEDS]
    candidates = [*stage1, *stage2]
    primary = [best1, stage2[0]]
    largest = 5000
    decision = '5000のprimary runを基に20,000局へ拡大'
    if stage2[0].saturated_fraction < 0.95:
        cache(20000)
        best3 = fit(20000, best1.rate, _SEEDS[0], 4)
        candidates.append(best3)
        primary.append(best3)
        largest = 20000
        can_expand = (
            best3.constant_mse - best3.mse >= 0.005
            and stage2[0].mse - best3.mse >= 0.002
            and best3.saturated_fraction < 0.95
            and available > 20000
        )
        if can_expand:
            cache(available)
            best4 = fit(available, best1.rate, _SEEDS[0], 4)
            candidates.append(best4)
            primary.append(best4)
            largest = available
            decision = '事前登録のvalidation改善条件を満たし、train全体を使用'
        else:
            decision = 'validation改善条件または利用可能局数によって最大規模への拡大を見送った'
    else:
        decision = '5,000局のprimary runで過度のtanh飽和を検出した'
    chosen = min(primary, key=lambda item: item.mse)
    freeze = output / 'freeze.json'
    report = train.freeze_candidate(
        freeze,
        initial,
        chosen.checkpoint,
        dataset / 'split-manifest.json',
        constant,
        decision + f'。凍結はprimary seedの最小validation MSE、{chosen.name} epoch {chosen.epoch}',
    )
    target = output / 'test'
    _invoke(
        'cache-test',
        ['prepare', str(dataset), 'test', str(target), '--freeze', str(freeze)],
        output,
    )
    _invoke('test', ['test', str(target), str(freeze), str(output / 'final-test')], output)
    train.test_without_shared_states(
        target,
        freeze,
        output / f'train-{chosen.games}',
        validation,
        output / 'test-state-overlap.json',
    )
    _json(
        output / 'inference.json',
        train.measure_inference(
            initial, chosen.checkpoint, data.State(referee.INITIAL_ROWS, 'B', 0, 0)
        ),
    )
    result: dict[str, object] = {
        'largest_games': largest,
        'decision': decision,
        'freeze': report,
        'chosen': chosen.name,
        'selected_games': chosen.games,
        'selected_epoch': chosen.epoch,
        'validation_mse': chosen.mse,
        'reproducible_1000': True,
        'runs': [
            {
                'name': c.name,
                'games': c.games,
                'seed': c.seed,
                'rate': c.rate,
                'epoch': c.epoch,
                'mse': c.mse,
                'constant_mse': c.constant_mse,
                'saturated_fraction': c.saturated_fraction,
            }
            for c in candidates
        ],
        'seconds': perf_counter() - start,
    }
    _json(output / 'summary.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main() -> int:
    """監査済み分割と未学習初期モデルに対する段階実験のCLI。"""
    parser = ArgumentParser(description=__doc__)
    for name in ('dataset', 'initial', 'output'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    run(args.dataset.resolve(), args.initial.resolve(), args.output.resolve())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
