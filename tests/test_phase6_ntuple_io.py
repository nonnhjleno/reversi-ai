"""モデルファイルの互換性、保存の安全性、破損時の診断を検証する。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
from pathlib import Path
import struct

import pytest

from harness.referee import INITIAL_ROWS
from reversi_player import ntuple, ntuple_io


def _canonical(value):
    """公開規約に従って、破損fixtureでも必要に応じて整合するSHA-256を作る。"""
    return json.dumps(value, separators=(',', ':'), sort_keys=True, ensure_ascii=True).encode()


@pytest.fixture
def model_file(tmp_path):
    """0、負の0、正負の非ゼロとbinary64の境界付近を含む試験用資産。"""
    weights = [0.0] * 1872
    weights[:4] = [0.25, -0.0, 5e-324, 2**1000]
    weights[-1] = -0.125
    model = ntuple.NtupleModel(tuple(weights))
    path = tmp_path / 'fixture.json'
    ntuple_io.save_model(path, model, provenance={'kind': 'hand_fixture', 'memo': '手計算'})
    return path, model


def test_roundtrip_reproducibility_and_independent_hash(model_file, tmp_path):
    path, model = model_file
    first = ntuple_io.load_model(path)
    second = ntuple_io.load_model(path)
    assert first == second
    assert first.model is not second.model and first.model is not model
    assert first.model.weights is not second.model.weights
    weights_bytes = struct.Struct('<1872d')
    assert weights_bytes.pack(*model.weights) == weights_bytes.pack(*first.model.weights)
    assert first.file_sha256 == sha256(path.read_bytes()).hexdigest()
    raw = json.loads(path.read_bytes())
    payload = raw['payload']
    manifest = payload['manifest']
    assert raw['payload_sha256'] == sha256(_canonical(payload)).hexdigest()
    assert manifest['format_version'] == 1
    assert (manifest['weight_count'], manifest['binary64_bytes']) == (1872, 14976)
    assert manifest['definition']['tuples'][0] == [[0, 0], [0, 1]]
    assert manifest['definition']['turn_order'] == ['B', 'W']
    assert manifest['definition']['lut_sizes'] == [9] * 32 + [27] * 24
    meaning = {'definition': manifest['definition'], 'weights': payload['weights']}
    assert first.model_id == 'sha256:' + sha256(_canonical(meaning)).hexdigest()
    assert first.model_id == manifest['model_id']
    for turn in 'BW':
        assert first.model.features(INITIAL_ROWS, turn) == model.features(INITIAL_ROWS, turn)
        assert first.model.value(INITIAL_ROWS, turn) == model.value(INITIAL_ROWS, turn)
    rendered_again = tmp_path / 'same.json'
    assert (
        ntuple_io.save_model(rendered_again, first.model, provenance=dict(first.provenance))
        == first.model_id
    )
    assert rendered_again.read_bytes() == path.read_bytes()
    with pytest.raises(FrozenInstanceError):
        first.model.weights = ()
    assert first.model.weights[0] == 0.25


def test_metadata_varies_independently_and_weights_change_model_id(model_file, tmp_path):
    path, model = model_file
    original = ntuple_io.load_model(path)
    next_path = tmp_path / 'future.json'
    updates = {'training_method': 'supervised', 'dataset_sha256': 'digest', 'seed': '17'}
    ntuple_io.save_model(next_path, model, provenance=updates)
    future = ntuple_io.load_model(next_path)
    assert future.model_id == original.model_id
    assert future.file_sha256 != original.file_sha256
    assert dict(future.provenance) == updates
    for turn in 'BW':
        assert future.model.features(INITIAL_ROWS, turn) == original.model.features(
            INITIAL_ROWS, turn
        )
        assert future.model.value(INITIAL_ROWS, turn) == original.model.value(INITIAL_ROWS, turn)
    zero_path = tmp_path / 'zero.json'
    zero_id = ntuple_io.save_model(
        zero_path, ntuple.NtupleModel.zeros(), provenance={'kind': 'untrained_zero'}
    )
    zero = ntuple_io.load_model(zero_path)
    assert zero.model_id == zero_id != future.model_id
    assert zero.model.logit(INITIAL_ROWS, 'B') == zero.model.logit(INITIAL_ROWS, 'W') == 0.0


@pytest.mark.parametrize(
    'value, error',
    [
        (b'', 'invalid_model_json'),
        (b'{', 'invalid_model_json'),
        (b'\xff', 'invalid_model_json'),
        (b'{}', 'invalid_model_envelope'),
        (b'[]', 'invalid_model_envelope'),
        (b'{"payload":{},"payload":{},"payload_sha256":""}', 'duplicate_json_key'),
        (b'{"payload":{},"payload_sha256":""}', 'invalid_payload'),
        (b'[' * 2000, 'invalid_model_json'),
        (b' ' * 1048577, 'model_file_too_large'),
    ],
    ids=[
        'empty',
        'truncated',
        'utf8',
        'missing',
        'array',
        'duplicate',
        'payload',
        'nested',
        'oversized',
    ],
)
def test_rejects_malformed_file(tmp_path, value, error):
    path = tmp_path / 'bad.json'
    path.write_bytes(value)
    with pytest.raises(ntuple_io.ModelFormatError, match=error):
        ntuple_io.load_model(path)


@pytest.mark.parametrize(
    'location,new_value,diagnostic',
    [
        (('manifest', 'format_version'), 2, 'unsupported_model_format'),
        (('manifest', 'format_version'), True, 'unsupported_model_format'),
        (('manifest', 'definition', 'turn_order'), ['W', 'B'], 'incompatible_model_definition'),
        (('manifest', 'definition', 'encoding', 'B'), True, 'incompatible_model_definition'),
        (
            ('manifest', 'definition', 'tuples', 0),
            [[0, 1], [0, 0]],
            'incompatible_model_definition',
        ),
        (('manifest', 'definition', 'activation'), 'linear', 'incompatible_model_definition'),
        (('manifest', 'weight_count'), 1873, 'invalid_manifest_capacity'),
        (('manifest', 'binary64_bytes'), 14977, 'invalid_manifest_capacity'),
        (('manifest', 'provenance'), {'seed': 3}, 'invalid_model_provenance'),
        (('manifest', 'provenance'), {'': 'bad'}, 'invalid_model_provenance'),
        (('manifest', 'model_id'), 'sha256:bad', 'model_id_mismatch'),
        (('weights',), [0.0] * 1871, 'invalid_weight_count'),
        (('weights',), [0.0] * 1873, 'invalid_weight_count'),
        (('weights', 0), '-2', 'invalid_weight_type'),
        (('weights', 0), 0, 'invalid_weight_type'),
        (('weights', 0), False, 'invalid_weight_type'),
        (('weights', 0), -1.25, 'model_id_mismatch'),
        (('weights', 0), 2.0**1001, 'invalid_weight'),
    ],
)
def test_rejects_noncompatible_model_even_with_consistent_hash(
    model_file, tmp_path, location, new_value, diagnostic
):
    original, model = model_file
    envelope = json.loads(original.read_bytes())
    payload = envelope['payload']
    target = payload
    for key in location[:-1]:
        target = target[key]
    target[location[-1]] = new_value
    envelope['payload_sha256'] = sha256(_canonical(payload)).hexdigest()
    bad = tmp_path / 'bad.json'
    bad.write_bytes(_canonical(envelope))
    with pytest.raises(ntuple_io.ModelFormatError, match=diagnostic):
        ntuple_io.load_model(bad)
    assert ntuple_io.load_model(original).model == model


@pytest.mark.parametrize(
    'token,diagnostic',
    [
        (b'NaN', 'nonfinite_json_number'),
        (b'Infinity', 'nonfinite_json_number'),
        (b'-Infinity', 'nonfinite_json_number'),
        (b'1e400', 'invalid_weight'),
    ],
)
def test_rejects_nonfinite_serialized_weight(model_file, tmp_path, token, diagnostic):
    path, _ = model_file
    data = path.read_bytes().replace(b'"weights":[0.25,', b'"weights":[' + token + b',', 1)
    broken = tmp_path / 'nonfinite.json'
    broken.write_bytes(data)
    with pytest.raises(ntuple_io.ModelFormatError, match=diagnostic):
        ntuple_io.load_model(broken)


def test_rejects_missing_manifest_truncation_and_hash_errors(model_file, tmp_path):
    path, _ = model_file
    raw = path.read_bytes()
    obj = json.loads(raw)
    obj['payload']['manifest']['provenance']['memo'] = 'tampered'
    broken = tmp_path / 'broken.json'
    for data, error in [
        (raw[:-19], 'invalid_model_json'),
        (_canonical(obj), 'payload_hash_mismatch'),
    ]:
        broken.write_bytes(data)
        with pytest.raises(ntuple_io.ModelFormatError, match=error):
            ntuple_io.load_model(broken)
    obj['payload'].pop('manifest')
    broken.write_bytes(_canonical(obj))
    with pytest.raises(ntuple_io.ModelFormatError, match='invalid_payload'):
        ntuple_io.load_model(broken)


def test_paths_and_interrupted_read_write_preserve_existing_model(
    model_file, tmp_path, monkeypatch
):
    original, model = model_file
    data = original.read_bytes()
    missing = tmp_path / 'missing.json'
    with pytest.raises(FileNotFoundError):
        ntuple_io.load_model(missing)
    with pytest.raises(OSError, match='not_regular'):
        ntuple_io.load_model(tmp_path)
    with pytest.raises(FileNotFoundError):
        ntuple_io.save_model(tmp_path / 'missing_dir' / 'model.json', model)
    with pytest.raises(FileExistsError):
        ntuple_io.save_model(original, model)
    assert original.read_bytes() == data

    class InterruptedRead:
        """部分データを読んだ後のI/O障害を表す。"""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            assert size > 0
            assert data[:5]
            raise PermissionError('injected read interruption')

    def fail(*args):
        """一時ファイル書込み後のflush失敗を再現する。"""
        raise PermissionError('injected sync failure')

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, 'open', lambda *args: InterruptedRead())
        with pytest.raises(PermissionError, match='read interruption'):
            ntuple_io.load_model(original)
    with monkeypatch.context() as scoped:
        scoped.setattr(ntuple_io.os, 'fsync', fail)
        with pytest.raises(PermissionError, match='sync failure'):
            ntuple_io.save_model(missing, model)
    assert original.read_bytes() == data
    assert not missing.exists()
    assert list(tmp_path.glob('.ntuple-*')) == []


def test_concurrent_publish_same_name_never_clobbers(tmp_path):
    path = tmp_path / 'same.json'
    models = (ntuple.NtupleModel.zeros(), ntuple.NtupleModel((0.125,) * 1872))

    def publish(model):
        """1本の成功ID、または先着による重複を返す。"""
        try:
            return ntuple_io.save_model(path, model)
        except FileExistsError:
            return None

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(publish, models))
    assert outcomes.count(None) == 1
    winner = ntuple_io.load_model(path)
    index = 0 if outcomes[0] is not None else 1
    assert winner.model_id == outcomes[index] and winner.model == models[index]
    assert list(tmp_path.glob('.ntuple-*')) == []
