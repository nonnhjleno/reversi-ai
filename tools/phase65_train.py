"""Phase 6互換の教師あり更新、固定された評価とcheckpointを扱う。"""

from argparse import ArgumentParser
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
from math import fsum, isfinite, sqrt, tanh
from mmap import ACCESS_READ, mmap
from operator import itemgetter
from pathlib import Path
from random import Random
from statistics import median
from struct import Struct, pack, unpack_from
import sys
from time import perf_counter
from typing import BinaryIO

from reversi_player import ntuple, ntuple_io
from tools import phase65_data as data

_MAGIC = b'P65\x01'
_GAME = Struct('<32sbB')
_STATE = Struct('<BH32s')
_GATHER = tuple(itemgetter(*table[:64]) for table in data._D4)
_SLICE_NAMES = (
    'all',
    'early',
    'middle',
    'late',
    'B',
    'W',
    'black_win',
    'draw',
    'white_win',
)
_PRIMARY_SEED = 65


@dataclass(frozen=True, slots=True)
class Sample:
    """1局面の群番号、D4同値識別子と疎なPhase 6出現回数。"""

    bucket: int
    state_id: bytes
    codes: tuple[int, ...]


def encode(state: data.State, model: ntuple.NtupleModel) -> Sample:
    """Phase 6 featuresの重みID・countを16ビットへ格納する。"""
    rows = ''.join(state.rows).encode('ascii')
    key = min(bytes(image(rows)) for image in _GATHER)
    state_id = sha256(state.turn.encode('ascii') + key).digest()
    features = model.features(state.rows, state.turn)
    codes = tuple(8 * index + count - 1 for index, count in features)
    return Sample(state.bucket, state_id, codes)


def predict(weights: list[float] | tuple[float, ...], codes: tuple[int, ...]) -> float:
    """キャッシュされた出現回数の積の和から黒視点の予測値を返す。"""
    return tanh(fsum(weights[code >> 3] * ((code & 7) + 1) for code in codes))


def loss_gradient(value: float, y: int) -> float:
    """重みごとのcountを掛ける前の二乗損失の勾配因子。"""
    return (value - y) * (1 - value * value)


def update(
    weights: list[float], codes: tuple[int, ...], y: int, rate: float
) -> tuple[float, float]:
    """1状態の更新を適用し、更新前の予測値と勾配因子を返す。"""
    value = predict(weights, codes)
    gradient = loss_gradient(value, y)
    if rate:
        step = rate * gradient
        for code in codes:
            weights[code >> 3] -= step * ((code & 7) + 1)
    return value, gradient


def _state_record(output: BinaryIO, item: Sample) -> None:
    """局面の長さと疎な特徴列を保存する。"""
    count = len(item.codes)
    output.write(_STATE.pack(item.bucket, count, item.state_id))
    output.write(pack(f'<{count}H', *item.codes))


class PackedGames:
    """ファイルに記録した各対局の位置だけをメモリに保持する。"""

    def __init__(self, payload: mmap) -> None:
        """cacheのサイズと各レコードの境界を照合する。"""
        self.payload = payload
        if payload[:4] != _MAGIC:
            raise ValueError('bad_cache_header')
        offsets = []
        position = 4
        while position < len(payload):
            offsets.append(position)
            _, outcome, count = _GAME.unpack_from(payload, position)
            if outcome not in (-1, 0, 1) or not 1 <= count <= 6:
                raise ValueError('bad_cache_game_header')
            position += _GAME.size
            seen = set()
            for _ in range(count):
                bucket, width, _ = _STATE.unpack_from(payload, position)
                if not 0 <= bucket < 6 or bucket in seen or not 1 <= width <= 448:
                    raise ValueError('bad_cache_state_header')
                seen.add(bucket)
                position += _STATE.size + width * 2
        if position != len(payload) or not offsets:
            raise ValueError('bad_cache_length')
        self.offsets = offsets

    def game(self, index: int) -> tuple[bytes, int, list[Sample]]:
        """指定した1局のcacheレコードを必要な時だけ復元する。"""
        position = self.offsets[index]
        game_id, y, count = _GAME.unpack_from(self.payload, position)
        position += _GAME.size
        items = []
        for _ in range(count):
            bucket, width, key = _STATE.unpack_from(self.payload, position)
            position += _STATE.size
            features = unpack_from(f'<{width}H', self.payload, position)
            items.append(Sample(bucket, key, features))
            position += width * 2
        return game_id, y, items


