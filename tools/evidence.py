"""JUnitと逐次journalから開発証拠を集約する。評価資格を発行せず原ファイルを変更しない。"""

import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


def mapping(value: object) -> dict[str, object]:
    """JSON境界の文字列キー辞書を取り出す。呼出側で必須値の有無を検査する。"""
    return (
        {key: item for key, item in value.items() if isinstance(key, str)}
        if isinstance(value, dict)
        else {}
    )


def _items(value: object) -> list[object]:
    """JSON配列を取り出す。未取得は空配列で表し、成功の証拠にはしない。"""
    return list(value) if isinstance(value, list) else []


def utc_now() -> str:
    """表示用UTC日時を返す。harnessの親子時計比較には使わない。"""
    return datetime.now(timezone.utc).isoformat()


def encoded_json(value: object) -> str:
    """派生証拠・品質publicationのUTF-8 JSON表現を固定する。"""
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def save_new(path: Path, text: str) -> None:
    """owned pendingをfsync/closeして排他link公開する。失敗時は完成名を作らない。"""
    if path.exists():
        raise FileExistsError('evidence output already exists')
    pending = path.with_name(path.name + '.pending')
    owned = False
    try:
        with pending.open('x', encoding='utf-8', newline='\n') as stream:
            owned = True
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(pending, path)
    finally:
        if owned:
            try:
                pending.unlink()
            except OSError:
                print('EVIDENCE WARNING: owned pending cleanup failed', file=sys.stderr)


def manifest(
    root: Path, output: Path, source_hashes: dict[str, str], plans: list[dict[str, object]]
) -> dict[str, object]:
    """Gitを読み取り専用で照会し、共通実行条件を作る。秘密環境は収集しない。"""
    git: dict[str, object] = {'status': 'unknown', 'commit': None, 'dirty': None}
    try:
        environment = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}
        commit = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        status = subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=10,
            check=True,
        )
        git.update(status='available', commit=commit.stdout.strip(), dirty=bool(status.stdout))
    except (OSError, subprocess.SubprocessError):
        git['reason'] = 'git_read_unavailable'
    return {
        'run_id': output.name,
        'started_at_utc': utc_now(),
        'git': git,
        'source_hashes': source_hashes,
        'steps': plans,
        'reproduce_gate': 'python -B tools/quality.py',
        'model_contract_hashes': {
            name: value for name, value in source_hashes.items() if name.startswith('docs/')
        },
        'limitations': [
            '任意local/assert自動抽出なし',
            '全Hypothesis成功例なし',
            '全AI stderrなし',
            '強制停止・停電時の完全保存なし',
            '原証拠の真正性・正式資格の証明ではない',
        ],
    }


def _read(path: Path, limit: int = 32_000_000) -> bytes:
    """容量を制限して既存ファイルを読む。欠損・巨大ファイルは不完全扱いにする。"""
    if path.stat().st_size > limit:
        raise ValueError('artifact_size_limit')
    return path.read_bytes()


def _diagnostic(text: str | None) -> str | None:
    """派生診断を短縮し典型的な秘密代入を伏せる。原ログの安全性を保証しない。"""
    if text is None:
        return None
    return re.sub(
        r'(?i)((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[^\s,;]+', r'\1<redacted>', text
    )[:8192]


def _junit_key(nodeid: str) -> tuple[str, str]:
    """通常pytest nodeidをJUnitのclass/nameへ対応させる。値を推測して補完しない。"""
    address, separator, parameters = nodeid.partition('[')
    parts = address.split('::')
    parts[0] = parts[0].replace('/', '.').replace('\\', '.')
    if parts[0].endswith('.py'):
        parts[0] = parts[0][:-3]
    return '.'.join(parts[:-1]), parts[-1] + (separator + parameters if separator else '')


