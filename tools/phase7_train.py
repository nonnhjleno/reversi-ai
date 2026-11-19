"""Phase 7の局境界checkpoint、再開、自己対戦CLI。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, cast

from reversi_player import ntuple, ntuple_io
from tools import evidence, phase7_td as td
from tools.phase65_data import sha_file
from tools.phase65_train import peak_memory_bytes

ROOT = Path(__file__).resolve().parents[1]
_SOURCES = (
    'tools/phase7_td.py',
    'tools/phase7_train.py',
    'tools/evidence.py',
    'reversi_player/ai_engine.py',
    'reversi_player/ntuple.py',
    'reversi_player/ntuple_io.py',
    'reversi_player/reversi.py',
    'harness/referee.py',
)
_ALGORITHM = 'ranger-black-online-semi-gradient-td0-v1'


class Counts(NamedTuple):
    """ゲーム境界で確定した累積件数。"""

    games: int = 0
    updates: int = 0
    placements: int = 0
    passes: int = 0


@dataclass
class Progress:
    """再開時に復元する学習状態。"""

    model: ntuple.NtupleModel
    rng: random.Random
    counts: Counts
    parent: dict[str, object] | None


def _canonical(value: object) -> bytes:
    """比較とhash照合のためJSONの表現を固定する。"""
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')).encode('utf-8')


def _digest(value: object) -> str:
    """正規化したJSONのSHA-256を返す。"""
    return hashlib.sha256(_canonical(value)).hexdigest()


def _map(value: object, fields: set[str]) -> dict[str, object]:
    """既知のcheckpointオブジェクト形だけを受け入れる。"""
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('invalid_td_checkpoint_fields')
    return cast(dict[str, object], value)


def _counts(value: object) -> Counts:
    """累積カウンターの整合を検査して読む。"""
    record = _map(value, set(Counts._fields))
    numbers = [record[name] for name in Counts._fields]
    if any(type(n) is not int or n < 0 for n in numbers):
        raise ValueError('invalid_td_counts')
    counts = Counts(*cast(list[int], numbers))
    if (
        counts.updates != counts.placements + counts.passes
        or counts.games > counts.placements
        or counts.placements > counts.games * 60
        or counts.passes > counts.placements
    ):
        raise ValueError('inconsistent_td_counts')
    return counts


def _restore_rng(value: object) -> random.Random:
    """Python Randomの整数状態だけを通常のJSONから復元する。"""
    if (
        not isinstance(value, list)
        or len(value) != 3
        or type(value[0]) is not int
        or value[0] != 3
        or not isinstance(value[1], list)
        or len(value[1]) != 625
        or any(type(n) is not int or not 0 <= n < 2**32 for n in value[1])
        or not 0 <= value[1][-1] <= 624
        or not any(value[1][:-1])
        or value[2] is not None
    ):
        raise ValueError('invalid_td_rng_state')
    rng = random.Random(0)
    rng.setstate((3, tuple(value[1]), None))
    return rng


def sources() -> dict[str, str]:
    """学習に関連するソースの実bytesを識別する。"""
    return {name: sha_file(ROOT / name) for name in _SOURCES}


def _runtime() -> dict[str, str]:
    """同一条件の再開を判定するための実行環境を返す。"""
    return {
        'python': sys.version,
        'implementation': platform.python_implementation(),
        'system': platform.system(),
        'machine': platform.machine(),
        'libc': ' '.join(platform.libc_ver()),
    }


def _settings(config: td.Config) -> dict[str, object]:
    """学習設定、固定scheduleと初期盤面の規約を返す。"""
    return {
        **asdict(config),
        'algorithm': _ALGORITHM,
        'gamma': 1,
        'rate_schedule': 'constant',
        'epsilon_schedule': 'constant',
        'opening': 'initial_black',
    }


def _restore(directory: Path, compatibility: dict[str, object]) -> Progress:
    """完全に保存され、現行学習と一致するcheckpointを検証する。"""
    manifest = directory / 'state.json'
    with manifest.open('rb') as stream:
        raw = stream.read(2**20 + 1)
    if len(raw) > 2**20:
        raise ValueError('oversized_td_checkpoint')
    envelope = _map(json.loads(raw), {'payload', 'payload_sha256'})
    state = _map(
        envelope['payload'],
        {
            'format_version',
            'complete',
            'compatibility',
            'counts',
            'from_counts',
            'rng',
            'model',
            'segment',
            'previous',
            'saved_at_utc',
            'source_commit',
            'argv',
            'metrics',
        },
    )
    if (
        envelope['payload_sha256'] != _digest(state)
        or type(state['format_version']) is not int
        or state['format_version'] != 1
        or state['complete'] is not True
    ):
        raise ValueError('invalid_td_checkpoint')
    if _canonical(state['compatibility']) != _canonical(compatibility):
        raise ValueError('incompatible_td_checkpoint')
    count = _counts(state['counts'])
    before = _counts(state['from_counts'])
    if count.games <= before.games or any(new < old for new, old in zip(count, before)):
        raise ValueError('invalid_td_segment_counts')
    model_info = _map(state['model'], {'model_id', 'sha256', 'bytes'})
    artifact = ntuple_io.load_model(directory / 'model.json')
    if (
        artifact.model_id != model_info['model_id']
        or artifact.file_sha256 != model_info['sha256']
        or (directory / 'model.json').stat().st_size != model_info['bytes']
    ):
        raise ValueError('td_checkpoint_model_mismatch')
    segment = _map(state['segment'], {'sha256', 'bytes'})
    games = directory / 'games.jsonl'
    if games.stat().st_size != segment['bytes'] or sha_file(games) != segment['sha256']:
        raise ValueError('td_checkpoint_games_mismatch')
    return Progress(
        artifact.model,
        _restore_rng(state['rng']),
        count,
        {'games': count.games, 'model_id': artifact.model_id, 'state_sha256': sha_file(manifest)},
    )


def _weights_report(weights: list[float], initial: tuple[float, ...]) -> dict[str, object]:
    """重みと初期値との差を黒白のLUT別に要約する。"""
    half = len(weights) // 2
    return {
        turn: {
            'max_abs': max(map(abs, weights[i:j])),
            'l2': math.hypot(*weights[i:j]),
            'nonzero': sum(w != 0 for w in weights[i:j]),
            'delta_from_initial_l2': math.hypot(*(weights[k] - initial[k] for k in range(i, j))),
        }
        for turn, i, j in [('B', 0, half), ('W', half, len(weights))]
    }


def run(
    initial: Path,
    output: Path,
    config: td.Config,
    until: int,
    *,
    resume: Path | None = None,
) -> dict[str, object]:
    """既存資産を保護して自己対戦し、新しい完局checkpointを発行する。"""
    if type(until) is not int or not 0 < until <= 50000:
        raise ValueError('invalid_td_game_limit')
    if not initial.is_absolute():
        raise ValueError('td_requires_absolute_initial_path')
    initial_artifact = ntuple_io.load_model(initial)
    source = sources()
    settings = _settings(config)
    compatibility = {
        'initial': {
            'model_id': initial_artifact.model_id,
            'sha256': initial_artifact.file_sha256,
        },
        'settings': settings,
        'sources': source,
        'runtime': _runtime(),
    }
    progress = (
        _restore(resume, compatibility)
        if resume is not None
        else Progress(initial_artifact.model, random.Random(config.seed), Counts(), None)
    )
    before = progress.counts
    if until <= before.games:
        raise ValueError('td_game_limit_must_advance')
    code_commit = subprocess.check_output(
        ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True, timeout=10
    ).strip()
    output.mkdir()
    start = time.perf_counter()
    weights = list(progress.model.weights)
    diagnostic = td.Diagnostics()
    outcomes: Counter[str] = Counter()
    game_lengths: Counter[int] = Counter()
    margins: Counter[int] = Counter()
    placements, passes = before.placements, before.passes
    pass_games = 0
    games_path = output / 'games.jsonl'
    with games_path.open('xb') as stream:
        for game_index in range(before.games + 1, until + 1):
            game = td.play(weights, config, progress.rng, diagnostic)
            pass_games += bool(game.passes)
            passes += game.passes
            placements += len(game.moves) - game.passes
            result = 'B' if game.margin > 0 else 'W' if game.margin < 0 else 'D'
            outcomes[result] += 1
            game_lengths[len(game.moves)] += 1
            margins[game.margin] += 1
            stream.write(
                _canonical(
                    {
                        'game': game_index,
                        'moves': game.moves,
                        'margin': game.margin,
                        'result': result,
                        'passes': game.passes,
                    }
                )
                + b'\n'
            )
        stream.flush()
        os.fsync(stream.fileno())
    elapsed = time.perf_counter() - start
    count = Counts(until, placements + passes, placements, passes)
    if diagnostic.observations != count.updates - before.updates:
        raise ValueError('td_segment_update_count_mismatch')
    if sources() != source or sha_file(initial) != initial_artifact.file_sha256:
        raise ValueError('td_source_or_initial_changed')
    provenance = {
        'algorithm': _ALGORITHM,
        'initial_model_id': initial_artifact.model_id,
        'initial_sha256': initial_artifact.file_sha256,
        'settings_sha256': _digest(settings),
        'games': str(until),
        'seed': str(config.seed),
        'training_source_sha256': _digest(source),
    }
    model_path = output / 'model.json'
    save_start = time.perf_counter()
    model_id = ntuple_io.save_model(
        model_path, ntuple.NtupleModel(tuple(weights)), provenance=provenance
    )
    save_seconds = time.perf_counter() - save_start
    load_start = time.perf_counter()
    artifact = ntuple_io.load_model(model_path)
    load_seconds = time.perf_counter() - load_start
    if artifact.model_id != model_id or artifact.model.weights != tuple(weights):
        raise ValueError('td_checkpoint_roundtrip')
    metrics = {
        'seconds': elapsed,
        'games': until - before.games,
        'games_per_second': (until - before.games) / elapsed,
        'peak_memory_bytes': peak_memory_bytes(),
        'outcomes': outcomes,
        'margins': {str(k): v for k, v in margins.items()},
        'game_lengths': {str(k): v for k, v in game_lengths.items()},
        'pass_games': pass_games,
        'td': vars(diagnostic),
        'weights': _weights_report(weights, initial_artifact.model.weights),
        'saturation_rate': diagnostic.saturated / diagnostic.observations,
        'model_save_seconds': save_seconds,
        'model_load_seconds': load_seconds,
        'finite': True,
        'referee': 'every_game',
    }
    checkpoint: dict[str, object] = {
        'format_version': 1,
        'complete': True,
        'compatibility': compatibility,
        'from_counts': before._asdict(),
        'counts': count._asdict(),
        'rng': progress.rng.getstate(),
        'model': {
            'model_id': model_id,
            'sha256': artifact.file_sha256,
            'bytes': model_path.stat().st_size,
        },
        'segment': {'sha256': sha_file(games_path), 'bytes': games_path.stat().st_size},
        'previous': progress.parent,
        'saved_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_commit': code_commit,
        'argv': [sys.executable, *sys.orig_argv[1:]],
        'metrics': metrics,
    }
    evidence.save_new(
        output / 'state.json',
        evidence.encoded_json({'payload': checkpoint, 'payload_sha256': _digest(checkpoint)}),
    )
    return checkpoint


def main() -> int:
    """1区間を新規保存する。失敗は非ゼロ終了として観測できる。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('initial', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--until', type=int, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--rate', type=float, required=True)
    parser.add_argument('--epsilon', type=float, required=True)
    parser.add_argument('--resume', type=Path)
    args = parser.parse_args()
    checkpoint = run(
        args.initial,
        args.output,
        td.Config(args.rate, args.epsilon, args.seed),
        args.until,
        resume=args.resume,
    )
    print(evidence.encoded_json({key: checkpoint[key] for key in ('counts', 'model', 'metrics')}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