@contextmanager
def open_cache(directory: Path) -> Iterator[PackedGames]:
    """検証済みcacheをread-onlyでmapし、終了時に閉じる。"""
    path = directory / 'states.bin'
    manifest = json.loads((directory / 'cache.json').read_bytes())
    if data.sha_file(path) != manifest['sha256']:
        raise ValueError('cache_hash_mismatch')
    with path.open('rb') as stream, mmap(stream.fileno(), 0, access=ACCESS_READ) as contents:
        packed = PackedGames(contents)
        if len(packed.offsets) != manifest['games']:
            raise ValueError('cache_game_count_mismatch')
        yield packed


def _json(path: Path, value: object) -> None:
    """再実行時に旧証拠を保持するため、新規JSONとして保存する。"""
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, allow_nan=False, indent=2)


def peak_memory_bytes() -> int | None:
    """Windowsのプロセスピークworking setをOSから読み取る。"""
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        """PROCESS_MEMORY_COUNTERSのOSレイアウト。"""

        _fields_ = [
            ('cb', wintypes.DWORD),
            ('faults', wintypes.DWORD),
            ('peak_working_set', ctypes.c_size_t),
            ('working_set', ctypes.c_size_t),
            ('peak_paged', ctypes.c_size_t),
            ('paged', ctypes.c_size_t),
            ('peak_nonpaged', ctypes.c_size_t),
            ('nonpaged', ctypes.c_size_t),
            ('pagefile', ctypes.c_size_t),
            ('peak_pagefile', ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    process = kernel.GetCurrentProcess
    process.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    memory = psapi.GetProcessMemoryInfo
    memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    memory.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not memory(process(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(counters.peak_working_set)


def _freeze_assets(freeze: Path) -> tuple[Path, Path, dict[str, object]]:
    """凍結記録が指す候補と評価用資産のhashを検証する。"""
    record = json.loads(freeze.read_bytes())
    checkpoint = Path(record['checkpoint'])
    if data.sha_file(checkpoint) != record['checkpoint_sha256']:
        raise ValueError('frozen_checkpoint_changed')
    initial = Path(record['initial'])
    if data.sha_file(initial) != record['initial_sha256']:
        raise ValueError('frozen_initial_changed')
    return checkpoint, initial, record


def prepare(
    dataset: Path, name: str, target: Path, limit: int | None, *, freeze: Path | None = None
) -> dict[str, object]:
    """分割を固定してから再生、サンプリング、特徴抽出の時間を記録する。"""
    tick = perf_counter()
    if name not in data.SPLITS:
        raise ValueError('bad_split_name')
    if name == 'test':
        if freeze is None:
            raise ValueError('test_requires_frozen_candidate')
        _, _, record = _freeze_assets(freeze)
        if (
            record['split_manifest_sha256'] != data.sha_file(dataset / 'split-manifest.json')
            or limit is not None
        ):
            raise ValueError('test_requires_frozen_full_split')
    split_report = json.loads((dataset / 'split-manifest.json').read_bytes())
    source = dataset / (name + '.jsonl')
    if data.sha_file(source) != split_report['splits'][name]['sha256']:
        raise ValueError('split_hash_mismatch')
    available = split_report['splits'][name]['games']
    if limit is not None and not 0 < limit <= available:
        raise ValueError('invalid_sample_limit')
    expected = available if limit is None else limit
    target.mkdir()
    output = target / 'states.bin'
    model = ntuple.NtupleModel.zeros()
    replay_seconds = sample_seconds = feature_seconds = 0.0
    games = samples = outcomes = 0
    with output.open('xb') as binary:
        binary.write(_MAGIC)
        for game in data.read_split(source):
            start = perf_counter()
            _, states = data.replay(game.moves, game.margin, collect=True)
            replay_seconds += perf_counter() - start
            start = perf_counter()
            selected = data.sample_states(states, game.game_id)
            sample_seconds += perf_counter() - start
            if not selected:
                raise ValueError('empty_samples')
            start = perf_counter()
            batch = [encode(state, model) for state in selected]
            feature_seconds += perf_counter() - start
            y = data.label(game.margin)
            binary.write(_GAME.pack(bytes.fromhex(game.game_id), y, len(batch)))
            for item in batch:
                _state_record(binary, item)
            games += 1
            samples += len(batch)
            outcomes += y
            if games == expected:
                break
    if games != expected:
        raise ValueError('split_count_changed')
    result = {
        'format_version': 1,
        'sha256': data.sha_file(output),
        'bytes': output.stat().st_size,
        'split_manifest_sha256': data.sha_file(dataset / 'split-manifest.json'),
        'split_name': name,
        'split_sha256': split_report['splits'][name]['sha256'],
        'normalized_sha256': split_report['source_sha256'],
        'sample_seed': data.SAMPLE_SEED,
        'games': games,
        'samples': samples,
        'mean_y': outcomes / games,
        'seconds': perf_counter() - tick,
        'replay_seconds': replay_seconds,
        'sample_seconds': sample_seconds,
        'feature_seconds': feature_seconds,
        'peak_working_set_bytes': peak_memory_bytes(),
    }
    _json(target / 'cache.json', result)
    return result


@dataclass(slots=True)
class _Aggregate:
    """1群の誤差と飽和数を、局内のサンプル重み付きで集計する。"""

    count: int = 0
    weight: float = 0
    square: float = 0
    absolute: float = 0
    saturated: int = 0

    def add(self, prediction: float, y: int, weight: float) -> None:
        """1局面を適切な局内重みで加える。"""
        error = abs(prediction - y)
        self.count += 1
        self.weight += weight
        self.square += weight * error**2
        self.absolute += weight * error
        self.saturated += abs(prediction) >= 0.99

    def summary(self) -> dict[str, float | int | None]:
        """空の群は誤差未定義として出力する。"""
        mse = self.square / self.weight if self.weight else None
        return {
            'samples': self.count,
            'mse': mse,
            'rmse': sqrt(mse) if mse is not None else None,
            'mae': self.absolute / self.weight if self.weight else None,
            'saturated': self.saturated,
        }


def _score_by_group(
    cache: PackedGames,
    weights: tuple[float, ...] | None,
    constant: float,
    *,
    pairs: list[tuple[str, float]] | None = None,
    exclude: set[bytes] | None = None,
) -> dict[str, dict[str, float | int | None]]:
    """対局全体と各群の等重みMSE、RMSE、MAEを評価する。"""
    totals = {name: _Aggregate() for name in _SLICE_NAMES}
    for index in range(len(cache.offsets)):
        key, y, samples = cache.game(index)
        evaluated = [
            (sample.bucket, predict(weights, sample.codes) if weights is not None else constant)
            for sample in samples
            if exclude is None or sample.state_id not in exclude
        ]
        if not evaluated:
            continue
        groups: dict[str, list[float]] = {
            'all': [value for _, value in evaluated],
            ('draw' if y == 0 else 'black_win' if y == 1 else 'white_win'): [
                value for _, value in evaluated
            ],
        }
        for bucket, value in evaluated:
            groups.setdefault(_SLICE_NAMES[1 + bucket // 2], []).append(value)
            groups.setdefault('W' if bucket % 2 else 'B', []).append(value)
        for name, values in groups.items():
            for prediction in values:
                totals[name].add(prediction, y, 1 / len(values))
        if pairs is not None:
            values_all = groups['all']
            pairs.append(
                (key.hex(), fsum((value - y) ** 2 for value in values_all) / len(values_all))
            )
    return {name: total.summary() for name, total in totals.items()}


def _cache_manifest(path: Path) -> dict[str, object]:
    """読込元の分割と生成条件をmanifestから取り出す。"""
    record: dict[str, object] = json.loads((path / 'cache.json').read_bytes())
    return record


def training_sources() -> dict[str, str]:
    """分割・変換・学習・推論の実ソースを来歴へ結び付ける。"""
    root = Path(__file__).resolve().parents[1]
    return {
        name: data.sha_file(root / name)
        for name in (
            'tools/phase65_data.py',
            'tools/phase65_train.py',
            'tools/phase65_decode.py',
            'tools/phase65_isolate.sh',
            'harness/referee.py',
            'reversi_player/ntuple.py',
            'reversi_player/ntuple_io.py',
        )
    }


def fit(
    train: Path,
    validation: Path,
    initial: Path,
    output: Path,
    *,
    rate: float,
    epochs: int,
    seed: int,
    constant: float,
) -> dict[str, object]:
    """trainだけを更新し、各epochのvalidationとcheckpointを保存する。"""
    if not isfinite(rate) or rate < 0 or epochs < 1 or not -1 <= constant <= 1:
        raise ValueError('invalid_training_config')
    t_info, v_info = _cache_manifest(train), _cache_manifest(validation)
    if not (
        t_info['split_name'] == 'train'
        and v_info['split_name'] == 'validation'
        and t_info['split_manifest_sha256'] == v_info['split_manifest_sha256']
    ):
        raise ValueError('wrong_training_split')
    output.mkdir()
    sources = training_sources()
    base = ntuple_io.load_model(initial)
    weights = list(base.model.weights)
    rng = Random(seed)
    config = {
        'rate': rate,
        'epochs': epochs,
        'seed': seed,
        'constant': constant,
        'initial_model_id': base.model_id,
        'initial_file_sha256': base.file_sha256,
        'train': t_info,
        'validation': v_info,
        'source_sha256': sources,
        'python': sys.version,
        'argv': sys.argv,
    }
    _json(output / 'config.json', config)
    history = []
    with open_cache(train) as examples, open_cache(validation) as valid:
        order = list(range(len(examples.offsets)))
        for epoch in range(1, epochs + 1):
            tick = perf_counter()
            rng.shuffle(order)
            online = _Aggregate()
            max_gradient = 0.0
            for index in order:
                _, y, states = examples.game(index)
                share = 1 / len(states)
                for sample in states:
                    value, gradient = update(weights, sample.codes, y, rate * share)
                    max_gradient = max(max_gradient, abs(gradient))
                    online.add(value, y, share)
            train_seconds = perf_counter() - tick
            model = ntuple.NtupleModel(tuple(weights))
            tick = perf_counter()
            evaluation = _score_by_group(valid, model.weights, constant)
            validation_seconds = perf_counter() - tick
            path = output / f'epoch-{epoch}.json'
            provenance = {
                'training_method': 'supervised_sequential_sgd_half_mse',
                'training_seed': str(seed),
                'learning_rate': repr(rate),
                'epoch': str(epoch),
                'initial_model_id': base.model_id,
                'train_games': str(len(order)),
                'train_cache_sha256': str(t_info['sha256']),
                'split_manifest_sha256': str(t_info['split_manifest_sha256']),
                'normalized_sha256': str(t_info['normalized_sha256']),
                'sampling': 'one_per_phase_and_turn_seed_65',
                'training_source_sha256': sources['tools/phase65_train.py'],
            }
            tick = perf_counter()
            model_id = ntuple_io.save_model(path, model, provenance=provenance)
            save_seconds = perf_counter() - tick
            tick = perf_counter()
            loaded = ntuple_io.load_model(path)
            load_seconds = perf_counter() - tick
            if loaded.model_id != model_id or loaded.model.weights != model.weights:
                raise ValueError('checkpoint_roundtrip_mismatch')
            info = {
                'epoch': epoch,
                'checkpoint': str(path.resolve()),
                'model_id': model_id,
                'checkpoint_sha256': loaded.file_sha256,
                'bytes': path.stat().st_size,
                'online_train': online.summary(),
                'validation': evaluation,
                'train_seconds': train_seconds,
                'validation_seconds': validation_seconds,
                'save_seconds': save_seconds,
                'load_seconds': load_seconds,
                'gradient_factor_abs_max': max_gradient,
                'gradient_weight_abs_upper_bound': max_gradient * 8,
                'weight_abs_max': max(map(abs, weights)),
                'weight_rms': sqrt(fsum(value * value for value in weights) / len(weights)),
            }
            _json(output / f'epoch-{epoch}-metrics.json', info)
            history.append(info)
        baseline = {
            'zero': _score_by_group(valid, None, 0),
            'constant': _score_by_group(valid, None, constant),
        }
    result: dict[str, object] = {
        'config': config,
        'history': history,
        'baselines': baseline,
        'peak_working_set_bytes': peak_memory_bytes(),
    }
    _json(output / 'training.json', result)
    return result


def freeze_candidate(
    path: Path,
    initial: Path,
    checkpoint: Path,
    split_manifest: Path,
    constant: float,
    decision: str,
) -> dict[str, object]:
    """候補・初期値・splitと選択理由をtestの前に固定する。"""
    initial_model = ntuple_io.load_model(initial)
    learned = ntuple_io.load_model(checkpoint)
    manifest_hash = data.sha_file(split_manifest)
    metadata = dict(learned.provenance)
    if (
        metadata.get('split_manifest_sha256') != manifest_hash
        or metadata.get('initial_model_id') != initial_model.model_id
    ):
        raise ValueError('checkpoint_experiment_mismatch')
    report = {
        'checkpoint': str(checkpoint.resolve()),
        'checkpoint_sha256': learned.file_sha256,
        'model_id': learned.model_id,
        'initial': str(initial.resolve()),
        'initial_sha256': initial_model.file_sha256,
        'constant': constant,
        'split_manifest_sha256': manifest_hash,
        'decision': decision,
    }
    _json(path, report)
    return report


def _interval(differences: list[float], *, seed: int = 940, trials: int = 400) -> list[float]:
    """対局を対で再抽出して誤差差の参考95%区間を返す。"""
    rng = Random(seed)
    count = len(differences)
    samples = sorted(sum(rng.choices(differences, k=count)) / count for _ in range(trials))
    return [samples[int(0.025 * trials)], samples[int(0.975 * trials) - 1]]


def evaluate_test(cache: Path, freeze: Path, output: Path) -> dict[str, object]:
    """凍結してある候補と2比較基準についてtestで一度の評価を記録する。"""
    checkpoint, initial, record = _freeze_assets(freeze)
    info = _cache_manifest(cache)
    if (
        info['split_name'] != 'test'
        or info['split_manifest_sha256'] != record['split_manifest_sha256']
    ):
        raise ValueError('wrong_test_split')
    constant = record['constant']
    if not isinstance(constant, (int, float)) or not -1 <= constant <= 1:
        raise ValueError('bad_frozen_constant')
    output.mkdir()
    learned = ntuple_io.load_model(checkpoint)
    baseline = ntuple_io.load_model(initial)
    result: dict[str, object] = {}
    errors: list[list[tuple[str, float]]] = []
    with open_cache(cache) as packed:
        for name, weights, prediction in (
            ('candidate', learned.model.weights, 0.0),
            ('zero', baseline.model.weights, 0.0),
            ('constant', None, constant),
        ):
            pairs: list[tuple[str, float]] = []
            result[name] = _score_by_group(packed, weights, prediction, pairs=pairs)
            errors.append(pairs)
    intervals = {}
    with (output / 'game-errors.jsonl').open('x', encoding='utf-8') as per_game:
        for candidate, zero, reference in zip(*errors, strict=True):
            if not candidate[0] == zero[0] == reference[0]:
                raise ValueError('baseline_game_mismatch')
            per_game.write(json.dumps([candidate[0], candidate[1], zero[1], reference[1]]) + '\n')
    for name, comparator in zip(('zero', 'constant'), errors[1:], strict=True):
        diffs = [
            other[1] - candidate[1] for candidate, other in zip(errors[0], comparator, strict=True)
        ]
        intervals[name] = {
            'improvement_mse': fsum(diffs) / len(diffs),
            'bootstrap_95': _interval(diffs),
        }
    final = {
        'freeze_sha256': data.sha_file(freeze),
        'games': len(errors[0]),
        'scores': result,
        'paired_differences': intervals,
        'per_game_sha256': data.sha_file(output / 'game-errors.jsonl'),
    }
    _json(output / 'test.json', final)
    return final


def state_ids(cache: PackedGames) -> dict[bytes, int]:
    """キャッシュ中のD4同値状態と手番を段階別の群番号へ写す。"""
    return {
        sample.state_id: sample.bucket
        for index in range(len(cache.offsets))
        for sample in cache.game(index)[2]
    }


def test_without_shared_states(
    cache: Path,
    freeze: Path,
    train_cache: Path,
    validation_cache: Path,
    output: Path,
) -> None:
    """凍結後に共通サンプルの重複と、重複を除いたtestの誤差を記録する。"""
    checkpoint, _, record = _freeze_assets(freeze)
    expected = record['split_manifest_sha256']
    for name, path in (('train', train_cache), ('validation', validation_cache), ('test', cache)):
        info = _cache_manifest(path)
        if info['split_name'] != name or info['split_manifest_sha256'] != expected:
            raise ValueError('wrong_overlap_split')
    with open_cache(train_cache) as learned, open_cache(validation_cache) as valid:
        a, b = state_ids(learned), state_ids(valid)
    with open_cache(cache) as test:
        c = state_ids(test)
        excluded = set(a) | set(b)
        overlap = {
            name: [
                sum(key in right and value == bucket for key, value in left.items())
                for bucket in range(6)
            ]
            for name, left, right in (
                ('train_validation', a, b),
                ('test_train', c, a),
                ('test_validation', c, b),
                ('test_seen', c, excluded),
            )
        }
        model = ntuple_io.load_model(checkpoint).model
        constant = float(str(record['constant']))
        scores = {
            name: _score_by_group(test, weights, prediction, exclude=excluded)
            for name, weights, prediction in (
                ('candidate', model.weights, 0.0),
                ('zero', None, 0.0),
                ('constant', None, constant),
            )
        }
    _json(
        output,
        {
            'overlapping_unique_states_by_bucket': overlap,
            'unique_sampled_states': {'train': len(a), 'validation': len(b), 'test': len(c)},
            'scores_on_test_without_exact_d4_matches': scores,
        },
    )


def measure_inference(initial: Path, trained: Path, sample: data.State) -> dict[str, object]:
    """同一の状態・実プロセスで両モデルを交互に呼び、推論時間を測る。"""
    models = [ntuple_io.load_model(path) for path in (initial, trained)]
    result: dict[str, object] = {}
    for art in models:
        art.model.value(sample.rows, sample.turn)
    timings: list[list[float]] = [[], []]
    for trial in range(22):
        index = trial % 2
        model = models[index].model
        tick = perf_counter()
        for _ in range(200):
            model.value(sample.rows, sample.turn)
        timings[index].append((perf_counter() - tick) / 200)
    for name, art, times, path in zip(
        ('initial', 'trained'), models, timings, (initial, trained), strict=True
    ):
        result[name] = {
            'id': art.model_id,
            'bytes': path.stat().st_size,
            'seconds': times,
            'median_seconds': median(times),
        }
    return result


def main() -> int:
    """キャッシュ生成、学習、凍結、最終testを独立コマンドで実行する。"""
    cli = ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('dataset', type=Path)
    prep.add_argument('split', choices=data.SPLITS)
    prep.add_argument('output', type=Path)
    prep.add_argument('--limit', type=int)
    prep.add_argument('--freeze', type=Path)
    train = sub.add_parser('fit')
    for name in ('train', 'validation', 'initial', 'output'):
        train.add_argument(name, type=Path)
    train.add_argument('--rate', type=float, required=True)
    train.add_argument('--epochs', type=int, required=True)
    train.add_argument('--seed', type=int, default=_PRIMARY_SEED)
    train.add_argument('--constant', type=float, required=True)
    final = sub.add_parser('freeze')
    for name in ('path', 'initial', 'checkpoint', 'split_manifest'):
        final.add_argument(name, type=Path)
    final.add_argument('--constant', type=float, required=True)
    final.add_argument('--decision', required=True)
    target = sub.add_parser('test')
    for name in ('cache', 'freeze', 'output'):
        target.add_argument(name, type=Path)
    args = cli.parse_args()
    if args.command == 'prepare':
        prepare(args.dataset, args.split, args.output, args.limit, freeze=args.freeze)
    elif args.command == 'fit':
        fit(
            args.train,
            args.validation,
            args.initial,
            args.output,
            rate=args.rate,
            epochs=args.epochs,
            seed=args.seed,
            constant=args.constant,
        )
    elif args.command == 'freeze':
        freeze_candidate(
            args.path,
            args.initial,
            args.checkpoint,
            args.split_manifest,
            args.constant,
            args.decision,
        )
    else:
        evaluate_test(args.cache, args.freeze, args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