def _outcome(phases: list[dict[str, object]], finished: bool) -> str:
    """pytestの段階結果を最終結果へ分類する。teardown失敗をcall成功で隠さない。"""
    if not finished:
        return 'interrupted'
    if any(item.get('outcome') == 'failed' and item.get('when') != 'call' for item in phases):
        return 'error'
    if any(item.get('outcome') == 'failed' for item in phases):
        return 'failed'
    if any(item.get('outcome') == 'skipped' for item in phases):
        return 'skipped'
    if {item.get('when') for item in phases} == {'setup', 'call', 'teardown'}:
        return 'passed'
    return 'interrupted'


def _journal_errors(events: list[dict[str, object]]) -> list[str]:
    """直列journal v1の区間と端点を検査する。原イベントやpytest結果は修復しない。"""
    errors: list[str] = []
    kinds = {
        'session_start',
        'session_finish',
        'collection',
        'selected',
        'test_start',
        'phase',
        'test_finish',
        'expectation',
        'observation',
        'artifact',
    }
    started = False
    ended = False
    selected = False
    selection: set[str] = set()
    started_tests: set[str] = set()
    finished_tests: set[str] = set()
    active: str | None = None
    phases: list[str] = []
    setup_outcome: object = None

    def reject(reason: str, index: int) -> None:
        """元journalの行へ対応する理由を残し、他の診断収集も継続する。"""
        errors.append(f'journal_lifecycle:{reason}:{index}')

    for index, event in enumerate(events):
        if type(event.get('format_version')) is not int or event['format_version'] != 1:
            reject('unsupported_journal_version', index)
        kind = event.get('kind')
        nodeid = event.get('nodeid')
        if not isinstance(kind, str) or kind not in kinds:
            reject('unknown_event_kind', index)
            continue
        if ended:
            reject('event_outside_session', index)
        if kind == 'session_start':
            if started:
                reject('duplicate_session_start', index)
            if index != 0:
                reject('session_start_out_of_order', index)
            if nodeid is not None:
                reject('session_nodeid_invalid', index)
            started = True
            continue
        if not started:
            reject('event_outside_session', index)
        if kind == 'session_finish':
            if ended:
                reject('duplicate_session_finish', index)
            if index != len(events) - 1:
                reject('session_finish_before_end', index)
            if nodeid is not None:
                reject('session_nodeid_invalid', index)
            if active is not None:
                reject('test_unfinished_at_session_end', index)
            ended = True
            continue
        if kind in ('collection', 'selected'):
            if nodeid is not None:
                reject('session_nodeid_invalid', index)
            if kind == 'collection':
                if selected or started_tests:
                    reject('collection_after_selection', index)
            else:
                if selected:
                    reject('duplicate_selected', index)
                if started_tests:
                    reject('selection_after_test_start', index)
                raw = event.get('nodeids')
                nodeids = [item for item in _items(raw) if isinstance(item, str) and item]
                if (
                    not isinstance(raw, list)
                    or len(raw) != len(nodeids)
                    or len(nodeids) != len(set(nodeids))
                ):
                    reject('invalid_selected_nodeids', index)
                selection = set(nodeids)
                selected = True
            continue
        if not isinstance(nodeid, str) or not nodeid:
            reject('event_outside_active_test', index)
            continue
        if kind == 'test_start':
            if not selected:
                reject('test_before_selection', index)
            if nodeid not in selection:
                reject('test_not_selected', index)
            if active is not None:
                reject('overlapping_test_start', index)
            if nodeid in started_tests:
                reject('duplicate_test_start', index)
            started_tests.add(nodeid)
            active = nodeid
            phases = []
            setup_outcome = None
            continue
        if active != nodeid:
            reject('event_outside_active_test', index)
        if kind == 'test_finish':
            if nodeid in finished_tests:
                reject('duplicate_test_finish', index)
            if phases != ['setup', 'call', 'teardown'] and not (
                phases == ['setup', 'teardown'] and setup_outcome in ('failed', 'skipped')
            ):
                reject('test_finish_before_phases', index)
            finished_tests.add(nodeid)
            if active == nodeid:
                active = None
        elif kind == 'phase' and active == nodeid:
            # phaseは処理後の通知なので、fixtureの明示記録をphase報告前にも許可する。
            when = event.get('when')
            phases.append(when if isinstance(when, str) else '')
            if when == 'setup':
                setup_outcome = event.get('outcome')
            if phases not in (
                ['setup'],
                ['setup', 'call'],
                ['setup', 'teardown'],
                ['setup', 'call', 'teardown'],
            ):
                reject('phase_order_invalid', index)
    if not started or not ended:
        errors.append('journal_lifecycle:session_incomplete')
    if not selected:
        errors.append('journal_lifecycle:selection_missing')
    if active is not None:
        errors.append('journal_lifecycle:test_unfinished')
    return errors


