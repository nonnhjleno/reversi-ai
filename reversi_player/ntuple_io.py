"""n-tuple資産の互換性、モデルIDと保存内容を検証する。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from reversi_player import ntuple
elif __package__:
    from . import ntuple
else:
    import ntuple


_FORMAT_VERSION = 1
_MAX_FILE_BYTES = 1024 * 1024


class ModelFormatError(ValueError):
    """資産の形式、モデル定義、重み、またはhashが不正な場合のエラー。"""


@dataclass(frozen=True)
class ModelArtifact:
    """検証済みのモデル、意味上のID、ファイル実bytesのhashと来歴。"""

    model: ntuple.NtupleModel
    model_id: str
    file_sha256: str
    provenance: tuple[tuple[str, str], ...]


def _definition() -> dict[str, object]:
    """version 1の完全な互換性判定に使う定義を返す。"""
    return {
        'board': {'size': 8, 'coordinates': 'xy_zero_based', 'rows': 'rows[y][x]'},
        'encoding': {'E': 0, 'B': 1, 'W': 2, 'first_digit': 'most_significant'},
        'tuples': [[list(position) for position in line] for line in ntuple.TUPLES],
        'symmetry_order': list(ntuple.SYMMETRIES),
        'symmetries': {
            'identity': ['x', 'y'],
            'rotate90': ['7-y', 'x'],
            'rotate180': ['7-x', '7-y'],
            'rotate270': ['y', '7-x'],
            'flip_x': ['7-x', 'y'],
            'flip_y': ['x', '7-y'],
            'diagonal': ['y', 'x'],
            'antidiagonal': ['7-y', '7-x'],
        },
        'lut_sizes': list(ntuple.LUT_SIZES),
        'turn_order': ['B', 'W'],
        'weight_order': ['turn', 'tuple', 'pattern'],
        'reference_count': ntuple.REFERENCE_COUNT,
        'aggregation': 'sum_with_multiplicity',
        'viewpoint': 'B',
        'activation': 'tanh',
        'weight_type': 'binary64',
        'max_abs_weight': ntuple.MAX_WEIGHT,
    }


def _json_bytes(value: object) -> bytes:
    """hashと保存で共用するJSONの正規化。"""
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('ascii')


def _sha256(value: bytes) -> str:
    """bytesのSHA-256を小文字hexで返す。"""
    return hashlib.sha256(value).hexdigest()


def _model_id(definition: dict[str, object], model: ntuple.NtupleModel) -> str:
    """来歴やファイルの空白から独立した、定義と重みのIDを返す。"""
    return 'sha256:' + _sha256(_json_bytes({'definition': definition, 'weights': model.weights}))


def _require_object(value: object, fields: set[str], error: str) -> dict[str, object]:
    """余分なキーや欠落のあるJSON objectを拒否する。"""
    if not isinstance(value, dict) or value.keys() != fields:
        raise ModelFormatError(error)
    return value


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """JSON decoderが重複キーを上書きする前に拒否する。"""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ModelFormatError('duplicate_json_key: ' + key)
        result[key] = value
    return result


def _reject_constant(token: str) -> object:
    """JSONの非標準の非有限値を拒否する。"""
    raise ModelFormatError('nonfinite_json_number: ' + token)


def _provenance(value: object) -> dict[str, str]:
    """学習条件等の拡張可能な文字列辞書を検証する。"""
    if not isinstance(value, dict) or any(
        type(key) is not str or not key or type(item) is not str for key, item in value.items()
    ):
        raise ModelFormatError('invalid_model_provenance')
    return value


def load_model(path: Path) -> ModelArtifact:
    """資産を検証して全体を返す。不正データはModelFormatError、I/O失敗はOSError。"""
    if not stat.S_ISREG(path.stat().st_mode):
        raise OSError('model_path_not_regular_file: ' + str(path))
    with path.open('rb') as stream:
        raw = stream.read(_MAX_FILE_BYTES + 1)
    if len(raw) > _MAX_FILE_BYTES:
        raise ModelFormatError('model_file_too_large')
    try:
        parsed = json.loads(
            raw.decode('utf-8'), object_pairs_hook=_unique_keys, parse_constant=_reject_constant
        )
        envelope = _require_object(parsed, {'payload', 'payload_sha256'}, 'invalid_model_envelope')
        payload = _require_object(envelope['payload'], {'manifest', 'weights'}, 'invalid_payload')
        manifest = _require_object(
            payload['manifest'],
            {
                'format_version',
                'definition',
                'model_id',
                'weight_count',
                'binary64_bytes',
                'provenance',
            },
            'invalid_model_manifest',
        )
        if (
            type(manifest['format_version']) is not int
            or manifest['format_version'] != _FORMAT_VERSION
        ):
            raise ModelFormatError('unsupported_model_format')
        definition = _definition()
        if _json_bytes(manifest['definition']) != _json_bytes(definition):
            raise ModelFormatError('incompatible_model_definition')
        count = ntuple.WEIGHT_COUNT
        if (
            type(manifest['weight_count']) is not int
            or manifest['weight_count'] != count
            or type(manifest['binary64_bytes']) is not int
            or manifest['binary64_bytes'] != 8 * count
        ):
            raise ModelFormatError('invalid_manifest_capacity')
        provenance = _provenance(manifest['provenance'])
        weights = payload['weights']
        if not isinstance(weights, list) or len(weights) != count:
            raise ModelFormatError('invalid_weight_count')
        if any(type(weight) is not float for weight in weights):
            raise ModelFormatError('invalid_weight_type')
        try:
            model = ntuple.NtupleModel(tuple(weights))
        except ValueError as failure:
            raise ModelFormatError('invalid_weight') from failure
        model_id = _model_id(definition, model)
        if manifest['model_id'] != model_id:
            raise ModelFormatError('model_id_mismatch')
        if envelope['payload_sha256'] != _sha256(_json_bytes(payload)):
            raise ModelFormatError('payload_hash_mismatch')
    except ModelFormatError:
        raise
    except (ValueError, RecursionError) as failure:
        raise ModelFormatError('invalid_model_json: ' + str(failure)) from failure
    return ModelArtifact(model, model_id, _sha256(raw), tuple(sorted(provenance.items())))


def save_model(
    path: Path, model: ntuple.NtupleModel, *, provenance: Mapping[str, str] | None = None
) -> str:
    """同一ディレクトリの一時ファイルから新規pathへ公開し、モデルIDを返す。"""
    metadata = _provenance(dict(provenance) if provenance is not None else {})
    definition = _definition()
    model_id = _model_id(definition, model)
    payload = {
        'manifest': {
            'format_version': _FORMAT_VERSION,
            'definition': definition,
            'model_id': model_id,
            'weight_count': ntuple.WEIGHT_COUNT,
            'binary64_bytes': 8 * ntuple.WEIGHT_COUNT,
            'provenance': metadata,
        },
        'weights': model.weights,
    }
    raw = _json_bytes({'payload': payload, 'payload_sha256': _sha256(_json_bytes(payload))}) + b'\n'
    if len(raw) > _MAX_FILE_BYTES:
        raise ModelFormatError('model_file_too_large')
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix='.ntuple-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # 同時に同名へ保存する処理があっても、先に公開したモデルを保持する。
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink()
    return model_id