def collect_step(output: Path, run_id: str, step: dict[str, object]) -> dict[str, object]:
    """一stepの元終了値・JUnit・journal・原参照を照合し、欠損も返却する。"""
    name = str(step['name'])
    result: dict[str, object] = {
        'step_id': run_id + ':' + name,
        'name': name,
        'exit_code': step.get('exit_code'),
        'execution_error': step.get('error'),
        'command': step.get('command'),
        'log': step.get('log'),
        'collection_status': 'complete',
        'tests': [],
        'errors': [],
        'artifacts': [],
    }
    errors: list[str] = []
    tests: dict[str, dict[str, object]] = {}
    artifacts: list[dict[str, object]] = []
    if step.get('seconds') is None:
        result.update(collection_status='not_executed', missing_information=['pytest未実行'])
        return result
    directory = output / name
    events: list[dict[str, object]] = []
    cases: dict[tuple[str, str], list[ElementTree.Element]] = {}
    files: list[dict[str, object]] = []
    try:
        log_bytes = _read(output / str(step['log']))
        files.append({'path': str(step['log']), 'sha256': hashlib.sha256(log_bytes).hexdigest()})
        if b'EVIDENCE ERROR:' in log_bytes:
            errors.append('plugin_save_error_in_log')
    except (OSError, ValueError, KeyError):
        errors.append('step_log_unavailable')
    try:
        raw = _read(directory / 'events.jsonl')
        files.append({'path': name + '/events.jsonl', 'sha256': hashlib.sha256(raw).hexdigest()})
        for index, line in enumerate(raw.decode('utf-8').splitlines()):
            event = mapping(json.loads(line))
            if (
                event.get('run_id') != run_id
                or event.get('step') != name
                or type(event.get('sequence')) is not int
                or event['sequence'] != index
            ):
                raise ValueError('journal_identity_or_sequence_mismatch')
            events.append(event)
        if (
            not events
            or events[0].get('kind') != 'session_start'
            or events[-1].get('kind') != 'session_finish'
        ):
            errors.append('journal_session_incomplete')
        elif events[-1].get('exit_code') != step.get('exit_code') or events[-1].get(
            'capture_errors'
        ):
            errors.append('journal_exit_or_capture_mismatch')
    except (OSError, ValueError, UnicodeError) as failure:
        errors.append('journal_unavailable:' + type(failure).__name__ + ':' + str(failure))
    errors.extend(_journal_errors(events))
    selected: list[str] = []
    collection: list[dict[str, object]] = []
    for event in events:
        kind = event.get('kind')
        nodeid = event.get('nodeid')
        if kind == 'selected':
            selected = [item for item in _items(event.get('nodeids')) if isinstance(item, str)]
        elif kind == 'collection':
            collection.append(event)
        elif isinstance(nodeid, str):
            if kind == 'test_start':
                if nodeid in tests:
                    errors.append('duplicate_test_start:' + nodeid)
                tests[nodeid] = {
                    'test_id': nodeid,
                    'execution_id': run_id + ':' + name + ':' + nodeid,
                    'important': event.get('important', False),
                    'phases': [],
                    'checks': [],
                    'observations': [],
                    'finished': False,
                    'artifact_refs': [],
                    'reproduce': [
                        'python',
                        '-B',
                        '-m',
                        'pytest',
                        '-q',
                        nodeid,
                        '-p',
                        'no:cacheprovider',
                    ],
                }
            elif nodeid not in tests:
                errors.append('event_without_test_start:' + nodeid)
            elif kind == 'phase':
                phase = {
                    key: event.get(key)
                    for key in ('when', 'outcome', 'duration', 'diagnostic_truncated')
                }
                phase['diagnostic'] = (
                    _diagnostic(str(event['diagnostic'])) if event.get('diagnostic') else None
                )
                _items_append(tests[nodeid], 'phases', phase)
            elif kind == 'test_finish':
                tests[nodeid]['finished'] = True
            elif kind == 'expectation':
                _items_append(
                    tests[nodeid],
                    'checks',
                    {
                        key: value
                        for key, value in event.items()
                        if key not in ('format_version', 'run_id', 'step', 'kind', 'nodeid')
                    },
                )
            elif kind == 'observation':
                expectations = [
                    mapping(check).get('check_id') for check in _items(tests[nodeid]['checks'])
                ]
                if event.get('check_id') not in expectations:
                    errors.append('observation_before_expectation:' + nodeid)
                _items_append(
                    tests[nodeid],
                    'observations',
                    {
                        key: value
                        for key, value in event.items()
                        if key not in ('format_version', 'run_id', 'step', 'kind', 'nodeid')
                    },
                )
            elif kind == 'artifact':
                artifact = {
                    key: event.get(key)
                    for key in ('path', 'sha256', 'artifact_kind', 'source_run_id', 'source_schema')
                }
                try:
                    artifact_path = Path(str(event['path']))
                    data = _read(artifact_path)
                    if hashlib.sha256(data).hexdigest() != event.get('sha256'):
                        raise ValueError('artifact_hash_mismatch')
                    if event.get('artifact_kind') == 'checkpoint':
                        source = mapping(json.loads(data))
                        if source.get('run_id') != event.get('source_run_id') or source.get(
                            'schema_version'
                        ) != event.get('source_schema'):
                            raise ValueError('artifact_run_mismatch')
                    if event.get('artifact_kind') == 'trace':
                        lines = data.decode('utf-8').splitlines()
                        if not lines:
                            raise ValueError('trace_header_missing')
                        first = mapping(json.loads(lines[0]))
                        if first.get('run_id') != event.get('source_run_id'):
                            raise ValueError('trace_run_mismatch')
                    artifact['reference_status'] = 'matched_bytes_not_formal_validation'
                except (OSError, ValueError) as failure:
                    artifact.update(reference_status='unavailable', reason=str(failure))
                    errors.append('artifact_correspondence_failed:' + nodeid)
                index = len(artifacts)
                artifacts.append(artifact)
                _items_append(tests[nodeid], 'artifact_refs', index)
    if len(selected) != len(set(str(item) for item in selected)) or set(selected) != set(tests):
        errors.append('selected_test_coverage_mismatch')
    try:
        xml_bytes = _read(directory / (name + '.xml'))
        files.append(
            {'path': name + '/' + name + '.xml', 'sha256': hashlib.sha256(xml_bytes).hexdigest()}
        )
        if b'<!DOCTYPE' in xml_bytes or b'<!ENTITY' in xml_bytes:
            raise ValueError('xml_entities_not_supported')
        xml = ElementTree.fromstring(xml_bytes)
        if xml.tag not in ('testsuites', 'testsuite'):
            raise ValueError('junit_root_invalid')
        for case in xml.iter('testcase'):
            cases.setdefault((case.get('classname', ''), case.get('name', '')), []).append(case)
        declared = sum(int(suite.get('tests', '-1')) for suite in xml.iter('testsuite'))
        phase_records = [
            mapping(phase) for test in tests.values() for phase in _items(test.get('phases'))
        ]
        expected_declared = sum(
            phase.get('when') == 'call' or phase.get('outcome') in ('failed', 'skipped')
            for phase in phase_records
        ) + len(collection)
        expected_declared -= sum(
            any(
                mapping(phase).get('when') == 'call' and mapping(phase).get('outcome') == 'failed'
                for phase in _items(test.get('phases'))
            )
            and any(
                mapping(phase).get('when') == 'teardown'
                and mapping(phase).get('outcome') == 'failed'
                for phase in _items(test.get('phases'))
            )
            for test in tests.values()
        )
        if declared != expected_declared:
            errors.append('junit_count_mismatch')
        for attribute, tag in (
            ('errors', 'error'),
            ('failures', 'failure'),
            ('skipped', 'skipped'),
        ):
            values = [suite.get(attribute) for suite in xml.iter('testsuite')]
            if all(value is not None for value in values):
                expected = sum(int(str(value)) for value in values)
                actual = sum(len(case.findall(tag)) for items in cases.values() for case in items)
                if expected != actual:
                    errors.append('junit_' + attribute + '_mismatch')
    except (OSError, ValueError, ElementTree.ParseError) as failure:
        errors.append('junit_unavailable:' + type(failure).__name__)
    matched: set[tuple[str, str]] = set()
    for nodeid, test in tests.items():
        phases = [mapping(item) for item in _items(test['phases'])]
        phase_names = [phase.get('when') for phase in phases]
        if phase_names not in (['setup', 'call', 'teardown'], ['setup', 'teardown']):
            errors.append('phase_order_invalid:' + nodeid)
        if len(phase_names) != len(set(phase_names)) or any(
            phase.get('outcome') not in ('passed', 'failed', 'skipped') for phase in phases
        ):
            errors.append('phase_contract_invalid:' + nodeid)
        outcome = _outcome(phases, test['finished'] is True)
        test['outcome'] = outcome
        durations = [item.get('duration') for item in phases]
        if any(
            type(value) not in (int, float)
            or not math.isfinite(float(str(value)))
            or float(str(value)) < 0
            for value in durations
        ):
            errors.append('phase_duration_invalid:' + nodeid)
        test['seconds'] = sum(
            float(str(value))
            for value in durations
            if type(value) in (int, float)
            and math.isfinite(float(str(value)))
            and float(str(value)) >= 0
        )
        key = _junit_key(nodeid)
        matches = cases.get(key, [])
        outcomes = [
            'error'
            if case.find('error') is not None
            else 'failed'
            if case.find('failure') is not None
            else 'skipped'
            if case.find('skipped') is not None
            else 'passed'
            for case in matches
        ]
        xml_outcome = next(
            (label for label in ('error', 'failed', 'skipped', 'passed') if label in outcomes), None
        )
        if xml_outcome != outcome:
            errors.append('junit_test_outcome_mismatch:' + nodeid)
        if matches:
            matched.add(key)
        if outcome == 'interrupted':
            errors.append('test_interrupted:' + nodeid)
        checks = _items(test['checks'])
        observations = _items(test['observations'])
        if test['important'] and outcome == 'passed' and (not checks or not observations):
            errors.append('important_evidence_missing:' + nodeid)
        test['detail_status'] = 'explicit' if checks and observations else 'not_collected'
        test['missing_information'] = (
            []
            if checks and observations
            else ['期待/観測は未登録、または失敗/skipで登録地点に未到達']
        )
    unmatched = [case for key, items in cases.items() if key not in matched for case in items]
    if len(unmatched) != len(collection) or any(
        not any(case.find(tag) is not None for tag in ('error', 'skipped')) for case in unmatched
    ):
        errors.append('junit_unmatched_cases')
    if step.get('exit_code') == 0 and (
        collection
        or any(test['outcome'] in ('failed', 'error', 'interrupted') for test in tests.values())
    ):
        errors.append('successful_exit_with_failed_tests')
    if step.get('exit_code') == 0 and not any(
        test.get('outcome') == 'passed' for test in tests.values()
    ):
        errors.append('successful_execution_not_demonstrated')
    if step.get('exit_code') not in (0, 1, 2, 5):
        errors.append('pytest_abnormal_exit')
    result.update(
        tests=list(tests.values()),
        collection=collection,
        errors=sorted(set(errors)),
        artifacts=artifacts,
        collection_status='incomplete' if errors else 'complete',
        junit=name + '/' + name + '.xml',
        journal=name + '/events.jsonl',
        files=files,
    )
    return result


def _items_append(record: dict[str, object], key: str, item: object) -> None:
    """収集済み配列へ一項目追加する。対象テストや原データは変更しない。"""
    values = _items(record.get(key))
    values.append(item)
    record[key] = values


def collect(
    output: Path, manifest_data: dict[str, object], steps: list[dict[str, object]]
) -> dict[str, object]:
    """全テストstepを集約する。テスト結果と収集完全性・旧品質結果を別に保持する。"""
    run_id = str(manifest_data['run_id'])
    records = [
        collect_step(output, run_id, step) for step in steps if step['name'] in ('pytest', 'smoke')
    ]
    manifest_data['checks'] = [
        dict(step, step_id=run_id + ':' + str(step['name'])) for step in steps
    ]
    plans = [mapping(item) for item in _items(manifest_data.get('steps'))]
    known = {item.get('name') for item in plans}
    plans.extend(
        {
            'name': name,
            'step_id': run_id + ':' + name,
            'command': ['internal:' + name],
            'timeout_seconds': None,
        }
        for name in ('environment', 'model', 'snapshot', 'evidence')
        if name not in known
    )
    manifest_data['steps'] = plans
    log_files: list[dict[str, object]] = []
    for step in steps:
        path = output / str(step['log'])
        try:
            log_files.append({'path': path.name, 'sha256': hashlib.sha256(_read(path)).hexdigest()})
        except (OSError, ValueError):
            log_files.append({'path': path.name, 'status': 'unavailable'})
    manifest_data['log_files'] = log_files
    errors = [str(error) for record in records for error in _items(record.get('errors'))]
    errors.extend(
        'check_log_unavailable:' + str(item['path'])
        for item in log_files
        if item.get('status') == 'unavailable'
    )
    original_passed = all(
        step.get('passed') is True and step.get('exit_code') == 0 and step.get('error') is None
        for step in steps
    )
    return {
        'format_version': 1,
        'report_kind': 'verification_evidence',
        'manifest': manifest_data,
        'collection_status': 'incomplete' if errors else 'complete',
        'errors': errors,
        'validation_passed': original_passed,
        'test_steps': records,
        'missing_information': [
            '通常テストの期待値・実測値は未収集（explicitのみ）',
            '参照先の長期保管・原証跡真正性は保証しない',
        ],
        'publication': 'fsynced_pending_exclusive_link_then_matching_quality_json',
    }


def render_markdown(report: dict[str, object]) -> str:
    """同じ証拠辞書から人向け一覧を生成する。成功理由を推測しない。"""
    manifest_data = mapping(report['manifest'])
    lines = [
        '# 自動検証証拠',
        '',
        f'実行ID: `{manifest_data["run_id"]}`',
        f'旧品質検査成功: {report["validation_passed"]} / 収集: {report["collection_status"]}',
        '',
        '完成確認: evidence.jsonのquality_report_sha256とquality.jsonのbytesを照合する。',
        'テスト成功と証拠完全性、AI正式資格は別である。',
        '',
        '## テスト一覧',
        '| step | test ID | 結果 | 秒 | 詳細 |',
        '|---|---|---|---:|---|',
    ]
    references: list[str] = []
    details: list[str] = []
    for value in _items(report['test_steps']):
        step = mapping(value)
        for value in _items(step.get('tests')):
            test = mapping(value)
            identity = str(test['test_id']).replace('|', '\\|').replace('\n', ' ')
            lines.append(
                f'| {step["name"]} | `{identity}` | {test["outcome"]} | {test["seconds"]} | {test["detail_status"]} |'
            )
            for item in _items(test.get('checks')):
                check = mapping(item)
                details.extend(
                    [
                        f'### {step["name"]} / {identity} / {check.get("check_id")}',
                        f'期待種別: {check.get("expectation_kind")} / 契約: {check.get("contract")}',
                        f'モデル: {check.get("model")} / 実装: {check.get("implementation")}',
                        '期待: `'
                        + str(_diagnostic(json.dumps(check.get('expected'), ensure_ascii=False)))[
                            :2048
                        ]
                        + '`',
                        '観測: `'
                        + str(
                            _diagnostic(json.dumps(test.get('observations'), ensure_ascii=False))
                        )[:2048]
                        + '`',
                        '詳細/省略部分はJSONと原参照を確認。',
                        '',
                    ]
                )
        references.append(
            f'{step["name"]}: [{step.get("junit", "未取得")}]({step.get("junit", "")}) / journal / {step["collection_status"]}'
        )
    lines.extend(
        [
            '',
            '## 成果物',
            *references,
            '',
            '## 重要チェック',
            *details,
            '',
            '## 不足・診断',
            *[f'- {item}' for item in _items(report['errors'])],
            *[f'- {item}' for item in _items(report['missing_information'])],
            '',
            '## 再現',
            '',
            '```powershell',
            'python -B tools/quality.py',
            '```',
            '',
            '条件・ソースhash・個別再現argv・期待/観測・原参照はevidence.jsonを参照。',
        ]
    )
    return '\n'.join(lines) + '\n'


def verify_publication(output: Path) -> list[str]:
    """保存済み派生証拠の完成marker・参照bytesを再確認する。正式資格は発行しない。"""
    errors: list[str] = []
    try:
        report = mapping(json.loads(_read(output / 'evidence.json')))
        if report.get('publication') != 'fsynced_pending_exclusive_link_then_matching_quality_json':
            errors.append('publication_protocol_unverified')
        if report.get('collection_status') != 'complete':
            errors.append('collection_incomplete')
        if hashlib.sha256(_read(output / 'quality.json')).hexdigest() != report.get(
            'quality_report_sha256'
        ):
            errors.append('quality_publication_mismatch')
        if hashlib.sha256(_read(output / 'evidence.md')).hexdigest() != report.get(
            'markdown_sha256'
        ):
            errors.append('markdown_publication_mismatch')
        for item in _items(mapping(report.get('manifest')).get('log_files')):
            reference = mapping(item)
            path = (output / str(reference['path'])).resolve()
            path.relative_to(output.resolve())
            if hashlib.sha256(_read(path)).hexdigest() != reference.get('sha256'):
                errors.append('check_log_hash_mismatch')
        for item in _items(report.get('test_steps')):
            step = mapping(item)
            for item in _items(step.get('files')):
                reference = mapping(item)
                path = (output / str(reference['path'])).resolve()
                path.relative_to(output.resolve())
                if hashlib.sha256(_read(path)).hexdigest() != reference.get('sha256'):
                    errors.append('step_artifact_hash_mismatch')
            for item in _items(step.get('artifacts')):
                reference = mapping(item)
                if hashlib.sha256(_read(Path(str(reference['path'])))).hexdigest() != reference.get(
                    'sha256'
                ):
                    errors.append('source_artifact_hash_mismatch')
    except (OSError, ValueError, KeyError) as failure:
        errors.append('publication_unavailable:' + type(failure).__name__)
    return errors
