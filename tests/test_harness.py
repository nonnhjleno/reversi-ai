"""対戦取得・再検証・統計資格・保存互換性の回帰試験。原証跡は必要に応じ複製して改変する。"""

import copy
import hashlib
import io
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path, PurePath
from types import SimpleNamespace

import pytest
import harness.evaluate as evaluate_module

from harness.evaluate import compare_reports, evaluate
from harness.openings import generate_population, load_population, select_entries, validate_population
from harness.protocol import HarnessError, Player, validate_response
from harness.search_budget import board_sha256, canonical_json, normalize_config, validate_trace
from harness.referee import INITIAL_ROWS
from harness.statistics import confidence_interval, estimate_pair_scores
from harness import analysis, contracts, openings, protocol
from tools.evidence_plugin import record_expectation, record_observation, record_harness_artifacts

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'reversi_player' / 'reversi.py'
FAULT = ROOT / 'tests' / 'fixtures' / 'fault_player.py'
BUDGET_FIXTURE = ROOT / 'tests' / 'fixtures' / 'budget_player.py'


@pytest.mark.parametrize('command,lines', [
    ('name', ['sample']), ('ask B 2 3', ['OK']), ('play B 0 0', ['NG']),
    ('genmove B', ['2 3']), ('genmove W', ['NG']),
    ('showboard', list(INITIAL_ROWS)), ('result', ['D']), ('quit', []),
])
def test_response_contract_accepts_exact_wire_format_without_mutation(command, lines):
    """授業仕様の行形式を検証する。ここでは着手合法性や盤面遷移は判定しない。"""
    before = copy.deepcopy(lines)
    record_expectation('wire-format', inputs={'command': command, 'lines': lines},
                       expected={'result': None, 'lines': before}, expectation_kind='fixed',
                       model='docs/harness_architecture.md:protocol', contract='プログラム開発方法.md:コマンド仕様',
                       implementation='harness.protocol.validate_response')
    result = validate_response(command, lines)
    record_observation('wire-format', {'result': result, 'lines': lines})
    assert result is None
    assert lines == before


@pytest.mark.parametrize('command,lines', [
    ('name', []), ('name', ['  ']), ('name', ['sample', 'DEBUG']),
    ('ask B 2 3', ['OK ']), ('play B 2 3', ['ok']),
    ('genmove B', ['8 0']), ('genmove B', ['2  3']), ('genmove B', ['２ ３']),
    ('genmove B', ['2 3', 'DEBUG']), ('showboard', ['EEEEEEEE'] * 7),
    ('showboard', ['EEEEEEEX'] * 8), ('showboard', ['EEEEEEEE '] * 8),
    ('result', ['WIN']), ('quit', ['OK']), ('result', 'B'),
    ('result', [None]), ('unsupported', ['NG']),
])
def test_response_contract_rejects_malformed_output_without_mutation(command, lines):
    """余剰出力・誤った型や形式を通信失敗として保持し、正常応答へ補修しない。"""
    before = copy.deepcopy(lines)
    record_expectation('wire-rejection', inputs={'command': command, 'lines': lines},
                       expected={'kind': 'protocol_error', 'command': command, 'lines': before}, expectation_kind='fixed',
                       model='docs/harness_architecture.md:protocol', contract='docs/harness_contracts.md:1',
                       implementation='harness.protocol.validate_response')
    with pytest.raises(HarnessError) as failure:
        validate_response(command, lines)
    record_observation('wire-rejection', {'kind': failure.value.kind, 'command': failure.value.command, 'lines': lines})
    assert failure.value.kind == 'protocol_error'
    assert failure.value.command == command
    assert lines == before


@pytest.fixture(scope='module')
def schema5_reference_reports(tmp_path_factory):
    """実AIと模擬AIの2局ずつを新schemaで取得し、参考集計と原証拠を一時領域に保持する。"""
    directory = tmp_path_factory.mktemp('schema5-reference')
    config = {'format_version': 2, 'budget': {'kind': 'nodes', 'limit_nodes': 100},
              'search': {'deterministic': True, 'counting_definition_id': 'fixture-node-v1'}}
    return {
        'real': evaluate(SCRIPT, SCRIPT, games=2, seed=73, evidence_dir=directory / 'real'),
        'fixture': evaluate(BUDGET_FIXTURE, BUDGET_FIXTURE, games=2, seed=73,
                            config_a=config, config_b=config, budget_policy='equal-work',
                            evidence_dir=directory / 'fixture'),
    }


@pytest.mark.parametrize('kind', ['real', 'fixture'])
def test_schema5_reference_generation_roundtrip(schema5_reference_reports, tmp_path, kind):
    """Goal 1: 新規取得・保存再読込・fresh集計が一致し、ライフサイクル未接続では正式CIを出さない。"""
    report = schema5_reference_reports[kind]
    assert report['schema_version'] == 5
    assert report['summary']['attempted'] == report['summary']['completed'] == 2
    path = tmp_path / (kind + '.json')
    contracts.write_report_exclusive(path, report)
    restored = json.loads(path.read_text(encoding='utf-8'))
    inspected = contracts.inspect_evaluation(restored)
    assert inspected['input_status'] == 'valid', inspected['reasons']
    assert inspected['protocol']['status'] == 'verified', inspected['reasons']
    assert inspected['canonical_summary'] == report['summary']
    assert not analysis.statistical_eligibility(restored)[0]
    assert analysis.estimate(restored)['interval'] is None
    for label in ('A', 'B'):
        summary = inspected['canonical_summary'][label]
        assert summary['wins'] + summary['losses'] + summary['draws'] == 2
        assert summary['point_rate'] == (summary['wins'] + summary['draws'] / 2) / 2
    result = analysis.reanalysis_report(restored)
    assert result['schema_version'] == 5
    assert result['statistics']['assessment']['reference_available']
    assert restored == json.loads(json.dumps(report))


@pytest.mark.parametrize('mutation,status', [('schema', 'unsupported'), ('conditions', 'invalid')])
def test_schema5_decode_diagnostics(schema5_reference_reports, mutation, status):
    """Goal 1: 未対応版や不正rootを、canonical summary欠損のKeyErrorへ変換しない。"""
    report = copy.deepcopy(schema5_reference_reports['fixture'])
    if mutation == 'schema':
        report['schema_version'] = 99
    else:
        report['conditions'] = []
    record_expectation('schema-rejection', inputs={'mutation': mutation},
                       expected={'input_status': status, 'interval': None}, expectation_kind='fixed',
                       model='docs/harness_architecture.md:contracts共通検証', contract='docs/harness_contracts.md:9',
                       implementation='harness.contracts.inspect_evaluation')
    inspected = contracts.inspect_evaluation(report)
    record_observation('schema-rejection', {'input_status': inspected['input_status']})
    assert inspected['input_status'] == status
    interval = analysis.estimate(report)['interval']
    record_observation('schema-rejection', {'interval': interval})
    assert interval is None


def test_schema5_acquisition_failure_has_explicit_diagnostic():
    """Goal 1: 集計不能を仮の集計値で隠さず、検証器の理由付きValueErrorにする。"""
    with pytest.raises(ValueError, match='acquisition_inspection_unavailable'):
        contracts._acquisition_summary({'schema_version': 99, 'report_kind': 'evaluation'})


def test_schema5_incomplete_temporal_cannot_issue_formal(integrity_report, tmp_path):
    """子entry証拠を失った正式候補は、保存positive flagがあっても正式化しない。"""
    report = _relocated_report(integrity_report, tmp_path)

    def remove_entry(events):
        """hashも更新し、必要時刻の欠損そのものを検査する。"""
        del events[0]['child_entry_ns']
        return events

    _rewrite_trace(report, remove_entry)
    report.setdefault('timing', {})['status'] = 'verified'
    inspected = contracts.inspect_evaluation(report)
    assert inspected['timing']['lifecycle']['status'] == 'unknown'
    assert any(item['code'] == 'temporal_endpoint_missing' for item in inspected['reasons'])
    assert not analysis.statistical_eligibility(report)[0]
    assert analysis.estimate(report)['interval'] is None
    assert analysis.reanalysis_report(report)['statistics']['interval'] is None
    assert analysis.paired(report, report)['status'] == 'unavailable'
    assert not analysis.compare(report, report)['pair_statistics_available']
    assert not analysis.comparison_report(report, report)['pair_statistics_available']


@pytest.mark.parametrize('missing', ['lifecycle', 'session', 'command_id', 'endpoint'])
def test_schema5_missing_binding_evidence_is_unknown(schema5_reference_reports, missing):
    """Goal 1: 必要証拠の欠損は検証不能として記録し、正式不可の理由を残す。"""
    report = copy.deepcopy(schema5_reference_reports['fixture'])
    player = report['games'][0]['players']['A']
    if missing == 'lifecycle':
        del player['lifecycle']
    elif missing == 'session':
        del player['lifecycle']['process_session_id']
    else:
        del player['commands'][1]['command_id' if missing == 'command_id' else 'response_read_end_ns']
    inspected = contracts.inspect_evaluation(report)
    assert inspected['status'] == inspected['protocol']['status'] == 'unknown', inspected['reasons']
    assert any(item['code'] == 'saved_protocol_evidence_unavailable' for item in inspected['reasons'])
    assert analysis.estimate(report)['interval'] is None


def test_schema5_saved_summary_is_not_silently_repaired(schema5_reference_reports):
    """Goal 1: 保存得点の矛盾をfresh集計で検出し、正しい値への上書きで成功扱いしない。"""
    report = copy.deepcopy(schema5_reference_reports['fixture'])
    report['summary']['A']['point_rate'] = 123
    before = copy.deepcopy(report)
    inspected = contracts.inspect_evaluation(report)
    assert inspected['status'] == 'failed'
    assert 'summary_scores_mismatch' in inspected['integrity_errors']
    assert inspected['canonical_summary']['A']['point_rate'] <= 1
    assert report == before
    assert not analysis.reanalysis_report(report)['statistics']['assessment']['reference_available']


def test_schema5_unvalidated_lifetime_is_held_at_formal_entries(schema5_reference_reports):
    """要求内の差を保つ起動前通信をfailedとして検出し、正式gateを開かない。"""
    report = copy.deepcopy(schema5_reference_reports['fixture'])
    player = report['games'][0]['players']['A']
    record = player['commands'][1]
    _shift_host_interval(record, player['spawn_before_ns'] - record['validation_end_ns'] - 1_000_000)
    inspected = contracts.inspect_evaluation(report)
    assert inspected['timing']['lifecycle']['status'] == 'failed'
    assert analysis.estimate(report)['interval'] is None
    assert analysis.reanalysis_report(report)['statistics']['interval'] is None
    assert analysis.paired(report, report)['status'] == 'unavailable'
    assert not analysis.compare(report, report)['pair_statistics_available']
    assert not compare_reports(report, report)['pair_statistics_available']


def test_schema5_saved_positive_assessment_cannot_open_formal_gate(schema5_reference_reports):
    """保存Inspectionの品質positiveを、公開入口の資格入力として再利用できない。"""
    inspected = contracts.inspect_evaluation(schema5_reference_reports['fixture'])
    inspected['decoded']['conditions']['evaluation_profile'] = 'confirmatory'
    inspected['status'] = 'verified'
    inspected['timing']['status'] = inspected['timing']['lifecycle']['status'] = 'verified'
    assessed = analysis.assess_evaluation(inspected)
    assert assessed['formal_eligibility'] == 'ineligible'
    assert assessed['input_status'] == 'invalid'
    assert any(item['code'] == 'inspection_is_not_source_report' for item in assessed['reasons'])


@pytest.mark.parametrize('kind', ['real', 'fixture', 'confirmatory'])
def test_schema5_cli_saves_reloads_and_reanalyses(tmp_path, kind):
    """参考/適格な正式評価の2局がCLI保存・再解析を完了し、fresh集計と一致する。"""
    output = tmp_path / 'evaluation.json'
    script = SCRIPT if kind == 'real' else BUDGET_FIXTURE
    command = [sys.executable, '-B', '-m', 'harness.evaluate', '--a', str(script), '--b', str(script),
               '--games', '2', '--seed', '73', '--output', str(output)]
    if kind != 'real':
        config = tmp_path / 'config.json'
        config.write_text(json.dumps({'format_version': 2, 'budget': {'kind': 'nodes', 'limit_nodes': 100},
                                     'search': {'deterministic': True, 'counting_definition_id': 'fixture-node-v1'}}),
                          encoding='utf-8')
        command += ['--a-config', str(config), '--b-config', str(config), '--budget-policy', 'equal-work']
    if kind == 'confirmatory':
        population = tmp_path / 'population.json'
        population.write_text(json.dumps(generate_population(4, 8, 913, 'held_out')), encoding='utf-8')
        command += ['--evaluation-profile', 'confirmatory', '--openings', str(population), '--selection-seed', '9']
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=60)
    expected_exit = 0
    assert result.returncode == expected_exit, result.stderr
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['schema_version'] == 5
    assert report['status']['execution'] == 'complete'
    assert report['status']['protocol'] == 'verified'
    assert report['summary']['completed'] == 2
    inspection = contracts.inspect_evaluation(report)
    assert inspection['canonical_summary'] == report['summary']
    assert inspection['protocol']['status'] == 'verified'
    assert (report['statistics']['interval'] is not None) == (kind == 'confirmatory')
    checkpoint = json.loads((Path(report['evidence']['root']) / 'run-state.json').read_text(encoding='utf-8'))
    assert checkpoint['report_state'] == 'complete'
    reanalysis_path = tmp_path / 'analysis.json'
    analysed = subprocess.run([sys.executable, '-B', '-m', 'harness.analysis', '--report', str(output),
                              '--output', str(reanalysis_path)], cwd=ROOT, capture_output=True,
                             text=True, encoding='utf-8', timeout=60)
    assert analysed.returncode == expected_exit, analysed.stderr
    restored = json.loads(reanalysis_path.read_text(encoding='utf-8'))
    assert restored['schema_version'] == 5
    assert (restored['statistics']['interval'] is not None) == (kind == 'confirmatory')
    assert restored['statistics']['verification']['status'] == report['statistics']['verification']['status']


def _shift_host_interval(record, delta):
    """差・期限・応答を保ち、実測端点だけ平行移動する時系列反例を作る。"""
    for key in ('exchange_start_ns', 'send_start_ns', 'response_read_end_ns',
                'validation_end_ns', 'exit_observed_ns'):
        if key in record:
            record[key] += delta


def _assert_temporal_rejected(report, valid, expected='failed'):
    """原報告を再検証する全正式入口で、時系列違反が正式区間にならないことを確認する。"""
    inspected = contracts.inspect_evaluation(report)
    assert inspected['timing']['status'] == expected, inspected['reasons']
    assert not analysis.statistical_eligibility(report)[0]
    assert analysis.estimate(report)['interval'] is None
    assert analysis.reanalysis_report(report)['statistics']['interval'] is None
    assert analysis.paired(report, valid)['status'] == 'unavailable'
    assert not analysis.compare(report, valid)['pair_statistics_available']
    assert not analysis.comparison_report(report, valid)['pair_statistics_available']
    assert not compare_reports(report, valid)['pair_statistics_available']


@pytest.mark.parametrize('mutation', ['before_spawn', 'command_order', 'quit_before_spawn', 'after_exit'])
def test_temporal_host_lifetime_cannot_bypass_formal_entries(integrity_report, mutation):
    """T03/T05: 正常な取得を対照に、局所区間の差を保つ生存期間外の要求を拒否する。"""
    assert analysis.statistical_eligibility(integrity_report)[0]
    report = copy.deepcopy(integrity_report)
    player = report['games'][0]['players']['A']
    record = player['commands'][-1 if mutation == 'quit_before_spawn' else 1]
    if mutation in ('before_spawn', 'quit_before_spawn'):
        end = record.get('validation_end_ns', record.get('exit_observed_ns'))
        delta = player['spawn_before_ns'] - end - 1_000_000
    elif mutation == 'after_exit':
        delta = player['commands'][-1]['exit_observed_ns'] - record['exchange_start_ns'] + 1_000_000
    else:
        delta = player['commands'][2]['exchange_start_ns'] - record['validation_end_ns'] + 1_000_000
    _shift_host_interval(record, delta)
    _assert_temporal_rejected(report, integrity_report)


@pytest.mark.parametrize('mutation,expected', [
    ('child_before_spawn', 'failed'), ('generation', 'failed'), ('domain', 'failed'),
    ('domain_missing', 'unknown'), ('received_before_send', 'failed'),
    ('search_after_response', 'failed'), ('command_id', 'failed'),
    ('search_endpoint_missing', 'unknown'),
])
def test_temporal_rehashed_trace_is_semantically_checked(integrity_report, tmp_path, mutation, expected):
    """T04/T06/T07/T17: rawとhashをともに変更しても、意味矛盾と証拠不足を区別する。"""
    assert analysis.statistical_eligibility(integrity_report)[0]
    report = _relocated_report(integrity_report, tmp_path)
    player = report['games'][0]['players']['A']

    def mutate(events):
        """目的の反例だけを作り、hash不一致による偶然の拒否を避ける。"""
        header = events[0]
        event = next(item for item in events if item.get('type') == 'genmove')
        record = next(item for item in player['commands'] if item['command_id'] == event['command_id'])
        if mutation == 'child_before_spawn':
            header['child_entry_ns'] = player['spawn_before_ns'] - 1_000_000
        elif mutation == 'generation':
            header['process_generation'] = 1
        elif mutation == 'domain':
            header['clock_domain_id'] = 'another-domain'
        elif mutation == 'domain_missing':
            del header['clock_domain_id']
        elif mutation == 'received_before_send':
            event['command_received_ns'] = record['send_start_ns'] - 1_000_000
        elif mutation == 'search_after_response':
            duration = event['search_elapsed_ns']
            event['search_start_ns'] = record['validation_end_ns'] + 1_000_000
            event['search_end_ns'] = event['search_start_ns'] + duration
            event['response_ready_ns'] = event['search_end_ns'] + 1_000
        elif mutation == 'command_id':
            event['command_id'] = 'another-command'
        else:
            del event['search_start_ns']
        return events

    _rewrite_trace(report, mutate)
    _assert_temporal_rejected(report, integrity_report, expected)


def test_temporal_public_assessment_requires_original_source(integrity_report):
    """T15: 公開assessもfresh検証し、保存Inspectionを認証として再利用しない。"""
    assert analysis.assess_evaluation(integrity_report)['formal_eligibility'] == 'eligible'
    saved = contracts.inspect_evaluation(integrity_report)
    assert analysis.assess_evaluation(saved)['formal_eligibility'] == 'ineligible'


def test_temporal_normal_schema5_reaches_all_formal_entries(integrity_report):
    """完全なtrace4は全時間軸を検証し、同じ原資料の単独・対応差を正式発行できる。"""
    record_expectation('formal-eligibility', inputs={'source_schema': 5, 'population_size': 4, 'games': 2, 'seed': 73},
                       expected={'inspection': 'verified', 'formal_eligibility': 'eligible', 'statistics': 'available'},
                       expectation_kind='consistency', model='docs/harness_architecture.md:INS/AE/PROMOTE',
                       contract='docs/harness_contracts.md:5/8/12', implementation='harness.analysis.assess_evaluation')
    inspected = contracts.inspect_evaluation(integrity_report)
    record_observation('formal-eligibility', {'inspection': inspected['status'],
                                           'timing': {axis: value['status'] for axis, value in inspected['timing'].items() if isinstance(value, dict) and 'status' in value}})
    assert inspected['status'] == 'verified', inspected['reasons']
    for axis in ('host_lifecycle', 'child_lifecycle', 'command_intervals', 'causal_order'):
        assert inspected['timing'][axis]['status'] == 'verified'
    eligibility = analysis.assess_evaluation(integrity_report)['formal_eligibility']
    record_observation('formal-eligibility', {'formal_eligibility': eligibility})
    assert eligibility == 'eligible'
    statistics_status = analysis.estimate(integrity_report)['status']
    record_observation('formal-eligibility', {'statistics': statistics_status})
    assert statistics_status == 'available'
    assert analysis.reanalysis_report(integrity_report)['statistics']['status'] == 'available'
    assert analysis.paired(integrity_report, integrity_report)['status'] == 'available'
    assert compare_reports(integrity_report, integrity_report)['pair_statistics_available']
    assert any(command.get('phase') == 'pass_probe' for game in integrity_report['games']
               for player in game['players'].values() for command in player['commands'])
    assert all(game['game_status'] == 'complete' for game in integrity_report['games'])
    record_harness_artifacts(integrity_report)


@pytest.mark.parametrize('mutation,expected', [
    ('received_before_spawn', 'failed'), ('search_before_entry', 'failed'),
    ('search_before_receive', 'failed'), ('search_reversed', 'failed'),
    ('child_after_exit', 'failed'), ('child_serial', 'failed'),
    ('session', 'failed'), ('ready_missing', 'unknown'), ('clock_missing', 'unknown'),
    ('extra_command', 'failed'), ('init_after_receive', 'failed'),
])
def test_temporal_all_child_events_are_revalidated(integrity_report, tmp_path, mutation, expected):
    """genmove以外のCC/CRも生存期間と結び、新hashだけで違反を通せない。"""
    report = _relocated_report(integrity_report, tmp_path)
    player = report['games'][0]['players']['A']

    def mutate(events):
        """対象の意味矛盾だけを作る。原資料は一時コピーのみ変更する。"""
        header, first = events[0], events[1]
        event = next(item for item in events if item.get('type') == 'genmove')
        if mutation == 'received_before_spawn':
            first['command_received_ns'] = player['spawn_before_ns'] - 1_000_000
        elif mutation == 'search_before_entry':
            event['search_end_ns'] = header['child_entry_ns'] - 1_000_000
            event['search_start_ns'] = event['search_end_ns'] - event['search_elapsed_ns']
        elif mutation == 'search_before_receive':
            event['search_start_ns'] = event['command_received_ns'] - 1_000_000
            event['search_end_ns'] = event['search_start_ns'] + event['search_elapsed_ns']
        elif mutation == 'search_reversed':
            event['search_start_ns'] = event['search_end_ns'] + 1_000_000
        elif mutation == 'child_after_exit':
            first['command_received_ns'] = player['lifecycle']['exit_observed_ns'] + 1_000_000
            first['response_ready_ns'] = first['command_received_ns'] + 1_000
        elif mutation == 'child_serial':
            first['response_ready_ns'] = events[2]['command_received_ns'] + 1_000_000
        elif mutation == 'session':
            first['process_session_id'] = 'another-session'
        elif mutation == 'ready_missing':
            del header['protocol_ready_ns']
        elif mutation == 'clock_missing':
            del header['clock_descriptor']
        elif mutation == 'extra_command':
            events.insert(2, copy.deepcopy(first))
        else:
            header['protocol_ready_ns'] = first['command_received_ns'] + 1_000_000
        return events

    _rewrite_trace(report, mutate)
    _assert_temporal_rejected(report, integrity_report, expected)
    inspected = contracts.inspect_evaluation(report)
    assert any(item['status'] == expected for item in inspected['timing']['reasons'])


@pytest.mark.parametrize('child_entry', [120_000, 200_000])
def test_temporal_popen_return_is_not_child_initialization_barrier(child_entry):
    """T01/T02の独立した整数例。子entryはPopen戻りの前後どちらでも正常。"""
    from harness.protocol import clock_descriptor, command_id, validate_process_timeline
    from harness.search_budget import validate_child_lifecycle

    clock = clock_descriptor()
    clock['qpc_frequency_hz'] = 10_000_000
    context = {'run_id': 'unit-run', 'purpose': 'game', 'pair_id': 'unit-pair', 'leg': 0,
               'player': 'A', 'case_id': 'unit-case', 'role_seed': 73, 'clock_domain_id': 'unit-domain'}
    lifecycle = {**context, 'process_session_id': 'unit-session', 'process_generation': 0,
                 'pid': 17, 'measurement_thread_id': 11, 'host_clock': clock,
                 'spawn_before_ns': 100_000, 'popen_return_ns': 180_000,
                 'startup_end_ns': 260_000, 'exit_observed_ns': 400_000, 'exit_code': 0,
                 'termination_kind': 'normal_quit', 'cleanup_end_ns': 450_000}
    command_context = {**context, 'process_session_id': 'unit-session', 'process_generation': 0}
    commands = []
    for index, (command, start, sent, read, end) in enumerate([
            ('name', 190_000, 200_000, 250_000, 260_000),
            ('showboard', 270_000, 280_000, 310_000, 320_000),
            ('quit', 330_000, 340_000, None, 400_000)]):
        record = {'command': command, 'command_index': index, 'command_id': command_id(command_context, index),
                  'exchange_start_ns': start, 'send_start_ns': sent, 'external_response_ns': end - start,
                  'seconds': (end - start) / 1e9, 'limit_ns': 1_000_000_000,
                  'clock': clock, 'clock_domain_id': 'unit-domain', 'process_session_id': 'unit-session',
                  'process_generation': 0, 'measurement_thread_id': 11}
        if read is None:
            record['exit_observed_ns'] = end
        else:
            record.update(response_read_end_ns=read, validation_end_ns=end, wire_envelope_ns=read - sent)
        commands.append(record)
    commands[0]['spawn_before_ns'] = 100_000
    player = {'lifecycle': lifecycle, 'commands': commands, 'clock': clock, 'clock_domain_id': 'unit-domain',
              'spawn_before_ns': 100_000, 'startup_end_ns': 260_000,
              'startup_seconds': 160_000 / 1e9, 'exit_code': 0}
    header = {'type': 'config', 'format_version': 4, **command_context,
              'host_clock': clock, 'clock_descriptor': clock, 'pid': 17, 'measurement_thread_id': 22,
              'child_entry_ns': child_entry, 'protocol_ready_ns': 220_000,
              'model_id': 'unit-model', 'model_load_status': 'startup',
              'model_load_start_ns': child_entry + 1_000, 'model_load_end_ns': 219_000,
              'model_load_elapsed_ns': 218_000 - child_entry}
    events = [header]
    for record, received, ready in zip(commands, [230_000, 290_000, 350_000], [240_000, 300_000, None]):
        event = {'type': 'command', 'command': record['command'], 'command_id': record['command_id'],
                 'command_index': record['command_index'], 'command_received_ns': received,
                 'process_session_id': 'unit-session', 'process_generation': 0, 'measurement_thread_id': 22}
        if ready is not None:
            event['response_ready_ns'] = ready
        events.append(event)
    events.append({'type': 'process_exit_ready', 'command_id': commands[-1]['command_id'],
                   'process_session_id': 'unit-session', 'process_generation': 0,
                   'measurement_thread_id': 22, 'exit_ready_ns': 360_000})
    host = validate_process_timeline(player, commands, context,
                                    {'startup_timeout': 1, 'command_timeout': 1, 'genmove_timeout': 10})
    child = validate_child_lifecycle(events, commands, lifecycle, command_context)
    assert host['status'] == child['status'] == 'verified', (host, child)


@pytest.mark.parametrize('field,value,expected', [
    ('model_load_start_ns', None, 'unknown'),
    ('model_load_end_ns', None, 'unknown'),
    ('model_load_elapsed_ns', None, 'unknown'),
    ('model_load_start_ns', True, 'failed'),
    ('model_load_start_ns', 120_000.5, 'failed'),
    ('model_load_elapsed_ns', -1, 'failed'),
])
def test_temporal_model_load_evidence_distinguishes_missing_from_invalid(field, value, expected):
    """T08/T10: 同じ子時計内の初期化で、証拠欠損と提供済み不正値を区別する。"""
    from harness.search_budget import _model_load_contract

    info = {'model_id': 'unit-model', 'model_load_status': 'startup',
            'model_load_start_ns': 120_000, 'model_load_end_ns': 140_000,
            'model_load_elapsed_ns': 20_000}
    info[field] = value
    status, reason = _model_load_contract(info, 100_000, 220_000)
    assert status == expected, reason


@pytest.mark.parametrize('difference,expected', [
    (-102, 'failed'), (-101, 'unknown'), (0, 'unknown'), (101, 'unknown'), (102, 'verified'),
])
def test_temporal_qpc_boundaries_preserve_ambiguity(difference, expected):
    """T09の10MHz例。U=101ns以内を大きな許容差でverifiedへ丸めない。"""
    from harness.protocol import clock_descriptor, temporal_order, temporal_result

    clock = clock_descriptor()
    clock['qpc_frequency_hz'] = 10_000_000
    result = temporal_result(temporal_order(1_000_000, 1_000_000 + difference, clock, 'T09', True))
    assert result['status'] == expected, result


@pytest.mark.parametrize('before,after,expected', [
    (None, True, 'failed'), (True, None, 'failed'),
    (None, 1.5, 'failed'), (1.5, None, 'failed'),
    (None, None, 'unknown'), (1, None, 'unknown'), (2, 1, 'unknown'),
])
def test_temporal_bad_type_survives_missing_clock_or_endpoint(before, after, expected):
    """T08/T09: 提供済み不正型はfailed、時計が不明なら整数の大小を比較しない。"""
    from harness.protocol import temporal_order, temporal_result

    result = temporal_result(temporal_order(before, after, None, 'T09', True))
    assert result['status'] == expected


def test_temporal_missing_domain_is_unknown_not_known_conflict(integrity_report, tmp_path):
    """domain証拠だけの欠損を、提供済みの別domainとの矛盾として分類しない。"""
    report = _relocated_report(integrity_report, tmp_path)

    def remove_domain(events):
        """原証拠の一時コピーからdomainを除き、hashは更新する。"""
        del events[0]['clock_domain_id']
        return events

    _rewrite_trace(report, remove_domain)
    inspection = contracts.inspect_evaluation(report)
    assert inspection['status'] == inspection['timing']['status'] == 'unknown', inspection['reasons']
    assert analysis.assess_evaluation(report)['formal_eligibility'] == 'ineligible'


def test_temporal_reused_session_is_rejected_with_consistent_aliases(integrity_report, tmp_path):
    """各slot内のID・hash・測定aliasが合っても、run全体のsession再利用は不可。"""
    from harness.protocol import command_id

    report = _relocated_report(integrity_report, tmp_path)
    game = report['games'][0]
    player = game['players']['A']
    session = report['games'][1]['players']['A']['lifecycle']['process_session_id']
    player['lifecycle']['process_session_id'] = session
    replacements = {}
    for record in player['commands']:
        original = record['command_id']
        record['process_session_id'] = session
        record['command_id'] = command_id(player['lifecycle'], record['command_index'])
        replacements[original] = record['command_id']
    for cache in (player['budget_trace'], game['budget']['A']):
        for measurement in cache['measurements']:
            measurement['command_id'] = replacements.get(measurement['command_id'], measurement['command_id'])

    def rebind(events):
        """IDの意味検査を孤立させ、単なるhash/cache不一致で落とさない。"""
        for event in events:
            event['process_session_id'] = session
            if 'command_id' in event:
                event['command_id'] = replacements[event['command_id']]
        return events

    _rewrite_trace(report, rebind)
    game['budget']['A']['artifact'] = copy.deepcopy(player['budget_trace']['artifact'])
    inspection = contracts.inspect_evaluation(report)
    assert inspection['timing']['status'] == 'failed'
    assert 'temporal_session_reused' in inspection['integrity_errors']
    assert 'metric_measurement_alias_mismatch' not in inspection['integrity_errors']
    assert analysis.estimate(report)['interval'] is None


def test_temporal_actor_move_must_precede_peer_play(integrity_report):
    """単独要求の長さだけでなく、collectorが保証するA/B因果辺を検査する。"""
    report = copy.deepcopy(integrity_report)
    game = report['games'][0]
    move = next(move for move in game['moves'] if move['position'] is not None)
    actor = move['player']
    peer = 'B' if actor == 'A' else 'A'
    generated = next(record for record in game['players'][actor]['commands']
                     if record['command'] == 'genmove ' + move['color'])
    played = next(record for record in game['players'][peer]['commands']
                  if record['command'] == 'play {} {} {}'.format(move['color'], *move['position']))
    _shift_host_interval(played, generated['validation_end_ns'] - played['exchange_start_ns'] - 1_000_000)
    inspection = contracts.inspect_evaluation(report)
    assert inspection['timing']['causal_order']['status'] == 'failed'
    assert any('T-CAUSE.' in item['path'] and generated['command_id'] in item['path']
               for item in inspection['timing']['causal_order']['reasons'])
    assert analysis.estimate(report)['interval'] is None


@pytest.mark.parametrize('mutation,expected', [
    ('normal', 'verified'), ('host_before_spawn', 'failed'),
    ('child_before_spawn', 'failed'), ('child_clock_missing', 'unknown'),
])
def test_temporal_public_apis_and_saved_clis_agree(integrity_report, tmp_path, mutation, expected):
    """正常sourceは両CLIで0、不正/不足sourceは診断保存1。保存原資料は修復しない。"""
    report = _relocated_report(integrity_report, tmp_path)
    player = report['games'][0]['players']['A']
    if mutation == 'host_before_spawn':
        record = player['commands'][1]
        _shift_host_interval(record, player['spawn_before_ns'] - record['validation_end_ns'] - 1_000_000)
    elif mutation in ('child_before_spawn', 'child_clock_missing'):
        def mutate(events):
            """全子時刻の相対順を保った起動前移動でも、固定SPへの接続で拒否する。"""
            if mutation == 'child_clock_missing':
                del events[0]['clock_descriptor']
            else:
                delta = player['spawn_before_ns'] - events[-1]['exit_ready_ns'] - 1_000_000
                for event in events:
                    for key, value in list(event.items()):
                        if key.endswith('_ns') and not key.endswith('_elapsed_ns') and type(value) is int:
                            event[key] += delta
            return events
        _rewrite_trace(report, mutate)
    original = json.loads(json.dumps(report))
    inspection = contracts.inspect_evaluation(report)
    assert inspection['timing']['status'] == expected, inspection['reasons']
    eligible = expected == 'verified'
    assert (analysis.assess_evaluation(report)['formal_eligibility'] == 'eligible') == eligible
    assert (analysis.assess_comparison(report, report)['formal_pair_eligibility'] == 'eligible') == eligible
    assert (analysis.estimate(report)['status'] == 'available') == eligible
    assert (analysis.paired(report, report)['status'] == 'available') == eligible
    assert compare_reports(report, report)['pair_statistics_available'] == eligible
    source = tmp_path / 'source.json'
    source.write_text(json.dumps(report), encoding='utf-8')
    raw_before = {str(path): path.read_bytes() for path in Path(report['evidence']['root']).rglob('*.jsonl')}
    for mode in ('reanalysis', 'comparison'):
        output = tmp_path / (mode + '.json')
        command = [sys.executable, '-B', '-m', 'harness.analysis', '--report', str(source)] if mode == 'reanalysis' else [
            sys.executable, '-B', '-m', 'harness.evaluate', '--compare', str(source), str(source)]
        completed = subprocess.run(command + ['--output', str(output)], cwd=ROOT,
                                   capture_output=True, text=True, encoding='utf-8', timeout=60)
        assert completed.returncode == (0 if eligible else 1), completed.stderr
        restored = json.loads(output.read_text(encoding='utf-8'))
        if mode == 'reanalysis':
            assert restored['statistics']['verification']['status'] == expected
            assert (restored['statistics']['status'] == 'available') == eligible
        else:
            assert restored['pair_statistics_available'] == eligible
            assert all(item['quality_status'] == expected for item in restored['assessment']['evaluations'])
    assert json.loads(source.read_text(encoding='utf-8')) == original
    assert report == original
    assert all(Path(path).read_bytes() == before for path, before in raw_before.items())


def test_temporal_real_startup_model_load_is_separate_from_search(tmp_path):
    """模擬producerで実ファイルを初回name前に読み、loadをsearch合計に加算しない。"""
    asset = tmp_path / 'fixture-model.bin'
    asset.write_bytes(b'initialization evidence only; not a trained model')
    population = tmp_path / 'population.json'
    population.write_text(json.dumps(generate_population(2, 8, 917)), encoding='utf-8')
    config = {'format_version': 2, 'budget': {'kind': 'nodes', 'limit_nodes': 100},
              'search': {'deterministic': True, 'counting_definition_id': 'fixture-node-v1',
                         'model_id': 'fixture-model', 'model_path': str(asset),
                         'model_sha256': hashlib.sha256(asset.read_bytes()).hexdigest()}}
    report = evaluate(BUDGET_FIXTURE, BUDGET_FIXTURE, games=2, openings_file=population,
                      selection_seed=1, config_a=config, config_b=config, assets_a=(asset,), assets_b=(asset,),
                      budget_policy='equal-work', evaluation_profile='confirmatory', evidence_dir=tmp_path / 'runs')
    assert report['statistics']['status'] == 'available'
    inspection = contracts.inspect_evaluation(report)
    assert inspection['timing']['status'] == 'verified'
    for label in ('A', 'B'):
        total = 0
        for game in report['games']:
            artifact = game['players'][label]['budget_trace']['artifact']
            events = [json.loads(line) for line in (Path(report['evidence']['root']) / artifact['path']).read_text(encoding='utf-8').splitlines()]
            header = events[0]
            assert header['model_load_status'] == 'startup'
            assert header['child_entry_ns'] <= header['model_load_start_ns'] <= header['model_load_end_ns'] <= header['protocol_ready_ns']
            assert header['protocol_ready_ns'] <= events[1]['command_received_ns']
            assert header['model_load_elapsed_ns'] == header['model_load_end_ns'] - header['model_load_start_ns']
            total += sum(event['search_elapsed_ns'] for event in events if event.get('type') == 'genmove')
        assert inspection['canonical_summary'][label]['search_sum_ns'] == total


@pytest.mark.parametrize('mutate_host', [False, True])
def test_temporal_legacy4_missing_lifecycle_is_not_upgraded(integrity_report, tmp_path, mutate_host):
    """旧4/trace3の独立fixtureでは参考を保全し、存在するSP逆転だけはfailedにする。"""
    from harness.protocol import command_id, LEGACY_MEASUREMENT_CONTRACT

    report = _relocated_report(integrity_report, tmp_path)
    report['schema_version'] = 4
    report['conditions']['measurement_contract'] = LEGACY_MEASUREMENT_CONTRACT
    report['runtime']['clock']['measurement_contract'] = LEGACY_MEASUREMENT_CONTRACT
    players = []
    for game in report['games']:
        for label in ('A', 'B'):
            player = game['players'][label]
            context = {'run_id': report['run_id'], 'pair_id': game['pair_id'], 'leg': game['leg'], 'player': label}
            players.append((player, player['budget_trace']['artifact'], context, game['budget'][label]))
    for label in ('A', 'B'):
        check = report['determinism'][label]
        for attempt, player in enumerate(check['attempts']):
            context = {'run_id': report['run_id'], 'pair_id': 'determinism-' + label, 'leg': attempt, 'player': label}
            players.append((player, check['trace_artifacts'][attempt], context, None))
    for player, artifact, context, alias in players:
        player.pop('lifecycle')
        player['clock'] = copy.deepcopy(report['runtime']['clock'])
        replacements = {}
        for record in player['commands']:
            previous = record['command_id']
            record['command_id'] = command_id(context, record['command_index'])
            replacements[previous] = record['command_id']
            record['clock'] = copy.deepcopy(player['clock'])
        for cache in (player.get('budget_trace', {}), alias or {}):
            for measurement in cache.get('measurements', []):
                measurement['command_id'] = replacements.get(measurement['command_id'], measurement['command_id'])
        path = Path(report['evidence']['root']) / artifact['path']
        events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        events[0]['format_version'] = 3
        events[0]['clock_descriptor'] = copy.deepcopy(player['clock'])
        events[0]['host_clock'] = copy.deepcopy(player['clock'])
        events = [event for index, event in enumerate(events) if index == 0 or event.get('type') == 'genmove']
        for event in events[1:]:
            event['command_id'] = replacements[event['command_id']]
        path.write_text('\n'.join(canonical_json(event) for event in events) + '\n', encoding='utf-8')
        artifact['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        if alias is not None:
            alias['artifact'] = copy.deepcopy(artifact)
    report['experiment_id'] = evaluate_module._experiment_id(
        report['conditions'], report['runtime'], report['targets'], report['evaluation_fingerprint'])
    if mutate_host:
        player = report['games'][0]['players']['A']
        record = player['commands'][1]
        _shift_host_interval(record, player['spawn_before_ns'] - record['validation_end_ns'] - 1_000_000)
    original = copy.deepcopy(report)
    inspection = contracts.inspect_evaluation(report)
    assert inspection['timing']['status'] == ('failed' if mutate_host else 'unknown'), inspection['reasons']
    assert analysis.estimate(report)['interval'] is None
    assert not analysis.statistical_eligibility(report)[0]
    assert analysis.reanalysis_report(report)['schema_version'] == 5
    if not mutate_host:
        assert inspection['status'] == 'unknown', inspection['reasons']
        assert analysis.assess_evaluation(report)['reference_available']
    assert report == original


@pytest.fixture(scope='module')
def integrity_report(tmp_path_factory):
    """回帰試験用にN=4から1ケースを抽出した正式報告と原証跡を一時領域へ作る。

    module共有fixtureなので改変試験はdeepcopyまたは証跡ごとの複製を使う。
    """
    directory = tmp_path_factory.mktemp('integrity-report')
    population = generate_population(4, 8, 913, 'held_out')
    path = directory / 'population.json'
    path.write_text(json.dumps(population), encoding='utf-8')
    config = {
        'format_version': 2, 'budget': {'kind': 'nodes', 'limit_nodes': 100},
        'search': {'deterministic': True, 'counting_definition_id': 'fixture-node-v1'},
    }
    return evaluate(
        BUDGET_FIXTURE, BUDGET_FIXTURE, games=2, seed=73,
        openings_file=path, selection_seed=9, config_a=config, config_b=config,
        budget_policy='equal-work', evaluation_profile='confirmatory', confidence=0.90,
        evidence_dir=directory / 'runs',
    )


def _relocated_report(report, directory):
    """原報告をJSON経由で複製し、証跡ツリーを一時領域へコピーして読込先を差し替える。"""
    import shutil

    copied = json.loads(json.dumps(report))
    shutil.copytree(report['evidence']['root'], directory / 'evidence')
    copied['evidence']['root'] = str(directory / 'evidence')
    return copied


def _rewrite_trace(report, mutate, label='A'):
    """複製済み報告のrawを変更し、artifact hashも更新して内容の意味検証だけを試せる状態にする。"""
    artifact = report['games'][0]['players'][label]['budget_trace']['artifact']
    path = Path(report['evidence']['root']) / artifact['path']
    events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    events = mutate(events)
    path.write_text('\n'.join(canonical_json(event) for event in events) + '\n', encoding='utf-8')
    artifact['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_inspection_uses_archive_size_and_revalidates_saved_report(integrity_report):
    """N=4とn=1を区別し、JSON往復・再解析でも原報告と保存confidenceが保持されることを確かめる。"""
    report = json.loads(json.dumps(integrity_report))
    original = copy.deepcopy(report)
    inspection = contracts.inspect_evaluation(report)
    assert inspection['status'] == 'verified', inspection['reasons']
    assert inspection['protocol']['status'] == inspection['budget']['status'] == 'verified'
    assert inspection['population']['verified_N'] == 4
    assert inspection['derived']['coverage_kind'] == 'sample_verified'
    assert analysis.estimate(report)['interval']['method'] != 'finite_population_census'
    assert analysis.estimate(report)['analysis_spec']['confidence'] == 0.90
    assert analysis.paired(report, report)['status'] == 'available'
    arithmetic = estimate_pair_scores(inspection['derived']['games'], 4)
    assert arithmetic['result_scope'] == 'arithmetic_only'
    assert arithmetic['interval']['method'] != 'finite_population_census'
    assert report == original


def test_consistency_q1_saved_budget_hierarchy_cannot_bypass_entries(integrity_report):
    """rawが正しくても、roleと矛盾する取得時failedを正式資格へ自動修復しない。"""
    assert contracts.inspect_evaluation(integrity_report)['budget']['status'] == 'verified'
    report = copy.deepcopy(integrity_report)
    for game in report['games']:
        game['budget_status'] = 'failed'
    report['status']['budget'] = 'failed'
    inspection = contracts.inspect_evaluation(report)
    assert 'budget_state_hierarchy_mismatch' in inspection['integrity_errors']
    assert not analysis.statistical_eligibility(report)[0]
    assert analysis.estimate(report)['interval'] is None
    assert analysis.paired(report, integrity_report)['status'] == 'unavailable'
    assert not compare_reports(report, integrity_report)['pair_statistics_available']


def test_consistency_q2_search_cannot_exceed_host_interval(integrity_report, tmp_path):
    """nodes上限に収まっても、外部応答より長い9秒の探索を拒否する。"""
    assert contracts.inspect_evaluation(integrity_report)['timing']['command_intervals']['status'] == 'verified'
    report = _relocated_report(integrity_report, tmp_path)

    def mutate(events):
        """探索差だけを9秒に改変し、raw hash不一致による拒否を避ける。"""
        next(event for event in events if event.get('type') == 'genmove')['search_elapsed_ns'] = 9_000_000_000
        return events

    _rewrite_trace(report, mutate)
    inspection = contracts.inspect_evaluation(report)
    assert any('search_interval' in item['code'] for item in inspection['reasons'])
    assert inspection['timing']['status'] == 'failed'
    assert analysis.estimate(report)['interval'] is None


@pytest.mark.parametrize('mutation', ['mean', 'count', 'missing', 'alias'])
def test_consistency_q3_internal_metrics_match_raw(integrity_report, mutation):
    """保存summaryと測定cacheはraw由来の正本と照合する。"""
    assert contracts.inspect_evaluation(integrity_report)['budget']['status'] == 'verified'
    report = copy.deepcopy(integrity_report)
    if mutation == 'mean':
        report['summary']['A']['mean_search_seconds'] = -123
    elif mutation == 'count':
        report['summary']['A']['search_samples'] = 0
    elif mutation == 'missing':
        del report['games'][0]['budget']['A']['measurements']
    else:
        report['games'][0]['players']['A']['budget_trace']['measurements'] = []
    inspection = contracts.inspect_evaluation(report)
    assert any('metric' in item['code'] for item in inspection['reasons'])
    assert analysis.estimate(report)['interval'] is None


def test_consistency_q4_zero_started_players_are_not_exempt(monkeypatch, tmp_path):
    """未起動は証拠不足であり、空集合へのallで適用除外にしてはいけない。"""
    def fail_start(*arguments, **keywords):
        """Popen前の失敗を模擬し、成功playerを1件も作らない。"""
        raise HarnessError('abnormal_exit', 'start rejected')

    monkeypatch.setattr(evaluate_module, 'Player', fail_start)
    report = evaluate(SCRIPT, SCRIPT, games=2, evidence_dir=tmp_path / 'runs')
    assert report['summary']['completed'] == 0
    assert report['status']['protocol'] == 'failed'
    assert all(game['budget_status'] == 'unknown' for game in report['games'])
    assert report['status']['budget'] == 'unknown'
    inspection = contracts.inspect_evaluation(report)
    assert inspection['budget']['status'] == 'unknown'
    assert inspection['protocol']['status'] == 'failed'


@pytest.mark.parametrize('mutation', ['conditions', 'schema'])
def test_consistency_q5_invalid_input_is_not_ci_not_requested(integrity_report, mutation):
    """未対応schemaと不正なconditionsを構造化不可とし、例外やCI不要へ落とさない。"""
    report = copy.deepcopy(integrity_report)
    if mutation == 'conditions':
        report['conditions'] = []
    else:
        report['schema_version'] = 99
    result = analysis.estimate(report)
    assert result['status'] == 'unavailable'
    assert result['interval'] is None
    assert result['statistical_eligibility'] == 'ineligible'
    assert result['verification']['input_status'] == ('invalid' if mutation == 'conditions' else 'unsupported')


def test_consistency_q6_post_start_save_failure_is_operational(integrity_report, monkeypatch, tmp_path, capsys):
    """完了報告のfinal保存失敗は入力不正2ではなく運用失敗1とする。"""
    output = tmp_path / 'final.json'
    monkeypatch.setattr(sys, 'argv', ['evaluate', '--a', str(SCRIPT), '--b', str(SCRIPT),
                                     '--games', '2', '--output', str(output)])
    monkeypatch.setattr(evaluate_module, 'evaluate', lambda *arguments, **keywords: copy.deepcopy(integrity_report))
    original_open = Path.open

    def fail_final(path, *arguments, **keywords):
        """最終出力のみ権限失敗とし、原traceとcheckpointには触れない。"""
        if path == output:
            raise PermissionError('final denied')
        return original_open(path, *arguments, **keywords)

    monkeypatch.setattr(Path, 'open', fail_final)
    with pytest.raises(SystemExit) as caught:
        evaluate_module.main()
    assert caught.value.code == 1
    assert 'final' in capsys.readouterr().err


@pytest.mark.parametrize('mutation,expected,reason', [
    ('normal', 'verified', None), ('zero', 'verified', None),
    ('elapsed', 'failed', 'search_interval_elapsed_mismatch'),
    ('before', 'failed', 'search_interval_not_enclosed'),
    ('after', 'failed', 'search_interval_not_enclosed'),
    ('order', 'failed', 'search_interval_order_invalid'),
    ('bool', 'failed', 'search_interval_order_invalid'),
    ('float', 'failed', 'search_interval_order_invalid'),
    ('domain', 'failed', 'search_interval_domain_mismatch'),
    ('domain_bool', 'failed', 'search_interval_domain_invalid'),
    ('missing_domain', 'unknown', 'search_interval_domain_missing'),
    ('unknown_clock', 'unknown', 'search_interval_clock_unsupported'),
    ('clock', 'failed', 'search_interval_clock_mismatch'),
])
def test_consistency_measured_interval_boundaries(mutation, expected, reason):
    """手計算例: search60ns ⊆ wire180ns ⊆ external300ns。長さだけの照合を防ぐ。"""
    from harness.protocol import clock_descriptor
    from harness.search_budget import validate_search_interval

    clock = clock_descriptor()
    record = {'command': 'genmove B', 'command_index': 3, 'command_id': 'unit',
              'clock': clock, 'clock_domain_id': 'unit-domain', 'exchange_start_ns': 100,
              'send_start_ns': 120, 'response_read_end_ns': 300, 'validation_end_ns': 400,
              'external_response_ns': 300, 'wire_envelope_ns': 180, 'seconds': 300 / 1e9,
              'limit_ns': 1000}
    header = {'format_version': 3, 'clock_descriptor': copy.deepcopy(clock),
              'host_clock': copy.deepcopy(clock), 'clock_domain_id': 'unit-domain'}
    event = {'command_index': 3, 'command_id': 'unit', 'command_received_ns': 150,
             'search_start_ns': 180, 'search_end_ns': 240, 'response_ready_ns': 260,
             'search_elapsed_ns': 60}
    assert validate_search_interval(header, event, record)['status'] == 'verified'
    if mutation == 'zero':
        event.update(command_received_ns=120, search_start_ns=120, search_end_ns=120,
                     response_ready_ns=120, search_elapsed_ns=0)
    elif mutation == 'elapsed':
        event['search_elapsed_ns'] = 61
    elif mutation == 'before':
        event.update(command_received_ns=0, search_start_ns=10, search_end_ns=70, response_ready_ns=80)
    elif mutation == 'after':
        event.update(command_received_ns=300, search_start_ns=310, search_end_ns=370, response_ready_ns=380)
    elif mutation == 'order':
        event['command_received_ns'] = 200
    elif mutation in ('bool', 'float'):
        event['search_start_ns'] = True if mutation == 'bool' else 180.0
    elif mutation == 'domain':
        header['clock_domain_id'] = 'other'
    elif mutation == 'domain_bool':
        header['clock_domain_id'] = True
        record['clock_domain_id'] = True
    elif mutation == 'missing_domain':
        del header['clock_domain_id']
    elif mutation == 'unknown_clock':
        header['clock_descriptor']['python_version'] = [3, 12]
    elif mutation == 'clock':
        header['clock_descriptor']['resolution'] *= 2
    result = validate_search_interval(header, event, record)
    assert result == {'status': expected, 'reason': reason}


@pytest.mark.parametrize('state', ['unknown', 'failed'])
def test_consistency_original_hold_is_not_automatically_recovered(integrity_report, state):
    """三階層が一貫した取得時holdも、raw再検証だけでは更正しない。"""
    report = copy.deepcopy(integrity_report)
    for game in report['games']:
        game['players']['A']['budget_trace']['status'] = state
        game['budget']['A']['status'] = state
        game['budget_status'] = state
    report['status']['budget'] = state
    inspection = contracts.inspect_evaluation(report)
    assert inspection['budget']['status'] == state
    assert inspection['source_consistency']['status'] == 'verified'
    assert any(item['code'] == 'acquisition_' + state + '_requires_explicit_review'
               for item in inspection['reasons'])
    assert analysis.estimate(report)['interval'] is None


@pytest.mark.parametrize('mutation', ['missing', 'command_index', 'board'])
def test_consistency_probe_host_transcript_is_required(integrity_report, mutation):
    """raw hashと同じ着手だけではprobe検証済みにならず、実際の通信列が必要。"""
    report = copy.deepcopy(integrity_report)
    check = report['determinism']['A']
    if mutation == 'missing':
        del check['attempts']
    elif mutation == 'command_index':
        next(item for item in check['attempts'][0]['commands']
             if item['command'].startswith('genmove'))['command_index'] += 1
    else:
        check['attempts'][0]['commands'][-2]['response'] = ['EEEEEEEE'] * 8
    result = analysis.estimate(report)
    assert result['interval'] is None
    assert any(item['code'].startswith('determinism_') for item in result['verification']['reasons'])


def test_consistency_probe_time_failure_is_in_timing_axis(integrity_report, tmp_path):
    """通常対局が正しくても、必須probeの時間矛盾を全時間verifiedへ集約しない。"""
    report = _relocated_report(integrity_report, tmp_path)
    artifact = report['determinism']['A']['trace_artifacts'][0]
    path = Path(report['evidence']['root']) / artifact['path']
    events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    next(event for event in events if event.get('type') == 'genmove')['search_elapsed_ns'] += 1
    path.write_text('\n'.join(canonical_json(event) for event in events) + '\n', encoding='utf-8')
    artifact['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    inspection = contracts.inspect_evaluation(report)
    assert inspection['timing']['status'] == 'failed'
    assert analysis.estimate(report)['interval'] is None


def test_consistency_reducer_has_exact_integer_metrics():
    """5ns/15nsからcount2・sum20・max15・mean10nsを独立に確認する。"""
    game = {'pair_id': 'unit', 'leg': 0, 'game_status': 'incomplete', 'protocol_status': 'failed',
            'players': {}, 'colors': {'B': 'A', 'W': 'B'}, 'error': None}
    series = {('unit', 0): {'A': {'status': 'verified', 'measurements': [
        {'search_elapsed_ns': 5, 'phase': 'normal_move'},
        {'search_elapsed_ns': 15, 'phase': 'terminal_probe'}]},
        'B': {'status': 'exempt', 'measurements': []}}}
    summary = contracts.reduce_observations([game], series)
    assert summary['A']['search_samples'] == 2
    assert summary['A']['search_sum_ns'] == 20
    assert summary['A']['search_max_ns'] == 15
    assert summary['A']['mean_search_seconds'] == 10 / 1e9
    assert summary['A']['normal_search_samples'] == 1
    assert summary['A']['normal_search_sum_ns'] == 5
    assert summary['B']['search_metric_status'] == 'not_required'
    assert summary['B']['mean_search_seconds'] is None
    assert summary['completed'] == 0


@pytest.mark.parametrize('mutation', ['null', 'list', 'schema_bool', 'profile', 'spec', 'nan',
                                     'targets', 'runtime', 'summary', 'hashes', 'manifest', 'search'])
def test_consistency_malformed_sources_fail_all_public_entries(integrity_report, mutation):
    """不正入力をどの公開入口でも正式CI不要/資格ありとして扱わない。"""
    report = copy.deepcopy(integrity_report)
    if mutation == 'null':
        report = None
    elif mutation == 'list':
        report = []
    elif mutation == 'schema_bool':
        report['schema_version'] = True
    elif mutation == 'profile':
        report['conditions']['evaluation_profile'] = 'unknown'
    elif mutation == 'spec':
        report['analysis_spec'] = []
    elif mutation == 'nan':
        report['summary']['A']['mean_search_seconds'] = float('nan')
    elif mutation in ('targets', 'runtime'):
        report[mutation] = []
    elif mutation == 'summary':
        report['summary']['A'] = []
    elif mutation == 'hashes':
        report['conditions']['budget_config_hashes'] = []
    elif mutation == 'manifest':
        report['conditions']['selection_manifest'] = []
    else:
        report['conditions']['budget_configs']['A']['search'] = []
    assert contracts.inspect_evaluation(report)['input_status'] == 'invalid'
    assert not analysis.statistical_eligibility(report)[0]
    assert analysis.estimate(report)['status'] == 'unavailable'
    assert analysis.paired(report, integrity_report)['status'] == 'unavailable'
    assert not compare_reports(report, integrity_report)['pair_statistics_available']
    assert analysis.reanalysis_report(report)['statistics']['status'] == 'unavailable'


def test_consistency_formal_entry_closure_and_arithmetic_boundary(integrity_report):
    """caller製CI・保存Inspection・builder上書きから正式資格へ迂回できない。"""
    report = copy.deepcopy(integrity_report)
    report['summary']['A']['search_samples'] = 0
    inspection = contracts.inspect_evaluation(report)
    assert inspection['metrics']['status'] == 'failed'
    single = analysis.estimate(report)
    reanalysis = analysis.reanalysis_report(report)['statistics']
    difference = analysis.paired(report, integrity_report)
    comparison = compare_reports(report, integrity_report)
    assert single['assessment']['formal_reason'] == reanalysis['assessment']['formal_reason']
    assert difference['assessment']['formal_reason'] == comparison['assessment']['formal_reason']
    assert single['assessment']['formal_reason'] == difference['assessment']['formal_reason']
    with pytest.raises(ValueError, match='caller_comparison'):
        analysis.comparison_report(report, integrity_report, {'pair_statistics_available': True})
    assert not analysis.statistical_eligibility(inspection)[0]
    arithmetic = estimate_pair_scores(integrity_report['games'], 4, eligible=True)
    assert arithmetic['status'] == 'computed'
    assert arithmetic['formal_eligibility'] == 'not_assessed'
    assert arithmetic['result_scope'] == 'arithmetic_only'
    with pytest.raises(ValueError, match='caller_comparison'):
        analysis.comparison_report(report, integrity_report, arithmetic)


def test_consistency_legacy_source_preserves_reference_without_time_upgrade(integrity_report):
    """旧sourceの秒から端点を捏造せず、原報告と過去CIを保全して現在資格を保留する。"""
    report = copy.deepcopy(integrity_report)
    report['schema_version'] = 3
    for game in report['games']:
        for player in game['players'].values():
            for record in player['commands']:
                for key in list(record):
                    if key.endswith('_ns') or key in ('command_index', 'command_id', 'clock', 'clock_domain_id'):
                        del record[key]
    original = copy.deepcopy(report)
    result = analysis.reanalysis_report(report)
    assert result['schema_version'] == 5
    assert result['statistics']['interval'] is None
    assert result['statistics']['verification']['status'] == 'unknown'
    assert result['statistics']['assessment']['reference_available']
    assert report == original
    assert not compare_reports(report, report)['pair_statistics_available']


@pytest.mark.parametrize('roots', [[], [None], [None, None, None], 'ab'])
def test_consistency_pair_roots_require_exactly_two(integrity_report, roots):
    """zip切捨てや文字列の二文字を二つの証跡rootと誤認しない。"""
    with pytest.raises(ValueError, match='two evidence roots'):
        analysis.paired(integrity_report, integrity_report, evidence_roots=roots)


@pytest.mark.parametrize('phase', ['final', 'comparison_final', 'reanalysis_final'])
@pytest.mark.parametrize('failure', ['open', 'serialize', 'write', 'flush', 'fsync'])
def test_consistency_shared_writer_classifies_operational_failures(tmp_path, monkeypatch, phase, failure):
    """各出力経路は共通writerで運用障害を分類し、phaseと原原因を保持する。"""
    path = tmp_path / 'output.json'
    payload = {'value': 1}
    if failure == 'open':
        def fail_open(*arguments, **keywords):
            """排他的作成時の権限エラーを再現する。"""
            raise PermissionError('denied')
        monkeypatch.setattr(Path, 'open', fail_open)
    elif failure == 'serialize':
        payload['value'] = object()
    elif failure in ('write', 'flush'):
        from contextlib import contextmanager
        from unittest.mock import Mock
        original_open = Path.open

        @contextmanager
        def failing_stream(path, *arguments, **keywords):
            """実ファイルを閉じつつ、指定したwrite/flushだけ故障させる。"""
            with original_open(path, *arguments, **keywords) as writer:
                proxy = Mock(wraps=writer)
                getattr(proxy, failure).side_effect = OSError('no space')
                yield proxy

        monkeypatch.setattr(Path, 'open', failing_stream)
    else:
        def fail_sync(*arguments):
            """write後のfsync失敗も成功扱いにしない。"""
            raise OSError('no space')
        monkeypatch.setattr(contracts.os, 'fsync', fail_sync)
    with pytest.raises(contracts.ArtifactWriteError) as caught:
        contracts.write_report_exclusive(path, payload, phase, 'unit')
    assert caught.value.phase == phase
    assert caught.value.operation_id == 'unit'
    assert caught.value.__cause__ is caught.value.cause


def test_consistency_writer_race_preserves_existing_asset(tmp_path):
    """preflight後に他者が作成した出力は、exclusive writerで保護する。"""
    path = tmp_path / 'winner.json'
    path.write_text('original', encoding='utf-8')
    with pytest.raises(contracts.ArtifactWriteError):
        contracts.write_report_exclusive(path, {'replacement': True})
    assert path.read_text(encoding='utf-8') == 'original'


def test_consistency_seed_adapter_is_in_acquisition_fingerprint():
    """seed適用コードを取得依存から漏らさない。ただし全AI依存の網羅保証ではない。"""
    files = evaluate_module._harness_info()
    assert any(Path(item['path']).name == 'seeded_player.py' for item in files)


@pytest.mark.parametrize('mutation', ['schema', 'conditions', 'nan'])
@pytest.mark.parametrize('entry', ['analysis', 'comparison'])
def test_consistency_cli_invalid_root_is_exit_two(integrity_report, tmp_path, mutation, entry):
    """不正rootは処理開始前に2で拒否し、診断可能なstderrと原資料を保全する。"""
    report = copy.deepcopy(integrity_report)
    if mutation == 'schema':
        report['schema_version'] = 99
    elif mutation == 'conditions':
        report['conditions'] = []
    else:
        report['summary']['A']['mean_search_seconds'] = float('nan')
    source = tmp_path / 'source.json'
    original = json.dumps(report)
    source.write_text(original, encoding='utf-8')
    output = tmp_path / 'result.json'
    command = [sys.executable, '-B', '-m', 'harness.' + ('analysis' if entry == 'analysis' else 'evaluate')]
    command.extend(['--report', str(source)] if entry == 'analysis' else ['--compare', str(source), str(source)])
    command.extend(['--output', str(output)])
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert result.stderr
    assert not output.exists()
    assert source.read_text(encoding='utf-8') == original


def test_consistency_raw_nonfinite_is_evidence_failure_not_input(integrity_report, tmp_path):
    """rootは有限のまま、新hash付きrawの追加NaNを検証失敗として拒否する。"""
    report = _relocated_report(integrity_report, tmp_path)
    artifact = report['games'][0]['players']['A']['budget_trace']['artifact']
    path = Path(report['evidence']['root']) / artifact['path']
    events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    events[1]['extra'] = float('nan')
    path.write_text('\n'.join(json.dumps(event) for event in events) + '\n', encoding='utf-8')
    artifact['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    inspection = contracts.inspect_evaluation(report)
    assert inspection['input_status'] == 'valid'
    assert inspection['budget']['status'] == 'failed'
    assert not analysis.estimate(report)['assessment']['request_success']


def test_consistency_second_player_start_failure_keeps_roles_and_cleanup(tmp_path, monkeypatch):
    """Aのみ起動後にBが失敗しても、B欠損を適用除外にせずAを回収する。"""
    original_player = evaluate_module.Player
    started = []

    def fail_second(*arguments, **keywords):
        """各legの二番目のPopenだけを模擬失敗させ、実Aを記録する。"""
        if len(started) % 2 == 1:
            started.append(None)
            raise HarnessError('abnormal_exit', 'B start denied')
        player = original_player(*arguments, **keywords)
        started.append(player)
        return player

    monkeypatch.setattr(evaluate_module, 'Player', fail_second)
    report = evaluate(SCRIPT, SCRIPT, games=2, evidence_dir=tmp_path / 'runs')
    for game in report['games']:
        assert set(game['players']) == {'A', 'B'}
        assert game['players']['B']['launch_status'] == 'failed'
        assert game['budget_status'] != 'exempt'
        assert game['protocol_status'] == 'failed'
    assert all(player.process.poll() is not None and all(not reader.is_alive() for reader in player._readers)
               for player in started if player is not None)


def test_consistency_prepare_failure_is_after_preflight(tmp_path, monkeypatch):
    """Popen前でも入力確認後のmkdir失敗は、APIのprepare運用障害となる。"""
    root = tmp_path / 'runs'
    original_mkdir = Path.mkdir

    def deny_prepare(path, *arguments, **keywords):
        """run領域だけを拒否し、pytestの既存一時領域を壊さない。"""
        if path.parent == root:
            raise PermissionError('prepare denied')
        return original_mkdir(path, *arguments, **keywords)

    monkeypatch.setattr(Path, 'mkdir', deny_prepare)
    with pytest.raises(contracts.ArtifactWriteError) as caught:
        evaluate(SCRIPT, SCRIPT, games=2, evidence_dir=root)
    assert caught.value.phase == 'prepare'
    assert isinstance(caught.value.cause, PermissionError)


def test_consistency_config_write_is_still_prepare(tmp_path, monkeypatch):
    """run作成後の設定書込もPopen前のprepareであり、入力不正へ戻さない。"""
    def denied_config(config, path):
        """専用一時設定の作成失敗を模擬する。"""
        raise PermissionError('config prepare denied')

    monkeypatch.setattr(evaluate_module.search_budget, 'write_config', denied_config)
    with pytest.raises(contracts.ArtifactWriteError) as caught:
        evaluate(SCRIPT, SCRIPT, games=2, evidence_dir=tmp_path / 'runs')
    assert caught.value.phase == 'prepare'


@pytest.mark.parametrize('field,value,state', [
    ('method', 'unregistered_method', 'unsupported'),
    ('estimand', 'different_population', 'invalid'),
])
def test_consistency_analysis_spec_is_not_silently_reinterpreted(integrity_report, field, value, state):
    """未知手法や別の推定対象を、既定の正式推定に黙って置き換えない。"""
    report = copy.deepcopy(integrity_report)
    report['analysis_spec'][field] = value
    inspection = contracts.inspect_evaluation(report)
    assert inspection['input_status'] == state
    assert analysis.estimate(report)['status'] == 'unavailable'
    assert not compare_reports(report, integrity_report)['pair_statistics_available']


def test_consistency_secondary_checkpoint_error_preserves_primary(tmp_path, monkeypatch):
    """中断の保存にも失敗した場合、二次障害で本来の原因を置き換えない。"""
    original_progress = evaluate_module._write_progress

    def primary_failure(*arguments, **keywords):
        """対局取得の主障害を模擬する。プロセス起動の証明には使わない。"""
        raise RuntimeError('primary collection failure')

    def secondary_failure(root, report, games, planned, state, *arguments, **keywords):
        """partialの保存だけを拒否し、既存checkpointは残す。"""
        if state == 'partial':
            raise contracts.ArtifactWriteError('checkpoint', root, report['run_id'], PermissionError('secondary'))
        return original_progress(root, report, games, planned, state, *arguments, **keywords)

    monkeypatch.setattr(evaluate_module, 'play_game', primary_failure)
    monkeypatch.setattr(evaluate_module, '_write_progress', secondary_failure)
    with pytest.raises(RuntimeError, match='primary collection failure') as caught:
        evaluate(SCRIPT, SCRIPT, games=2, evidence_dir=tmp_path / 'runs')
    assert any('secondary_checkpoint_failure' in note for note in caught.value.__notes__)
    assert list((tmp_path / 'runs').glob('*/run-state.json'))


def test_consistency_persisted_strict_requirement_is_not_weakened(integrity_report):
    """保存されたstrict要求を、公開入口側でflagを省略して弱めない。"""
    report = copy.deepcopy(integrity_report)
    report['verification_requirements']['require_budget_verification'] = True
    report['schema_version'] = 3
    assert not compare_reports(report, report)['comparable']
    assert not analysis.reanalysis_report(report)['statistics']['assessment']['request_success']


@pytest.mark.parametrize('status,start,end,elapsed,expected', [
    ('not_required', None, None, 0, 'verified'),
    ('not_required', None, None, 1, 'failed'),
    ('command', 120, 160, 40, 'verified'),
    ('command', 190, 230, 40, 'failed'),
    ('command', 120, 160, 41, 'failed'),
    ('undefined', None, None, 0, 'unknown'),
])
def test_consistency_model_load_is_separate_measured_interval(status, start, end, elapsed, expected):
    """要求[100,300]のload[120,160]はsearch[180,240]と重ならない。"""
    from harness.search_budget import _model_load_contract
    info = {'model_id': 'model', 'model_load_status': status,
            'model_load_start_ns': start, 'model_load_end_ns': end, 'model_load_elapsed_ns': elapsed}
    assert _model_load_contract(info, 100, 300, (180, 240))[0] == expected


def test_consistency_checkpoint_failure_leaves_started_processes_closed(tmp_path, monkeypatch):
    """1leg回収後のcheckpoint fsync失敗でも、実際に起動した両process/threadが残らない。"""
    original_player = evaluate_module.Player
    original_sync = evaluate_module.os.fsync
    started = []
    calls = 0

    def record_player(*arguments, **keywords):
        """実Playerを記録して、例外後のprocessとreader状態を確認可能にする。"""
        player = original_player(*arguments, **keywords)
        started.append(player)
        return player

    def fail_third_sync(descriptor):
        """not_started/runningは保存し、1leg終了後のpartial保存だけを失敗させる。"""
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError('checkpoint fsync denied')
        return original_sync(descriptor)

    monkeypatch.setattr(evaluate_module, 'Player', record_player)
    monkeypatch.setattr(evaluate_module.os, 'fsync', fail_third_sync)
    with pytest.raises(contracts.ArtifactWriteError) as caught:
        evaluate(SCRIPT, SCRIPT, games=2, evidence_dir=tmp_path / 'runs')
    assert caught.value.phase == 'checkpoint'
    assert len(started) == 2
    assert all(player.process.poll() is not None and all(not reader.is_alive() for reader in player._readers)
               for player in started)
    assert list((tmp_path / 'runs').glob('*/run-state.json'))


@pytest.mark.parametrize('location', ['report_size', 'conditions_size', 'manifest_size',
                                     'purpose', 'placements', 'selected', 'sample_size', 'algorithm'])
def test_population_aliases_cannot_create_false_census(integrity_report, location):
    """保存母集合と報告aliasが矛盾した場合、各公開解析入口から偽の幅0区間を出さない。"""
    report = copy.deepcopy(integrity_report)
    if location == 'report_size':
        report['population']['size'] = 1
    elif location == 'conditions_size':
        report['conditions']['opening_population_size'] = 1
    elif location == 'manifest_size':
        report['conditions']['selection_manifest']['population_size'] = 1
    elif location in ('purpose', 'placements'):
        report['population'][location] = 'development' if location == 'purpose' else 1
    elif location == 'selected':
        report['population']['selected_case_ids'] = []
    else:
        report['conditions']['selection_manifest'][location if location != 'algorithm' else 'selection_algorithm'] = 2
    assert contracts.validate_report(report)
    assert analysis.estimate(report)['interval'] is None
    assert analysis.paired(report, integrity_report)['status'] == 'unavailable'
    assert not compare_reports(report, integrity_report)['pair_statistics_available']


@pytest.mark.parametrize('size', [None, True, 4.0, 0, -1])
def test_population_size_requires_present_strict_positive_int(integrity_report, size):
    """Pythonのbool/floatと整数の等値比較だけでNの型制約を通さない。"""
    report = copy.deepcopy(integrity_report)
    report['population']['size'] = size
    assert analysis.estimate(report)['status'] == 'unavailable'


@pytest.mark.parametrize('mutation', ['null', 'board', 'response', 'phase', 'pair', 'budget',
                                     'scope', 'clock', 'model', 'missing', 'extra', 'depth', 'fallback'])
def test_saved_trace_semantics_block_formal_statistics_even_with_new_hash(integrity_report, tmp_path, mutation):
    """hashを更新した不正rawでも、旧verifiedを根拠に正式統計へ採用できない。"""
    record_expectation('trace-rejection', inputs={'mutation': mutation, 'hash_recalculated': True},
                       expected={'budget_status': 'unknown' if mutation in ('scope', 'clock') else 'failed',
                                 'statistics': 'unavailable', 'eligible': False}, expectation_kind='fixed',
                       model='docs/harness_architecture.md:INS/AE/NoFormal', contract='docs/harness_contracts.md:6/8',
                       implementation='harness.contracts.inspect_evaluation/harness.analysis.estimate')
    report = _relocated_report(integrity_report, tmp_path)

    def mutate(events):
        """指定された意味違反または欠落/余分イベントを複製traceへ注入する。"""
        if mutation == 'null':
            return [None]
        if mutation in ('scope', 'clock', 'model', 'pair'):
            key = {'scope': 'measurement_scope', 'clock': 'clock', 'model': 'model_id', 'pair': 'pair_id'}[mutation]
            events[0][key] = 'undefined'
        elif mutation == 'missing':
            removed = next(event for event in events if event.get('type') == 'genmove')
            return [event for event in events if event is not removed]
        elif mutation == 'extra':
            return events + [next(event for event in events if event.get('type') == 'genmove')]
        else:
            key = {'board': 'board_sha256', 'response': 'response', 'phase': 'phase',
                   'budget': 'nodes', 'depth': 'completed_depth', 'fallback': 'fallback'}[mutation]
            next(event for event in events if event.get('type') == 'genmove')[key] = {
                'board': '0' * 64, 'response': '0 0', 'phase': 'terminal_probe',
                'budget': 101, 'depth': 3, 'fallback': True}[mutation]
        return events

    # hashも合わせ、hash不一致検査だけによる拒否ではなく意味検証を試す。
    _rewrite_trace(report, mutate)
    inspection = contracts.inspect_evaluation(report)
    record_observation('trace-rejection', {'budget_status': inspection['budget']['status'],
                                         'reason_codes': sorted({reason['code'] for reason in inspection['reasons']}),
                                         'reason_count': len(inspection['reasons']), 'reason_details': 'not_collected'})
    assert inspection['budget']['status'] == ('unknown' if mutation in ('scope', 'clock') else 'failed')
    statistics_status = analysis.estimate(report)['status']
    record_observation('trace-rejection', {'statistics': statistics_status})
    assert statistics_status == 'unavailable'
    eligible = analysis.statistical_eligibility(report)[0]
    record_observation('trace-rejection', {'eligible': eligible})
    assert not eligible
    assert analysis.paired(report, integrity_report)['status'] == 'unavailable'
    assert not compare_reports(report, integrity_report)['budget_comparable']


@pytest.mark.parametrize('mutation', ['player', 'command', 'order', 'phase', 'board', 'timing', 'exit', 'score'])
def test_saved_game_and_protocol_are_validated_together(integrity_report, mutation):
    """一見保存済みの成功局でも、双方通信・棋譜・時間・得点の矛盾ならCIを停止する。"""
    report = copy.deepcopy(integrity_report)
    game = report['games'][0]
    player = game['players']['A']
    if mutation == 'player':
        del game['players']['B']
    elif mutation == 'command':
        player['commands'].pop(3)
    elif mutation == 'order':
        player['commands'][1:3] = player['commands'][1:3][::-1]
    elif mutation == 'board':
        player['commands'][1]['response'] = ['EEEEEEEE'] * 8
    elif mutation == 'timing':
        player['commands'][1]['seconds'] = 2
    elif mutation == 'exit':
        player['exit_code'] = 1
    elif mutation == 'score':
        game['score_a'] = 1 - game['score_a'] if game['score_a'] != 0.5 else 1
    else:
        next(command for command in player['commands'] if command['command'].startswith('genmove'))['phase'] = 'terminal_probe'
    assert analysis.estimate(report)['interval'] is None


def test_missing_population_and_trace_never_pass_joint_gate(integrity_report, tmp_path):
    """母集合だけverifiedでもtrace欠損はunknownとし、正式区間のAND条件を満たさない。"""
    report = _relocated_report(integrity_report, tmp_path)
    artifact = report['games'][0]['players']['A']['budget_trace']['artifact']
    (Path(report['evidence']['root']) / artifact['path']).unlink()
    inspection = contracts.inspect_evaluation(report)
    assert inspection['population']['status'] == 'verified'
    assert inspection['budget']['status'] == 'unknown'
    assert analysis.estimate(report)['interval'] is None
    (Path(report['evidence']['root']) / report['evidence']['population']['path']).unlink()
    assert analysis.estimate(report)['interval'] is None


def test_direct_paired_requires_mutual_conditions(integrity_report):
    """各報告の個別検証に通っても、runtimeの異なる直接paired呼出は拒否する。"""
    changed = copy.deepcopy(integrity_report)
    changed['runtime']['machine'] = 'other'
    changed['experiment_id'] = evaluate_module._experiment_id(
        changed['conditions'], changed['runtime'], changed['targets'], changed['evaluation_fingerprint'],
    )
    assert contracts.inspect_evaluation(changed)['protocol']['status'] == 'verified'
    assert analysis.paired(integrity_report, changed)['status'] == 'unavailable'
    assert 'runtime' in compare_reports(integrity_report, changed)['condition_mismatches']


def test_evidence_root_override_preserves_source_identity(integrity_report, tmp_path):
    """証跡の移動先指定を、原報告内容/hashの変更や検証省略と混同しない。"""
    copied = _relocated_report(integrity_report, tmp_path)
    report = copy.deepcopy(integrity_report)
    report['evidence']['root'] = str(tmp_path / 'absent')
    expected_hash = contracts.digest(report)
    inspection = contracts.inspect_evaluation(report, copied['evidence']['root'])
    assert inspection['status'] == 'verified'
    assert inspection['protocol']['status'] == inspection['budget']['status'] == 'verified'
    assert inspection['source_report_sha256'] == expected_hash
    assert analysis.estimate(report, evidence_root=copied['evidence']['root'])['status'] == 'available'
    assert analysis.reanalysis_report(report, evidence_root=copied['evidence']['root'])['statistics']['assessment']['reference_available']


def test_census_requires_all_archived_cases_and_both_legs(tmp_path):
    """検証済み全archive/両legでのみ正式全数区間を出し、欠落後は出さない。"""
    population = generate_population(2, 8, 914)
    path = tmp_path / 'population.json'
    path.write_text(json.dumps(population), encoding='utf-8')
    config = {'format_version': 2, 'budget': {'kind': 'depth', 'limit_depth': 1},
              'search': {'deterministic': True}}
    report = evaluate(BUDGET_FIXTURE, BUDGET_FIXTURE, games=4, openings_file=path, selection_seed=1,
                      config_a=config, config_b=config, budget_policy='equal-work',
                      evaluation_profile='confirmatory', evidence_dir=tmp_path / 'runs')
    result = analysis.estimate(json.loads(json.dumps(report)))
    inspection = contracts.inspect_evaluation(report)
    assert inspection['population']['status'] == inspection['coverage']['status'] == 'verified'
    assert result['status'] == 'available'
    assert result['coverage_kind'] == 'census_verified'
    interval = estimate_pair_scores(inspection['derived']['games'], 2)['interval']
    assert interval['method'] == 'finite_population_census'
    # 同じ決定的模擬AIの先後交換ペアでは、勝敗にかかわらずA平均は0.5。
    assert interval['lower'] == interval['upper'] == 0.5
    report['games'].pop()
    assert analysis.estimate(report)['interval'] is None


def test_unapproved_none_cannot_be_verified_by_valid_trace(integrity_report):
    """有効rawの存在をnoneの適用除外宣言・policy許可の代わりにしない。"""
    game = integrity_report['games'][0]
    player = game['players']['A']
    artifact = player['budget_trace']['artifact']
    path = Path(integrity_report['evidence']['root']) / artifact['path']
    config = {'format_version': 2, 'budget': {'kind': 'none'}, 'search': {}}
    assert validate_trace(path, config, player['commands'], integrity_report['targets']['A']['sha256'])['status'] == 'unknown'


def test_baseline_exemption_is_local_and_does_not_skip_candidate_trace(tmp_path):
    """Bの対局trace除外を、決定性probeやAの要求traceの免除へ拡張しない。"""
    population = generate_population(2, 8, 915)
    path = tmp_path / 'population.json'
    path.write_text(json.dumps(population), encoding='utf-8')
    candidate = {'format_version': 2, 'budget': {'kind': 'depth', 'limit_depth': 1},
                 'search': {'deterministic': True}}
    baseline = {'format_version': 2,
                'budget': {'kind': 'none', 'exemption': {'reason': 'fixed fixture baseline',
                           'target_sha256': hashlib.sha256(BUDGET_FIXTURE.read_bytes()).hexdigest()}},
                'search': {'deterministic': True}}
    report = evaluate(BUDGET_FIXTURE, BUDGET_FIXTURE, games=2, openings_file=path, selection_seed=1,
                      config_a=candidate, config_b=baseline, budget_policy='baseline-asymmetric',
                      evaluation_profile='confirmatory', evidence_dir=tmp_path / 'runs')
    assert contracts.inspect_evaluation(report)['budget']['status'] == 'verified'
    assert analysis.estimate(report)['status'] == 'available'
    artifact = report['games'][0]['players']['B']['budget_trace']['artifact']
    (Path(report['evidence']['root']) / artifact['path']).unlink()
    assert contracts.inspect_evaluation(report)['budget']['status'] == 'unknown'
    assert any(item['code'].startswith('trace_evidence_unavailable')
               for item in contracts.inspect_evaluation(report)['reasons'])
    probe_artifact = report['determinism']['B']['trace_artifacts'][0]
    probe_path = Path(report['evidence']['root']) / probe_artifact['path']
    original_bytes = probe_path.read_bytes()
    probe_path.write_text('null\n', encoding='utf-8')
    probe_artifact['sha256'] = hashlib.sha256(probe_path.read_bytes()).hexdigest()
    assert analysis.estimate(report)['status'] == 'unavailable'
    assert any(item['code'].startswith('determinism_trace_') for item in contracts.inspect_evaluation(report)['reasons'])
    probe_path.write_bytes(original_bytes)
    probe_artifact['sha256'] = hashlib.sha256(original_bytes).hexdigest()
    artifact = report['games'][0]['players']['A']['budget_trace']['artifact']
    (Path(report['evidence']['root']) / artifact['path']).unlink()
    assert analysis.estimate(report)['status'] == 'unavailable'
    assert contracts.inspect_evaluation(report)['budget']['status'] == 'unknown'


def test_saved_probe_is_revalidated_not_just_hash_checked(integrity_report, tmp_path):
    """probeのhashを新しい不正内容へ合わせても、保存出力/verifiedだけで資格を与えない。"""
    report = _relocated_report(integrity_report, tmp_path)
    artifact = report['determinism']['A']['trace_artifacts'][0]
    path = Path(report['evidence']['root']) / artifact['path']
    path.write_text('null\n', encoding='utf-8')
    artifact['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert analysis.estimate(report)['status'] == 'unavailable'


def test_reanalysis_does_not_launch_ai_or_require_original_validator(integrity_report, monkeypatch):
    """旧取得版の出典を保ち、新しい検証器によるconfidence変更を取得やAI再実行と分離する。"""
    report = json.loads(json.dumps(integrity_report))
    report['evaluation_fingerprint'] = 'historical-collector'
    report['experiment_id'] = evaluate_module._experiment_id(
        report['conditions'], report['runtime'], report['targets'], report['evaluation_fingerprint'],
    )

    def forbid_process(*args, **kwargs):
        """再解析中のPopen試行を直ちに試験失敗にする。"""
        pytest.fail('reanalysis must not start a player')

    monkeypatch.setattr(subprocess, 'Popen', forbid_process)
    first = analysis.reanalysis_report(report)
    second = analysis.reanalysis_report(report, confidence=0.95)
    assert first['statistics']['status'] == second['statistics']['status'] == 'available'
    assert first['statistics']['assessment']['reference_available']
    assert first['source'] == second['source']
    assert first['analysis_id'] != second['analysis_id']
    assert first['statistics']['analysis_spec']['confidence'] == 0.90


def test_reanalysis_cli_writes_separate_result_and_exits_nonzero_when_unavailable(integrity_report, tmp_path):
    """正常再解析0・不正Nの診断保存1・上書き拒否2と元JSON不変を実CLIで確認する。"""
    source = tmp_path / 'source.json'
    original = json.dumps(integrity_report)
    source.write_text(original, encoding='utf-8')
    output = tmp_path / 'analysis.json'
    command = [sys.executable, '-B', '-m', 'harness.analysis', '--report', str(source), '--output', str(output)]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['report_kind'] == 'analysis'
    assert result['statistics']['status'] == 'available'
    assert result['statistics']['assessment']['reference_available']
    assert source.read_text(encoding='utf-8') == original
    second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert second.returncode == 2
    report = copy.deepcopy(integrity_report)
    report['population']['size'] = 1
    source.write_text(json.dumps(report), encoding='utf-8')
    command[-1] = str(tmp_path / 'unavailable.json')
    failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert failed.returncode == 1
    assert json.loads((tmp_path / 'unavailable.json').read_text(encoding='utf-8'))['statistics']['interval'] is None


def test_initial_position_multiple_pairs_remain_descriptive_and_comparable(tmp_path):
    """固定母集合なしの複数repeatを偽N=1全数にせず、正常な記述的比較を維持する。"""
    report = evaluate(BUDGET_FIXTURE, BUDGET_FIXTURE, games=4, evidence_dir=tmp_path / 'runs')
    assert report['summary']['completed'] == 4
    assert contracts.validate_report(report) == []
    assert compare_reports(report, report)['comparable']
    assert analysis.estimate(report)['interval'] is None


def test_sampling_runtime_unknown_blocks_interval_without_claiming_corruption(integrity_report):
    """未対応Pythonでの抽出再現不能を、母集合内容の不正と区別してunknownにする。"""
    report = copy.deepcopy(integrity_report)
    report['runtime']['python'] = '2.7.18 unsupported'
    report['experiment_id'] = evaluate_module._experiment_id(
        report['conditions'], report['runtime'], report['targets'], report['evaluation_fingerprint'],
    )
    inspection = contracts.inspect_evaluation(report)
    assert inspection['status'] == 'unknown', inspection['reasons']
    assert inspection['population']['status'] == 'verified'
    assert inspection['selection']['status'] == 'unknown'
    assert analysis.estimate(report)['interval'] is None


def test_time_budget_comparison_accepts_different_legal_games_without_formal_ci(tmp_path):
    """異なる合法time棋譜の各生存期間を検証し、参考比較と固定得点CIを分ける。"""
    population = generate_population(2, 8, 916)
    path = tmp_path / 'population.json'
    path.write_text(json.dumps(population), encoding='utf-8')
    alternate = tmp_path / 'alternate.py'
    # 異なる合法棋譜を作る試験であり、非決定性自体の実証ではない。
    script = BUDGET_FIXTURE.read_text(encoding='utf-8').replace(
        'str(Path(__file__).resolve().parents[2])', repr(str(ROOT)),
    ).replace('position = legal[0]', 'position = legal[-1]')
    alternate.write_text(script, encoding='utf-8')
    config = {'format_version': 2, 'budget': {'kind': 'time', 'limit_ns': 5_000_000_000}, 'search': {}}
    arguments = dict(games=2, openings_file=path, selection_seed=1,
                     config_a=config, config_b=config, budget_policy='equal-time',
                     evaluation_profile='deployment', evidence_dir=tmp_path / 'runs')
    before = evaluate(BUDGET_FIXTURE, BUDGET_FIXTURE, **arguments)
    after = evaluate(alternate, BUDGET_FIXTURE, **arguments)
    assert before['games'][0]['moves'] != after['games'][0]['moves']
    for report in (before, after):
        inspection = contracts.inspect_evaluation(report)
        assert inspection['protocol']['status'] == inspection['budget']['status'] == 'verified'
        assert inspection['status'] == 'verified'
    comparison = compare_reports(before, after)
    assert comparison['comparable']
    assert comparison['condition_mismatches'] == []
    assert not comparison['pair_statistics_available']
    assert analysis.estimate(after)['interval'] is None


def test_each_artifact_is_read_once_per_inspection(integrity_report, monkeypatch):
    """同一解析内のhash照合とparseでファイルsnapshotが分かれないことを読込回数で確かめる。"""
    original_read = Path.read_bytes
    counts = {}
    root = Path(integrity_report['evidence']['root'])

    def count_reads(path):
        """証跡領域のread_bytes回数だけを数え、実際の読込処理へ委譲する。"""
        if path.is_relative_to(root):
            counts[path] = counts.get(path, 0) + 1
        return original_read(path)

    monkeypatch.setattr(Path, 'read_bytes', count_reads)
    assert contracts.inspect_evaluation(integrity_report)['protocol']['status'] == 'verified'
    assert counts
    assert all(count == 1 for count in counts.values())


@pytest.fixture(scope='module')
def smoke_reports():
    """同一seedの本体先後ペアを2回取得し、再現性と比較試験で共有する。"""
    return [evaluate(SCRIPT, SCRIPT, games=2, seed=31) for _ in range(2)]


@pytest.mark.smoke
def test_paired_complete_games_are_reproducible_and_serializable(smoke_reports):
    """同条件で棋譜が再現し、先後割当・A/B勝ち点率の補数関係・通信期限が保たれる。"""
    before, after = smoke_reports
    record_expectation('paired-smoke', inputs={'seed': 31, 'games_per_run': 2, 'runs': 2},
                       expected={'completed': 2, 'failed': 0, 'matching_moves_and_boards': True},
                       expectation_kind='consistency', model='docs/harness_architecture.md:COL/REPORT',
                       contract='docs/harness_contracts.md:4/7', implementation='harness.evaluate.evaluate')
    record_observation('paired-smoke', {'completed': before['summary']['completed'], 'failed': before['summary']['failed'],
                                      'positions_before': [[move['position'] for move in game['moves']] for game in before['games']],
                                      'positions_after': [[move['position'] for move in game['moves']] for game in after['games']]})
    assert before['summary']['completed'] == 2
    assert before['summary']['failed'] == 0
    assert before['summary']['A']['black_games'] == before['summary']['A']['white_games'] == 1
    assert before['summary']['A']['wins'] == before['summary']['B']['losses']
    assert before['summary']['A']['point_rate'] + before['summary']['B']['point_rate'] == 1
    for previous, current in zip(before['games'], after['games']):
        assert previous['result'] == current['result']
        assert [move['position'] for move in previous['moves']] == [move['position'] for move in current['moves']]
        assert previous['final_board'] == current['final_board']
        for player in previous['players'].values():
            assert player['exit_code'] == 0
            for command in player['commands']:
                if not command['startup_probe']:
                    assert command['seconds'] <= (10 if command['command'].startswith('genmove ') else 1)
    assert json.loads(json.dumps(before))['summary'] == before['summary']
    for report in smoke_reports:
        record_harness_artifacts(report)


def test_comparison_rejects_changed_conditions_or_opponent(smoke_reports):
    """seedと固定Bの変更を候補Aの改良差として解釈しない。"""
    before, after = smoke_reports
    assert compare_reports(before, after)['comparable']
    changed = copy.deepcopy(after)
    changed['conditions']['seed'] += 1
    changed['targets']['B']['sha256'] = 'different opponent'
    comparison = compare_reports(before, changed)
    assert not comparison['comparable']
    assert comparison['condition_mismatches'] == [
        'conditions', 'opponent.sha256', 'report_integrity_failed',
    ]


@pytest.mark.parametrize('games', [0, 1, 3, -2])
def test_evaluation_requires_balanced_game_count(games):
    """先後2legのペアを構成できない局数指定は取得前に拒否する。"""
    with pytest.raises(ValueError, match='even'):
        evaluate(SCRIPT, SCRIPT, games=games)


@pytest.mark.parametrize('budget,version,expected', [
    ({'kind': 'nodes', 'limit_nodes': 100.0}, 1, {'kind': 'nodes', 'limit_nodes': 100}),
    ({'kind': 'depth', 'limit_depth': 3.0}, 2, {'kind': 'depth', 'limit_depth': 3}),
    ({'kind': 'time', 'limit_ms': 0.001}, 1, {'kind': 'time', 'limit_ns': 1000}),
    ({'kind': 'time', 'limit_ns': 1}, 2, {'kind': 'time', 'limit_ns': 1}),
    ({'kind': 'unknown'}, 2, {'kind': 'unknown'}),
    ({'kind': 'none'}, 2, {'kind': 'none'}),
])
def test_budget_typing_preserves_normalization_and_opaque_search(budget, version, expected):
    """上限は手計算値を使い、未検証metadataをコピーや型変換で有効値にしない。"""
    marker = object()
    search = {'model_id': marker, 'extension': [None, True, {'opaque': marker}]}
    config = {'format_version': version, 'budget': budget, 'search': search}
    before = copy.deepcopy(budget)
    normalized = normalize_config(config)
    assert normalized == {'format_version': 2, 'budget': expected, 'search': search}
    assert normalized is not config and normalized['budget'] is not budget
    assert normalized['search'] is search and normalized['search']['model_id'] is marker
    assert budget == before


@pytest.mark.parametrize('kind,key,value,version,reason', [
    ('time', 'limit_ms', True, 1, 'positive finite number'),
    ('time', 'limit_ms', None, 1, 'positive finite number'),
    ('time', 'limit_ms', float('nan'), 1, 'positive finite number'),
    ('time', 'limit_ms', float('inf'), 1, 'positive finite number'),
    ('time', 'limit_ms', 0.0000001, 1, 'integer nanoseconds'),
    ('time', 'limit_ns', True, 2, 'positive integer'),
    ('time', 'limit_ns', 1.0, 2, 'positive integer'),
    ('nodes', 'limit_nodes', 1.5, 2, 'must be an integer'),
    ('depth', 'limit_depth', -1, 1, 'positive finite number'),
])
def test_budget_typing_preserves_limit_rejection(kind, key, value, version, reason):
    with pytest.raises(ValueError, match=reason):
        normalize_config({'format_version': version, 'budget': {'kind': kind, key: value}})


def test_budget_typing_preserves_exemption_identity_and_overflow():
    config = {'format_version': 2, 'budget': {'kind': 'none', 'exemption': {
        'reason': ' fixture ', 'target_sha256': 'AB' * 32}}, 'search': {}}
    result = normalize_config(config)
    assert result['budget']['exemption'] == {'reason': ' fixture ', 'target_sha256': 'ab' * 32}
    assert config['budget']['exemption']['target_sha256'] == 'AB' * 32
    with pytest.raises(OverflowError):
        normalize_config({'format_version': 1, 'budget': {'kind': 'nodes', 'limit_nodes': 10 ** 1000}})


@pytest.mark.parametrize('field,value,expected', [
    ('model_load_start_ns', None, ('unknown', 'model_load_endpoint_missing')),
    ('model_load_start_ns', True, ('failed', 'model_load_interval_invalid')),
    ('model_load_end_ns', 9, ('failed', 'model_load_interval_not_enclosed')),
    ('model_load_elapsed_ns', 9, ('failed', 'model_load_interval_not_enclosed')),
    ('model_id', None, ('failed', 'model_load_without_model')),
    ('model_load_status', 'unregistered', ('unknown', 'model_load_contract_unsupported')),
])
def test_budget_typing_preserves_model_load_states(field, value, expected):
    from harness.search_budget import _model_load_contract

    info = {'model_id': 'model', 'model_load_status': 'startup', 'model_load_start_ns': 10,
            'model_load_end_ns': 20, 'model_load_elapsed_ns': 10}
    info[field] = value
    before = copy.deepcopy(info)
    assert _model_load_contract(info, 0, 30, (21, 30)) == expected
    assert info == before


def test_budget_typing_preserves_model_overlap_and_raw_operand_failure():
    """局所的な型ビューが未検証区間を数値へ変換したり成功へ補修したりしない。"""
    from harness.search_budget import _model_load_contract

    info = {'model_id': 'model', 'model_load_status': 'startup', 'model_load_start_ns': 10,
            'model_load_end_ns': 20, 'model_load_elapsed_ns': 10}
    assert _model_load_contract(info, 0, 30, (20, 30)) == ('verified', None)
    assert _model_load_contract(info, 0, 30, (19, 30)) == ('failed', 'model_load_overlaps_search')
    with pytest.raises(TypeError):
        _model_load_contract(info, 0, 30, ('invalid', 30))


@pytest.mark.parametrize('field,value,expected', [
    ('search_start_ns', 3000, ('verified', None)),
    ('search_start_ns', None, ('unknown', 'search_interval_endpoint_missing')),
    ('search_start_ns', True, ('failed', 'search_interval_order_invalid')),
    ('search_start_ns', 2500, ('failed', 'search_interval_elapsed_mismatch')),
    ('search_end_ns', 7000, ('failed', 'search_interval_order_invalid')),
    ('search_elapsed_ns', 2000.0, ('failed', 'search_interval_elapsed_mismatch')),
])
def test_budget_typing_preserves_search_interval_and_inputs(field, value, expected):
    """同thread順序と親子包絡は別条件。期待nsは固定した整数から手計算する。"""
    from harness.search_budget import validate_search_interval

    clock = protocol.clock_descriptor()
    header = {'format_version': 4, 'clock_descriptor': clock, 'host_clock': clock,
              'clock_domain_id': 'typing-domain'}
    event = {'command_index': 0, 'command_id': 'typing-command', 'command_received_ns': 2000,
             'search_start_ns': 3000, 'search_end_ns': 5000, 'response_ready_ns': 6000,
             'search_elapsed_ns': 2000}
    record = {'clock': clock, 'clock_domain_id': 'typing-domain', 'command': 'genmove B',
              'command_index': 0, 'command_id': 'typing-command', 'exchange_start_ns': 0,
              'send_start_ns': 1000, 'response_read_end_ns': 9000, 'validation_end_ns': 10000,
              'external_response_ns': 10000, 'wire_envelope_ns': 8000, 'seconds': 0.00001,
              'limit_ns': 1_000_000_000}
    event[field] = value
    before = copy.deepcopy((header, event, record))
    assert validate_search_interval(header, event, record) == dict(zip(('status', 'reason'), expected))
    assert (header, event, record) == before


@pytest.mark.parametrize('source,issue', [
    (None, 'report_not_json_object'),
    ([], 'report_not_json_object'),
    ({'extension': float('nan')}, 'report_not_json_object'),
    ({'extension': object()}, 'report_not_json_object'),
    ({1: 'non-string-key'}, 'report_not_json_object'),
    ({'schema_version': True}, 'schema_type_invalid'),
    ({'report_kind': 'analysis'}, 'report_kind_mismatch'),
    ({'conditions': []}, 'conditions_not_object'),
    ({'summary': []}, 'summary_not_object'),
    ({'validation_contract_version': 3, 'decoded': {}}, 'inspection_is_not_source_report'),
])
def test_contract_typing_preserves_input_rejection(source, issue):
    decoded = contracts.decode_input(source)
    assert decoded['input_status'] == 'invalid' and decoded['issues'] == [issue]


def test_contract_typing_keeps_legacy_metadata_and_fresh_snapshot():
    """JSONとして正常な未知値を過剰型付けせず、返却側の変更を元資料へ戻さない。"""
    source = {'schema_version': 2, 'conditions': {'extension': {'number': 'opaque', 'array': (1, None)}},
              'summary': {}, 'games': []}
    before = copy.deepcopy(source)
    decoded = contracts.decode_input(source)
    assert decoded['input_status'] == 'valid'
    assert decoded['conditions'] == source['conditions']
    decoded['conditions']['extension']['number'] = 42
    assert source == before
    inspected = contracts.inspect_evaluation(source)
    assert inspected['status'] == 'unknown'
    assert inspected['snapshot'] == source and inspected['snapshot'] is not source
    inspected['snapshot']['conditions']['extension']['array'] = []
    assert source == before


@pytest.mark.parametrize('states,policy,expected', [
    ({}, 'equal-work', 'unknown'),
    ({'A': 'verified', 'B': 'verified'}, 'equal-work', 'verified'),
    ({'A': 'verified', 'B': 'unknown'}, 'equal-work', 'unknown'),
    ({'A': 'unknown', 'B': 'failed'}, 'equal-work', 'failed'),
    ({'A': 'verified', 'B': 'exempt'}, 'baseline-asymmetric', 'verified'),
    ({'A': 'verified', 'B': 'exempt'}, 'equal-work', 'failed'),
    ({'A': 'exempt', 'B': 'verified'}, 'baseline-asymmetric', 'failed'),
    ({'A': 'unregistered', 'B': 'verified'}, 'equal-work', 'unknown'),
])
def test_contract_typing_preserves_role_budget_precedence(states, policy, expected):
    assert contracts.aggregate_role_budget(states, {'budget_policy': policy}) == expected


@pytest.mark.parametrize('requested,profile,size,expected', [
    (None, 'smoke', 0, (False, None, 2, 'default_initial_pair')),
    (None, 'descriptive', 3, (False, None, 6, 'all_openings')),
    (2, 'confirmatory', 3, (True, 2, 2, 'explicit')),
    (6, 'deployment', 3, (True, 6, 6, 'explicit')),
])
def test_contract_typing_preserves_game_count_resolution(requested, profile, size, expected):
    assert contracts.resolve_game_count(requested, profile, size) == dict(zip(
        ('games_explicit', 'requested_games', 'resolved_games', 'resolution'), expected))


@pytest.mark.parametrize('requested,profile,size,reason', [
    (True, 'smoke', 0, 'positive even number'),
    (2.0, 'smoke', 0, 'positive even number'),
    (3, 'smoke', 0, 'positive even number'),
    (None, 'confirmatory', 3, 'requires explicit games'),
    (8, 'confirmatory', 3, 'exceed opening population'),
])
def test_contract_typing_preserves_game_count_rejection(requested, profile, size, reason):
    with pytest.raises(ValueError, match=reason):
        contracts.resolve_game_count(requested, profile, size)


def test_contract_typing_preserves_writer_error_identity(tmp_path):
    path = tmp_path / 'existing.json'
    path.write_bytes(b'original')
    with pytest.raises(contracts.ArtifactWriteError) as raised:
        contracts.write_report_exclusive(path, {'value': 1}, phase='typed-final', operation_id='operation')
    error = raised.value
    assert error.phase == 'typed-final' and error.path == str(path) and error.operation_id == 'operation'
    assert isinstance(error.cause, FileExistsError) and error.__cause__ is error.cause
    assert path.read_bytes() == b'original'


def test_contract_typing_preserves_none_get_failure():
    from harness.contracts import _get_container

    with pytest.raises(AttributeError) as native:
        getattr(None, 'get')
    with pytest.raises(AttributeError) as guarded:
        _get_container(None)
    assert str(guarded.value) == str(native.value)
    assert guarded.value.name == native.value.name == 'get'
    assert guarded.value.obj is native.value.obj is None


def test_contract_typing_preserves_get_container_identity():
    from harness.contracts import _get_container

    marker = object()
    source = {'unknown': marker}
    assert _get_container(source) is source and source['unknown'] is marker


@pytest.mark.parametrize('container', [list, tuple])
def test_contract_typing_temporal_reason_metadata_keeps_original_alias(container):
    marker = object()
    detail = {'extension': marker}
    reasons = container([{'status': 'unknown', 'code': 'unavailable', 'path': 'trace', 'detail': detail}])
    observed = protocol.temporal_result(reasons)
    assert observed['status'] == 'unknown' and observed['reasons'] is reasons
    assert observed['reasons'][0]['detail'] is detail and detail['extension'] is marker


def test_contract_typing_reference_metrics_do_not_convert_uncertified_ns():
    """参考cacheのfloatをintへ丸めず、正式な整数nsの証拠とも扱わない。"""
    game = {'game_status': 'complete', 'protocol_status': 'verified', 'error': None,
            'winner': 'A', 'result': 'B', 'colors': {'B': 'A', 'W': 'B'},
            'players': {'A': {'commands': []}, 'B': {'commands': []}},
            'budget': {'A': {'status': 'verified', 'measurements': [
                {'phase': 'normal_move', 'search_elapsed_ns': 1.5}]}}}
    summary = contracts.reduce_observations([game])
    assert type(summary['A']['search_sum_ns']) is float and summary['A']['search_sum_ns'] == 1.5
    assert summary['A']['search_metric_status'] == 'reference_only'
    assert summary['A']['mean_search_seconds'] == 1.5 / 1_000_000_000


def _protocol_typing_player():
    """実プロセスなしの通信fixture。pipe/stateは元Playerと同じ公開属性で差し替える。"""
    player = object.__new__(Player)
    player.commands = []
    player.stderr_tail = []
    player._output = queue.Queue()
    player._overflow = threading.Event()
    player._closed = False
    player._readers = []
    player.clock = {}
    player.clock_domain_id = 'scope'
    player.evaluation_context = {'process_session_id': 'session', 'process_generation': 0}
    player.lifecycle = dict(player.evaluation_context)
    player.command_timeout = 1.0
    player.genmove_timeout = 1.0
    player.process = SimpleNamespace(
        stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO(),
        poll=lambda: None, returncode=None, pid=123,
    )
    return player


@pytest.mark.parametrize('operation,pipe_name,attribute', [
    ('_read_stdout', 'stdout', 'readline'),
    ('_read_stderr', 'stderr', 'readline'),
    ('_exchange', 'stdin', 'write'),
    ('finish', 'stdin', 'write'),
    ('abort', 'stdin', 'close'),
])
def test_protocol_typing_preserves_missing_pipe_failure(operation, pipe_name, attribute):
    """Noneをskip/AssertionErrorへ変えず、例外属性と未完了cleanupを維持する。"""
    player = _protocol_typing_player()
    setattr(player.process, pipe_name, None)
    calls = []
    if operation == 'finish':
        player.abort = lambda: calls.append('abort')
    elif operation == 'abort':
        player.process.poll = lambda: 0
        player.lifecycle['exit_observed_ns'] = 1
    with pytest.raises(AttributeError) as failure:
        if operation == '_exchange':
            player._exchange('ask B 2 3', 1.0)
        else:
            getattr(player, operation)()
    assert str(failure.value) == "'NoneType' object has no attribute '{}'".format(attribute)
    assert failure.value.name == attribute and failure.value.obj is None
    assert player._closed is False and 'cleanup_end_ns' not in player.lifecycle
    assert calls == (['abort'] if operation == 'finish' else [])


@pytest.mark.parametrize('operation', ['_exchange', 'finish'])
def test_protocol_typing_does_not_cache_mutable_stdin(operation):
    """writeが公開stdin属性を変えたとき、旧実装と同じく次のflushで再参照する。"""
    player = _protocol_typing_player()
    player.abort = lambda: None

    class ReplacingWriter(io.StringIO):
        """書込後に次の属性参照先を消す診断用pipe。"""

        def write(self, value):
            """書込値は保持し、次のflushでNoneを観測させる。"""
            count = super().write(value)
            player.process.stdin = None
            player._output.put('OK\n')
            return count

    player.process.stdin = ReplacingWriter()
    with pytest.raises(AttributeError) as failure:
        if operation == '_exchange':
            player._exchange('ask B 2 3', 1.0)
        else:
            player.finish()
    assert failure.value.name == 'flush' and failure.value.obj is None


def test_protocol_typing_preserves_exchange_measurement_call_order(monkeypatch):
    """guardの採時追加や端点移動を検出する。setdefaultのdefault式も必ず評価される。"""
    player = _protocol_typing_player()
    events = []
    stamps = iter([100, 110, 140, 150, 160])

    def nanoseconds():
        """固定の採時列を返し、呼出位置を記録する。"""
        value = next(stamps)
        events.append(('ns', value))
        return value

    class Writer(io.StringIO):
        """要求書込後だけ応答queueへ行を供給する。"""

        def write(self, value):
            """実際の書込イベントと合法な応答を記録する。"""
            events.append(('write', value))
            player._output.put('OK\n')
            return super().write(value)

        def flush(self):
            """flushの採時相対位置を記録する。"""
            events.append(('flush',))

    player.process.stdin = Writer()
    player.process.poll = lambda: events.append(('poll',))
    player._validate = lambda command, lines: events.append(('validate', command, lines))
    monkeypatch.setattr(protocol, 'time', SimpleNamespace(
        perf_counter_ns=nanoseconds,
        perf_counter=lambda: events.append(('seconds',)) or 120 / 1e9,
    ))
    assert player._exchange('ask B 2 3', 1.0) == ['OK']
    assert events == [
        ('ns', 100), ('poll',), ('ns', 110), ('write', 'ask B 2 3\n'),
        ('flush',), ('seconds',), ('ns', 140),
        ('validate', 'ask B 2 3', ['OK']), ('ns', 150), ('ns', 160),
    ]
    record = player.commands[0]
    assert record['external_response_ns'] == 50 and record['wire_envelope_ns'] == 30
    assert record['seconds'] == 50 / 1e9


@pytest.mark.parametrize('platform,os_name', [('win32', 'nt'), ('linux', 'posix'), ('darwin', 'posix')])
def test_protocol_typing_preserves_os_abort_and_cleanup(monkeypatch, platform, os_name):
    """OSを模擬し、実PIDへ停止命令を出さずに既存のtree/kill/回収順を固定する。"""
    player = _protocol_typing_player()
    events = []
    stamps = iter([100, 200, 300])
    player.process.poll = lambda: player.process.returncode
    player.process.wait = lambda timeout: events.append(('wait', timeout))

    def kill():
        """Windowsのfallback停止を模擬する。"""
        events.append(('kill',))
        player.process.returncode = -9

    def kill_group(pid, signal):
        """POSIX停止は模擬だけで行い、実OSへ送信しない。"""
        events.append(('killpg', pid, signal))
        player.process.returncode = -9

    player.process.kill = kill
    monkeypatch.setattr(protocol, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setattr(protocol, 'os', SimpleNamespace(name=os_name, killpg=kill_group))
    monkeypatch.setattr(protocol, 'signal', SimpleNamespace(SIGKILL=9))
    monkeypatch.setattr(protocol, 'time', SimpleNamespace(perf_counter_ns=lambda: next(stamps)))
    monkeypatch.setattr(protocol, 'subprocess', SimpleNamespace(
        run=lambda command, **kwargs: events.append(('taskkill', command, kwargs)),
        TimeoutExpired=subprocess.TimeoutExpired,
    ))
    player.abort()
    expected = ([('taskkill', ['taskkill', '/PID', '123', '/T', '/F'],
                  {'capture_output': True, 'timeout': 3}), ('kill',)]
                if platform == 'win32' else [('killpg', 123, 9)])
    assert events == expected + [('wait', 3)]
    assert player._closed and all(pipe.closed for pipe in (
        player.process.stdin, player.process.stdout, player.process.stderr,
    ))
    assert player.lifecycle['abort_requested_ns'] == 100
    assert player.lifecycle['exit_observed_ns'] == 200
    assert player.lifecycle['cleanup_end_ns'] == 300
    assert player.lifecycle['termination_kind'] == 'killed'


@pytest.mark.parametrize('platform,os_name', [('win32', 'nt'), ('linux', 'posix'), ('darwin', 'posix')])
@pytest.mark.parametrize('spawn_error', [False, True])
def test_protocol_typing_preserves_spawn_endpoints_context_and_options(
        monkeypatch, platform, os_name, spawn_error):
    """Popenの前/戻り採時・失敗診断を固定し、contextを型付けで再構築しない。"""
    events = []
    stamps = iter([100, 200])
    extension = {'retain': [1]}
    context = {'clock_domain_id': 'domain', 'extension': extension}
    supplied = {}

    class ScriptPath:
        """実OSを変更せず、constructorに必要なパス操作だけを模擬する。"""

        def resolve(self):
            """同じ仮想パスを返す。"""
            return self

        @property
        def parent(self):
            """仮想cwdとして自身を返す。"""
            return self

        def __str__(self):
            """実ファイルを指さない文字列を返す。"""
            return 'virtual-player'

    def spawn(command, **kwargs):
        """起動を記録するだけで、実プロセスは作らない。"""
        events.append('spawn')
        supplied.update(kwargs)
        if spawn_error:
            raise OSError('spawn fixture')
        return SimpleNamespace(pid=7)

    def nanoseconds():
        """SP/PRまたはspawn_failureの順に固定値を返す。"""
        value = next(stamps)
        events.append(('ns', value))
        return value

    def exchange(self, command, timeout, startup=False):
        """nameの終了点だけを供給し、他の採時を追加しない。"""
        events.append(('name', command, startup))
        self.commands.append({'validation_end_ns': 400})
        return ['fixture']

    monkeypatch.setattr(protocol, 'Path', lambda value: ScriptPath())
    monkeypatch.setattr(protocol, 'sys', SimpleNamespace(platform=platform, executable='python'))
    monkeypatch.setattr(protocol, 'os', SimpleNamespace(name=os_name, environ={}))
    monkeypatch.setattr(protocol, 'clock_descriptor', lambda: {'opaque': 'clock'})
    monkeypatch.setattr(protocol, 'time', SimpleNamespace(
        perf_counter_ns=nanoseconds, perf_counter=lambda: 300 / 1e9,
    ))
    monkeypatch.setattr(protocol, 'threading', SimpleNamespace(
        Event=threading.Event, get_ident=lambda: 777,
        Thread=lambda **kwargs: SimpleNamespace(start=lambda: events.append('reader')),
    ))
    monkeypatch.setattr(protocol, 'subprocess', SimpleNamespace(
        Popen=spawn, PIPE=-1, CREATE_NEW_PROCESS_GROUP=512,
    ))
    monkeypatch.setattr(Player, '_exchange', exchange)
    if spawn_error:
        with pytest.raises(HarnessError) as failure:
            Player('virtual', evaluation_context=context)
        assert failure.value.kind == 'abnormal_exit'
        assert str(failure.value) == 'spawn fixture'
        assert failure.value.diagnostics['lifecycle']['spawn_failure_ns'] == 200
        assert failure.value.diagnostics['lifecycle']['termination_kind'] == 'spawn_failed'
        assert events == [('ns', 100), 'spawn', ('ns', 200)]
    else:
        player = Player('virtual', evaluation_context=context)
        assert events == [('ns', 100), 'spawn', ('ns', 200), 'reader', 'reader',
                          ('name', 'name', True)]
        assert player.lifecycle['popen_return_ns'] == 200
        assert player.startup_end_ns == 400 and player.startup_seconds == 300 / 1e9
        assert player.lifecycle['extension'] is extension
        assert player.lifecycle['measurement_thread_id'] == 777
    assert context == {'clock_domain_id': 'domain', 'extension': extension}
    assert supplied['stdin'] == supplied['stdout'] == supplied['stderr'] == -1
    assert supplied['text'] is True and supplied['encoding'] == 'utf-8'
    assert supplied['errors'] == 'replace' and supplied['bufsize'] == 1
    if platform == 'win32':
        assert supplied['creationflags'] == 512 and 'start_new_session' not in supplied
    else:
        assert supplied['start_new_session'] is True and 'creationflags' not in supplied


@pytest.mark.parametrize('timeout', [None, True, '1', 0, -1, float('nan'), float('inf')])
def test_protocol_typing_preserves_raw_deadline_rejection(timeout):
    """host区間の前提が正しくても、bool/非有限/欠損期限を正常化して通さない。"""
    record = {
        'command': 'ask B 2 3', 'command_index': 0, 'exchange_start_ns': 0,
        'send_start_ns': 1, 'response_read_end_ns': 2, 'validation_end_ns': 3,
        'external_response_ns': 3, 'wire_envelope_ns': 1, 'seconds': 3 / 1e9,
    }
    with pytest.raises(ValueError, match='host_deadline_invalid'):
        protocol.validate_command_record(record, timeout)


@pytest.mark.parametrize('observed', [150.5, True])
def test_protocol_typing_exit_view_does_not_normalize_saved_value(monkeypatch, observed):
    """castは転記値を整数化しない。不正な元時刻は保存し、既存の意味検証が拒否する。"""
    player = _protocol_typing_player()
    stamps = iter([100, 110, 160])
    player.lifecycle['exit_observed_ns'] = observed
    player._observe_exit = lambda kind: None
    player.process.wait = lambda timeout: setattr(player.process, 'returncode', 0)
    player.abort = lambda: None
    monkeypatch.setattr(protocol, 'time', SimpleNamespace(
        perf_counter_ns=lambda: next(stamps), perf_counter=lambda: 120 / 1e9,
    ))
    player.finish()
    record = player.commands[0]
    assert record['exit_observed_ns'] is observed
    assert record['external_response_ns'] == observed - 100
    with pytest.raises(ValueError, match='host_interval_invalid'):
        protocol.validate_command_record(record, 1.0)


def test_protocol_typing_legacy_clock_metadata_remains_unverified():
    """v1で未検証の周波数/patchを型付けで修復せず、親子比較はunknownにする。"""
    clock = protocol.clock_descriptor()
    clock.update(measurement_contract=protocol.LEGACY_MEASUREMENT_CONTRACT,
                 qpc_frequency_hz='opaque', python_patch_version=['unchecked'])
    assert protocol.clock_status(clock) == 'verified'
    result = protocol.temporal_order(1, 1000, clock, 'legacy-edge', cross_process=True)
    assert result[0]['status'] == 'unknown'
    assert result[0]['code'] == 'temporal_frequency_missing'


@pytest.mark.parametrize('mode', ['timeout', 'abnormal_exit', 'protocol_error', 'illegal_move', 'board_mismatch'])
def test_evaluation_records_faults_and_continues(monkeypatch, mode):
    """故障局を失敗として記録し、勝率の分母に混ぜず後続legと終了回収を確認する。"""
    monkeypatch.setenv('REVERSI_HARNESS_TEST_FAULT', mode)
    report = evaluate(FAULT, SCRIPT, games=2, seed=31, genmove_timeout=0.1)
    assert report['summary']['failed'] == 2
    assert report['summary']['completed'] == 0
    assert report['games'][0]['error']['kind'] == mode
    assert report['games'][0]['error']['player'] == 'A'
    assert report['summary']['A']['win_rate'] is None
    for game in report['games']:
        assert all(player['exit_code'] is not None for player in game['players'].values())


@pytest.mark.parametrize('mode,operation,kind', [
    ('timeout', 'genmove B', 'timeout'),
    ('abnormal_exit', 'genmove B', 'abnormal_exit'),
    ('protocol_error', 'genmove B', 'protocol_error'),
    ('extra_stdout', 'quit', 'protocol_error'),
    ('quit_timeout', 'quit', 'timeout'),
])
def test_bad_player_is_terminated(monkeypatch, mode, operation, kind):
    """通信違反の種類と、故障後のprocess/thread回収を確認する。"""
    monkeypatch.setenv('REVERSI_HARNESS_TEST_FAULT', mode)
    player = Player(FAULT, command_timeout=0.1, genmove_timeout=0.1)
    try:
        with pytest.raises(HarnessError) as failure:
            if operation == 'quit':
                player.finish()
            else:
                player.command(operation)
        assert failure.value.kind == kind
        assert player.process.poll() is not None
        assert all(not reader.is_alive() for reader in player._readers)
    finally:
        player.abort()


def test_startup_has_its_own_timeout(monkeypatch):
    """最初のname未応答を通常要求期限とは別の起動失敗として診断できる。"""
    monkeypatch.setenv('REVERSI_HARNESS_TEST_FAULT', 'startup_timeout')
    with pytest.raises(HarnessError) as failure:
        Player(FAULT, startup_timeout=0.1)
    assert failure.value.kind == 'timeout'
    assert failure.value.command == 'name'
    assert failure.value.diagnostics['exit_code'] is not None


def test_failed_games_are_not_used_to_claim_performance_improvement(smoke_reports):
    """成功局だけの値や改変集計を、未完走評価の改善根拠にしない。"""
    before, after = smoke_reports
    changed = copy.deepcopy(after)
    changed['summary']['failed'] = 1
    assert compare_reports(before, changed)['condition_mismatches'] == [
        'incomplete_evaluation', 'report_integrity_failed',
    ]


def test_genmove_uses_its_longer_deadline(monkeypatch):
    """通常期限を超える応答でも、genmove専用期限内なら成功させる。"""
    monkeypatch.setenv('REVERSI_HARNESS_TEST_FAULT', 'slow_genmove')
    with Player(FAULT, command_timeout=0.1, genmove_timeout=0.5) as player:
        assert player.command('genmove B') == ['2 3']
        assert player.commands[-1]['seconds'] > player.command_timeout
        assert player.commands[-1]['seconds'] < player.genmove_timeout


def test_total_response_time_is_checked_after_validation(monkeypatch):
    """stdout到着だけで期限判定を終えず、形式検査の遅延も応答時間へ含める。"""
    player = Player(SCRIPT, command_timeout=0.05)
    validate = player._validate

    def slow_validation(command, lines):
        """実際の形式検査後に遅延を注入し、全応答時間の期限判定を試す。"""
        validate(command, lines)
        time.sleep(0.08)

    monkeypatch.setattr(player, '_validate', slow_validation)
    try:
        with pytest.raises(HarnessError) as failure:
            player.command('result')
        assert failure.value.kind == 'timeout'
        assert player.process.poll() is not None
    finally:
        player.abort()


def test_opening_population_is_reproducible_validated_and_sampled_without_replacement(tmp_path):
    """同じ生成/抽出seedで内容が再現し、対称重複・非復元抽出・不正履歴拒否を確かめる。"""
    first = generate_population(12, 8, 417, 'development')
    second = generate_population(12, 8, 417, 'development')
    assert first == second
    assert len({entry['case_id'] for entry in first['entries']}) == 12
    assert len({entry['orbit_id'] for entry in first['entries']}) == 12
    path = tmp_path / 'openings.json'
    path.write_text(json.dumps(first), encoding='utf-8')
    assert load_population(path) == first
    selected_first, manifest_first = select_entries(first, 5, 2026)
    selected_second, manifest_second = select_entries(first, 5, 2026)
    assert [entry['case_id'] for entry in selected_first] == [entry['case_id'] for entry in selected_second]
    assert manifest_first == manifest_second
    assert len({entry['case_id'] for entry in selected_first}) == 5
    corrupted = copy.deepcopy(first)
    corrupted['entries'][0]['moves'][0]['position'] = [0, 0]
    with pytest.raises(ValueError):
        validate_population(corrupted)


def test_opening_typing_preserves_validation_aliases_and_extensions(tmp_path):
    """型付けで検証対象を再構築せず、追加キー・履歴・選択entryの共有を維持する。"""
    population = generate_population(3, 4, 417)
    population['extension'] = {'note': ['preserve']}
    payload = {key: value for key, value in population.items() if key != 'population_hash'}
    population['population_hash'] = hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')).hexdigest()
    before = copy.deepcopy(population)
    assert validate_population(population) is population
    original = population['entries'][0]
    checked = openings.validate_entry(original, 4)
    assert checked == original and checked is not original
    assert checked['moves'] is original['moves']
    assert checked['board'] is not original['board']
    selected, manifest = select_entries(population, 2, 2026)
    assert all(any(entry is source for source in population['entries']) for entry in selected)
    assert manifest['selected_case_ids'] == [entry['case_id'] for entry in selected]
    assert population == before
    path = tmp_path / 'extension-population.json'
    path.write_text(json.dumps(population), encoding='utf-8')
    assert load_population(path) == before
    assert load_population(PurePath(path)) == before


@pytest.mark.parametrize('kind,metadata', [
    ('absent', None), ('null', None), ('array', ['opaque']),
])
def test_opening_typing_keeps_generator_metadata_unverified(kind, metadata):
    """母集合検証は生成metadataを検査しないため、型付けで新しい拒否を追加しない。"""
    population = generate_population(1, 4, 417)
    if kind == 'absent':
        population.pop('generator')
    else:
        population['generator'] = metadata
    population['population_hash'] = openings.digest({
        key: value for key, value in population.items() if key != 'population_hash'
    })
    before = copy.deepcopy(population)
    assert validate_population(population) is population
    assert population == before


@pytest.mark.parametrize('position,message', [
    (None, 'invalid pass in opening history'),
    ((2, 3), 'opening position must contain two integers'),
    ([True, 3], 'opening position must contain two integers'),
    ([2], 'opening position must contain two integers'),
    ('2 3', 'opening position must contain two integers'),
    ([0, 0], 'illegal move in opening history'),
])
def test_opening_typing_preserves_untrusted_position_rejection(position, message):
    """初手は合法手がある。tuple・bool・長さ違いを補正せず、既存の例外を返す。"""
    entry = generate_population(1, 1, 417)['entries'][0]
    entry['moves'][0]['position'] = position
    before = copy.deepcopy(entry)
    with pytest.raises(ValueError, match=message):
        openings.validate_entry(entry, 1)
    assert entry == before


@pytest.mark.parametrize('changes,message', [
    ({'count': True}, 'count must be a positive integer'),
    ({'placements': True}, 'placements must be a positive integer'),
    ({'seed': True}, 'seed must be an integer'),
    ({'max_attempts': True}, 'requested count exceeds generation attempt limit'),
])
def test_opening_typing_preserves_strict_integer_generation_inputs(changes, message):
    arguments = {'count': 1, 'placements': 4, 'seed': 417}
    arguments.update(changes)
    with pytest.raises(ValueError, match=message):
        generate_population(**arguments)


@pytest.mark.parametrize('count,seed,message', [
    (True, 7, 'selection count must be between 1 and population size'),
    (1, True, 'selection seed must be an integer'),
    (0, 7, 'selection count must be between 1 and population size'),
    (3, 7, 'selection count must be between 1 and population size'),
])
def test_opening_typing_preserves_selection_rejections(count, seed, message):
    population = generate_population(2, 4, 417)
    before = copy.deepcopy(population)
    with pytest.raises(ValueError, match=message):
        select_entries(population, count, seed)
    assert population == before


def test_opening_typing_preserves_canonical_json_and_raw_byte_digest():
    assert openings.canonical_json({'z': '序盤', 'a': 1}) == '{"a":1,"z":"序盤"}'
    assert openings.digest(b'abc') == 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    with pytest.raises(TypeError):
        openings.canonical_json(object())


@pytest.mark.parametrize('value', [float('nan'), float('inf')])
def test_opening_typing_preserves_nonfinite_json_rejection(value):
    with pytest.raises(ValueError):
        openings.digest(value)


def test_opening_typing_preserves_exclusion_and_symmetric_attempt_limit(tmp_path):
    """初手4候補はD4で同一軌道なので、2件の対称重複排除生成は完了できない。"""
    development = generate_population(2, 4, 417)
    path = tmp_path / 'development.json'
    path.write_text(json.dumps(development), encoding='utf-8')
    held_out = generate_population(2, 4, 418, 'held_out', [PurePath(path)])
    assert not ({entry['orbit_id'] for entry in development['entries']}
                & {entry['orbit_id'] for entry in held_out['entries']})
    assert held_out['generator']['excluded_population_hashes'] == [development['population_hash']]
    with pytest.raises(ValueError, match='could not generate 2 unique openings within 2 attempts'):
        generate_population(2, 1, 417, max_attempts=2)


def test_opening_evaluation_replays_forced_moves_to_both_players(tmp_path):
    """序盤履歴とcase/pairを両legで共有し、本体の予算未対応を正式資格へ昇格させない。"""
    population = generate_population(3, 8, 94)
    population_path = tmp_path / 'population.json'
    population_path.write_text(json.dumps(population), encoding='utf-8')
    report = evaluate(
        SCRIPT, SCRIPT, games=2, seed=31, openings_file=population_path,
        selection_seed=71,
    )
    assert report['schema_version'] == 5
    assert report['population']['hash'] == population['population_hash']
    assert report['summary']['completed'] == 2
    assert report['summary']['failed'] == 0
    assert report['games'][0]['case_id'] == report['games'][1]['case_id']
    assert report['games'][0]['pair_id'] == report['games'][1]['pair_id']
    assert report['games'][0]['opening_moves'] == report['games'][1]['opening_moves']
    assert report['games'][0]['colors'] != report['games'][1]['colors']
    assert report['games'][0]['pair_status'] == 'unqualified'
    assert report['statistics']['status'] == 'descriptive_only'


def test_budget_config_rejects_invalid_values_and_exemption_hash_mismatch():
    """整数work上限と厳密なns変換を守り、同値入力を同じ正規化設定へまとめる。"""
    with pytest.raises(ValueError, match='limit_nodes'):
        normalize_config({'format_version': 1, 'budget': {'kind': 'nodes', 'limit_nodes': 0}})
    with pytest.raises(ValueError, match='target_sha256'):
        normalize_config({
            'format_version': 1,
            'budget': {'kind': 'none', 'exemption': {'reason': 'baseline', 'target_sha256': 'not-a-hash'}},
        })
    assert normalize_config({
        'format_version': 1, 'budget': {'kind': 'nodes', 'limit_nodes': 100}, 'search': {},
    }) == normalize_config({
        'format_version': 1, 'budget': {'kind': 'nodes', 'limit_nodes': 100.0}, 'search': {},
    })
    with pytest.raises(ValueError, match='integer'):
        normalize_config({
            'format_version': 1, 'budget': {'kind': 'depth', 'limit_depth': 2.5}, 'search': {},
        })
    with pytest.raises(ValueError, match='nanoseconds'):
        normalize_config({
            'format_version': 1, 'budget': {'kind': 'time', 'limit_ms': 0.0000001}, 'search': {},
        })


def test_confirmatory_evaluation_verifies_trace_and_estimates_paired_scores(tmp_path):
    """全時間/予算/母集合を検証した正式ペア区間と、seed改変・予定欠落の拒否を確認する。"""
    population = generate_population(8, 8, 810, 'held_out')
    population_path = tmp_path / 'held-out.json'
    population_path.write_text(json.dumps(population), encoding='utf-8')
    node_config = {
        'format_version': 1,
        'budget': {'kind': 'nodes', 'limit_nodes': 100},
        'search': {
            'algorithm': 'fixture',
            'deterministic': True,
            'counting_definition_id': 'fixture-node-v1',
        },
    }
    report = evaluate(
        BUDGET_FIXTURE, BUDGET_FIXTURE, games=6, seed=2026,
        openings_file=population_path, selection_seed=99,
        config_a=node_config, config_b=node_config,
        budget_policy='equal-work', evaluation_profile='confirmatory',
    )
    assert report['summary']['completed'] == 6
    assert report['summary']['failed'] == 0
    assert report['status']['budget'] == 'verified'
    assert report['statistics']['status'] == 'available'
    assert report['statistics']['interval'] is not None
    assert report['statistics']['assessment']['formal_reason'] is None
    inspection = contracts.inspect_evaluation(report)
    arithmetic = estimate_pair_scores(inspection['derived']['games'], 8)
    assert arithmetic['status'] == 'computed'
    assert arithmetic['pair_count'] == 3
    assert arithmetic['interval']['lower'] <= arithmetic['point_rate'] <= arithmetic['interval']['upper']
    assert all(game['pair_status'] == 'qualified' for game in report['games'])
    assert all(player['budget_trace']['status'] == 'verified'
               for game in report['games'] for player in game['players'].values())
    comparison = compare_reports(report, report)
    assert comparison['comparable']
    assert comparison['pair_statistics_available']
    assert comparison['paired_difference']['mean_delta'] == 0
    assert analysis.paired(report, report)['status'] == 'available'
    tampered = copy.deepcopy(report)
    for game in tampered['games']:
        game['seeds']['A'] += 1
    assert not compare_reports(report, tampered)['pair_statistics_available']
    assert 'report_integrity_failed' in compare_reports(report, tampered)['condition_mismatches']
    selection = report['conditions']['selection_manifest']
    incomplete_statistics = estimate_pair_scores(
        report['games'][:-2], 8, eligible=True,
        expected_pair_count=selection['sample_size'],
        expected_case_ids=selection['selected_case_ids'],
    )
    assert incomplete_statistics['status'] == 'unavailable'
    assert incomplete_statistics['reason'] == 'expected_cases_mismatch'
    truncated_report = {**report, 'games': report['games'][:-2]}
    assert not compare_reports(report, truncated_report)['comparable']


def test_deployment_profile_verifies_equal_time_budget_without_formal_interval(tmp_path):
    """時間予算とlifecycleは照合するが、deploymentへ固定得点CIを付けない。"""
    population = generate_population(4, 8, 811)
    population_path = tmp_path / 'deployment-openings.json'
    population_path.write_text(json.dumps(population), encoding='utf-8')
    time_config = {
        'format_version': 1,
        'budget': {'kind': 'time', 'limit_ms': 5000},
        'search': {'algorithm': 'fixture', 'deterministic': True},
    }
    report = evaluate(
        BUDGET_FIXTURE, BUDGET_FIXTURE, games=2, seed=2027,
        openings_file=population_path, selection_seed=100,
        config_a=time_config, config_b=time_config,
        budget_policy='equal-time', evaluation_profile='deployment',
    )
    assert report['summary']['completed'] == 2
    assert report['status']['protocol'] == 'verified'
    assert report['status']['budget'] == 'verified'
    assert report['statistics']['status'] == 'descriptive_only'
    assert report['statistics']['reason'] == 'formal_ci_not_requested'
    assert report['status']['performance_criterion'] == 'not_requested'
    comparison = compare_reports(report, report)
    assert comparison['comparable']
    assert comparison['condition_mismatches'] == []
    assert not comparison['pair_statistics_available']


def test_trace_validation_rejects_bad_types_depth_and_unapproved_exemption(tmp_path):
    """構文型、完了深さと開始深さの大小、除外許可を独立した違反として扱う。"""
    config = normalize_config({
        'format_version': 1, 'budget': {'kind': 'nodes', 'limit_nodes': 100},
        'search': {},
    })
    trace_path = tmp_path / 'bad.jsonl'
    trace_path.write_text('null\n', encoding='utf-8')
    assert validate_trace(trace_path, config, [], 'a' * 64)['status'] == 'failed'

    config_hash = hashlib.sha256(canonical_json(config).encode()).hexdigest()
    trace_path.write_text('\n'.join([
        canonical_json({
            'type': 'config', 'format_version': 2, 'run_id': 'run', 'pair_id': 'pair',
            'leg': 0, 'player': 'A', 'requested_config_sha256': config_hash,
            'target_sha256': 'a' * 64, 'effective_config': config,
            'clock': 'perf_counter_ns', 'measurement_scope': 'search_only', 'model_id': None,
            'model_load_elapsed_ns': 0, 'fallback': False,
        }),
        canonical_json({
            'type': 'genmove', 'sequence': 0, 'phase': 'normal_move', 'command': 'genmove B',
            'color': 'B', 'board_sha256': board_sha256(INITIAL_ROWS), 'response': '2 3',
            'search_elapsed_ns': 1, 'nodes': 1, 'max_depth_started': 2,
            'completed_depth': 3, 'stop_reason': 'limit', 'model_id': None,
            'model_load_elapsed_ns': 0, 'fallback': False,
        }),
    ]) + '\n', encoding='utf-8')
    commands = [
        {'command': 'showboard', 'response': list(INITIAL_ROWS)},
        {'command': 'genmove B', 'response': ['2 3'], 'phase': 'normal_move'},
    ]
    result = validate_trace(trace_path, config, commands, 'a' * 64, {
        'run_id': 'run', 'pair_id': 'pair', 'leg': 0, 'player': 'A',
    })
    assert result['status'] == 'failed'
    assert 'depth_progression_invalid' in result['reason']

    exemption_config = normalize_config({
        'format_version': 1,
        'budget': {'kind': 'none', 'exemption': {'reason': 'baseline', 'target_sha256': 'a' * 64}},
        'search': {},
    })
    result = validate_trace(trace_path, exemption_config, [], 'a' * 64)
    assert result['status'] == 'unknown'
    assert result['reason'] == 'exemption_not_authorized'


def test_trace_artifacts_are_unique_and_survive_evaluation(tmp_path):
    """一時設定領域の終了後にも両leg/両roleの原traceが個別に残る。"""
    config = {
        'format_version': 1,
        'budget': {'kind': 'nodes', 'limit_nodes': 100},
        'search': {'counting_definition_id': 'fixture-node-v1'},
    }
    report = evaluate(BUDGET_FIXTURE, BUDGET_FIXTURE, games=2, seed=45,
                      config_a=config, config_b=config, budget_policy='equal-work',
                      evidence_dir=tmp_path / 'runs')
    artifacts = [
        player['budget_trace']['artifact']
        for game in report['games'] for player in game['players'].values()
    ]
    assert len(artifacts) == 4
    assert len({artifact['path'] for artifact in artifacts}) == 4
    assert all(artifact['exists'] for artifact in artifacts)
    assert all((Path(report['evidence']['root']) / artifact['path']).is_file()
               for artifact in artifacts)


def test_analysis_changes_do_not_change_experiment_identity():
    """confidenceだけの変更は取得条件IDを保ち、解析版/IDだけを変える。"""
    first = evaluate(SCRIPT, SCRIPT, games=2, seed=46, confidence=0.95)
    second = evaluate(SCRIPT, SCRIPT, games=2, seed=46, confidence=0.90)
    assert first['experiment_id'] == second['experiment_id']
    assert first['evaluation_fingerprint'] == second['evaluation_fingerprint']
    assert first['analysis_fingerprint'] != second['analysis_fingerprint']
    assert first['analysis_id'] != second['analysis_id']


def _legacy_optional_budget_report(schema_version):
    """旧形式の参考集計を用意する。新しいraw証拠や正式資格があるとは仮定しない。"""
    report = {
        'summary': {
            'failed': 0,
            'A': {'win_rate': 0.5, 'point_rate': 0.5,
                  'mean_genmove_seconds': 0.1, 'max_genmove_seconds': 0.2},
        },
        'conditions': {}, 'runtime': {},
        'targets': {'B': {'sha256': 'target', 'assets': []}},
        'harness': [],
    }
    if schema_version is not None:
        report['schema_version'] = schema_version
    return report


def _legacy_optional_budget_config():
    """任意メタデータに使う正常なformat2設定を返す。予算の実測証拠ではない。"""
    return {'format_version': 2, 'budget': {'kind': 'nodes', 'limit_nodes': 100},
            'search': {'deterministic': True, 'counting_definition_id': 'fixture-node-v1'}}


def _legacy_optional_budget_invalid_report(schema_version, mutation):
    """型・設定の既知矛盾を持つ旧JSONを生成する。設定欠落の互換入力とは区別する。"""
    report = _legacy_optional_budget_report(schema_version)
    configs = {'A': _legacy_optional_budget_config(), 'B': _legacy_optional_budget_config()}
    if mutation == 'outer_list':
        configs = []
    elif mutation == 'outer_null':
        configs = None
    elif mutation == 'outer_bool':
        configs = False
    elif mutation == 'extra_role':
        configs['C'] = _legacy_optional_budget_config()
    elif mutation == 'role_list':
        configs['A'] = []
    elif mutation == 'role_null':
        configs['B'] = None
    elif mutation == 'search_list':
        configs['A']['search'] = []
    elif mutation == 'search_null':
        configs['B']['search'] = None
    elif mutation == 'budget_list':
        configs['A']['budget'] = []
    elif mutation == 'config_version':
        configs['A']['format_version'] = 99
    elif mutation == 'budget_limit':
        configs['A']['budget']['limit_nodes'] = -1
    else:
        raise ValueError('unknown legacy budget mutation')
    report['conditions']['budget_configs'] = configs
    return report


@pytest.mark.parametrize('schema_version', [1, 2])
@pytest.mark.parametrize('mutation', ['outer_list', 'outer_null', 'outer_bool', 'extra_role',
                                     'role_list', 'role_null', 'search_list', 'search_null',
                                     'budget_list', 'config_version', 'budget_limit'])
def test_legacy_optional_budget_invalid_sources_fail_all_public_entries(schema_version, mutation):
    """F02: 旧不正型を参考成功やAttributeErrorにせず、比較のどちら側でも構造化拒否する。"""
    report = _legacy_optional_budget_invalid_report(schema_version, mutation)
    original = copy.deepcopy(report)
    valid = _legacy_optional_budget_report(schema_version)
    for before, after in ((report, valid), (valid, report), (report, report)):
        difference = analysis.paired(before, after)
        comparison = compare_reports(before, after)
        builder = analysis.comparison_report(before, after)
        pair_assessment = analysis.assess_comparison(before, after)
        assert difference['status'] == 'unavailable'
        assert difference['interval'] is None
        assert pair_assessment['formal_pair_eligibility'] == 'ineligible'
        assert 'input_invalid' in pair_assessment['condition_mismatches']
        assert not comparison['comparable']
        assert not comparison['pair_statistics_available']
        assert comparison['paired_difference'] is None
        assert builder['assessment'] == comparison['assessment']
        assert [item['input_status'] for item in pair_assessment['evaluations']] == [
            'invalid' if source is report else 'valid' for source in (before, after)
        ]
    issue = ('budget_configs_invalid' if mutation in
             ('outer_list', 'outer_null', 'outer_bool', 'extra_role') else 'budget_config_invalid')
    decoded = contracts.decode_input(report)
    assert decoded['input_status'] == 'invalid'
    assert decoded['issues'] == [issue]
    assert decoded['conditions'] == {}
    inspection = contracts.inspect_evaluation(report)
    assert inspection['input_status'] == 'invalid'
    assert inspection['status'] == 'failed'
    assert 'snapshot' not in inspection
    assessment = analysis.assess_evaluation(report)
    assert not assessment['reference_available']
    assert not assessment['request_success']
    assert assessment['formal_eligibility'] == 'ineligible'
    assert analysis.statistical_eligibility(report) == (False, 'input_invalid')
    estimate = analysis.estimate(report)
    reanalysis = analysis.reanalysis_report(report)['statistics']
    assert estimate['status'] == reanalysis['status'] == 'unavailable'
    assert estimate['interval'] is reanalysis['interval'] is None
    assert estimate['assessment'] == reanalysis['assessment'] == assessment
    assert report == original


@pytest.mark.parametrize('schema_version', [None, 1, 2])
@pytest.mark.parametrize('metadata', ['absent', 'empty', 'one_role', 'both_roles'])
def test_legacy_optional_budget_valid_sources_preserve_reference(schema_version, metadata):
    """設定欠落・空・片roleの旧互換性は維持し、正常メタデータでも正式証拠へ昇格しない。"""
    report = _legacy_optional_budget_report(schema_version)
    if metadata != 'absent':
        report['conditions']['budget_configs'] = {}
    if metadata in ('one_role', 'both_roles'):
        report['conditions']['budget_configs']['A'] = _legacy_optional_budget_config()
    if metadata == 'one_role':
        del report['conditions']['budget_configs']['A']['search']
    elif metadata == 'both_roles':
        report['conditions']['budget_configs']['B'] = _legacy_optional_budget_config()
    original = copy.deepcopy(report)
    assert contracts.decode_input(report)['input_status'] == 'valid'
    inspection = contracts.inspect_evaluation(report)
    assert inspection['status'] == 'unknown'
    assert inspection['snapshot'] == original
    estimate = analysis.estimate(report)
    assert estimate['status'] == 'descriptive_only'
    assert estimate['assessment']['reference_available']
    assert estimate['assessment']['request_success']
    assert estimate['interval'] is None
    assert not analysis.statistical_eligibility(report)[0]
    reanalysis = analysis.reanalysis_report(report)
    assert reanalysis['schema_version'] == 5
    assert reanalysis['statistics']['assessment'] == estimate['assessment']
    difference = analysis.paired(report, report)
    comparison = compare_reports(report, report)
    assert difference['status'] == 'unavailable'
    assert difference['interval'] is None
    assert comparison['comparable']
    assert not comparison['pair_statistics_available']
    assert not comparison['budget_verified']
    assert comparison['decision'] == 'undetermined'
    assert report == original


@pytest.mark.parametrize('schema_version', [1, 2])
@pytest.mark.parametrize('mutation', ['outer_list', 'role_null', 'search_list'])
@pytest.mark.parametrize('entry', ['analysis', 'comparison'])
def test_legacy_optional_budget_cli_invalid_is_exit_two(tmp_path, schema_version, mutation, entry):
    """両実CLIは不正旧JSONを開始前に終了2で拒否し、出力も原資料の修復も行わない。"""
    report = _legacy_optional_budget_invalid_report(schema_version, mutation)
    source = tmp_path / 'source.json'
    original = json.dumps(report)
    source.write_text(original, encoding='utf-8')
    output = tmp_path / 'result.json'
    command = [sys.executable, '-B', '-m', 'harness.' + ('analysis' if entry == 'analysis' else 'evaluate')]
    command.extend(['--report', str(source)] if entry == 'analysis' else ['--compare', str(source), str(source)])
    command.extend(['--output', str(output)])
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'budget_config' in result.stderr
    assert not output.exists()
    assert source.read_text(encoding='utf-8') == original


@pytest.mark.parametrize('schema_version', [1, 2])
@pytest.mark.parametrize('entry', ['analysis', 'comparison'])
def test_legacy_optional_budget_cli_valid_reference_is_exit_zero(tmp_path, schema_version, entry):
    """正常な片roleの旧メタデータは実CLIで再利用でき、出力schema5だけで正式昇格しない。"""
    report = _legacy_optional_budget_report(schema_version)
    report['conditions']['budget_configs'] = {'A': _legacy_optional_budget_config()}
    source = tmp_path / 'source.json'
    original = json.dumps(report)
    source.write_text(original, encoding='utf-8')
    output = tmp_path / 'result.json'
    command = [sys.executable, '-B', '-m', 'harness.' + ('analysis' if entry == 'analysis' else 'evaluate')]
    command.extend(['--report', str(source)] if entry == 'analysis' else ['--compare', str(source), str(source)])
    command.extend(['--output', str(output)])
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    restored = json.loads(output.read_text(encoding='utf-8'))
    assert restored['schema_version'] == 5
    if entry == 'analysis':
        assert restored['statistics']['status'] == 'descriptive_only'
        assert restored['statistics']['interval'] is None
        assert restored['statistics']['assessment']['reference_available']
    else:
        assert restored['comparable']
        assert not restored['pair_statistics_available']
        assert restored['paired_difference'] is None
    assert source.read_text(encoding='utf-8') == original


def test_legacy_comparison_cannot_claim_budget_verification():
    """旧形式の参考差分を正式な予算証拠やペアCIとして扱わない。"""
    legacy = {
        'schema_version': 1,
        'summary': {
            'failed': 0,
            'A': {'win_rate': 0.5, 'point_rate': 0.5,
                  'mean_genmove_seconds': 0.1, 'max_genmove_seconds': 0.2},
        },
        'conditions': {}, 'runtime': {},
        'targets': {'B': {'sha256': 'target', 'assets': []}},
        'harness': [],
    }
    comparison = compare_reports(legacy, legacy, require_budget_verification=True)
    assert not comparison['comparable']
    assert not comparison['budget_comparable']
    assert comparison['paired_difference'] is None
    assert 'budget_verification_unavailable' in comparison['condition_mismatches']


def test_cli_saves_v5_and_rejects_unverified_budget_comparison(tmp_path):
    """取得完了checkpointがあっても、本体の予算未対応なら必須予算比較は終了1にする。"""
    evaluation_path = tmp_path / 'smoke.json'
    comparison_path = tmp_path / 'comparison.json'
    command = [
        sys.executable, '-B', '-m', 'harness.evaluate',
        '--a', str(SCRIPT), '--b', str(SCRIPT), '--games', '2', '--seed', '47',
        '--output', str(evaluation_path),
    ]
    evaluated = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert evaluated.returncode == 0, evaluated.stderr
    report = json.loads(evaluation_path.read_text(encoding='utf-8'))
    assert report['schema_version'] == 5
    assert report['report_kind'] == 'evaluation'
    state_path = Path(report['evidence']['root']) / 'run-state.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    assert state['report_state'] == 'complete'
    assert state['progress']['not_started_legs'] == 0

    compared = subprocess.run([
        sys.executable, '-B', '-m', 'harness.evaluate', '--compare',
        str(evaluation_path), str(evaluation_path), '--require-budget-verification',
        '--output', str(comparison_path),
    ], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert compared.returncode == 1
    comparison = json.loads(comparison_path.read_text(encoding='utf-8'))
    assert comparison['report_kind'] == 'comparison'
    assert not comparison['budget_comparable']


def test_omitted_games_uses_all_fixed_openings_and_confirmatory_requires_explicit(tmp_path):
    """記述的評価の全序盤既定値と、正式評価の事前明示局数を区別する。"""
    population = generate_population(4, 8, 812)
    population_path = tmp_path / 'population.json'
    population_path.write_text(json.dumps(population), encoding='utf-8')
    report = evaluate(BUDGET_FIXTURE, BUDGET_FIXTURE, openings_file=population_path,
                      evidence_dir=tmp_path / 'runs')
    assert report['conditions']['planned_games'] == 8
    assert report['request']['games_explicit'] is False
    with pytest.raises(ValueError, match='explicit games'):
        evaluate(BUDGET_FIXTURE, BUDGET_FIXTURE, openings_file=population_path,
                 selection_seed=2, config_a={'format_version': 1,
                 'budget': {'kind': 'nodes', 'limit_nodes': 2},
                 'search': {'deterministic': True}},
                 config_b={'format_version': 1,
                 'budget': {'kind': 'nodes', 'limit_nodes': 2},
                 'search': {'deterministic': True}},
                 budget_policy='equal-work', evaluation_profile='confirmatory')


def test_interrupted_evaluation_keeps_partial_checkpoint(tmp_path, monkeypatch):
    """1leg返却後の模擬中断で予定・開始・中断の数と既得記録が残ることを確認する。"""
    calls = 0

    def fail_after_first_leg(*args, **kwargs):
        """初回に失敗legを返し、次の呼出で例外を発生させる。実Popen件数の試験ではない。"""
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                'index': 0, 'game_status': 'incomplete', 'protocol_status': 'failed',
                'budget_status': 'unknown', 'winner': None, 'result': None,
                'pair_id': 'pair', 'case_id': 'case', 'leg': 0,
                'seeds': {'A': 1, 'B': 2}, 'score_a': None,
                'error': {'kind': 'fixture_failure'}, 'players': {},
                'colors': {'B': 'A', 'W': 'B'},
            }
        raise RuntimeError('simulated_interruption')

    monkeypatch.setattr(evaluate_module, 'play_game', fail_after_first_leg)
    with pytest.raises(RuntimeError, match='simulated_interruption'):
        evaluate(SCRIPT, SCRIPT, games=2, seed=48, evidence_dir=tmp_path / 'runs')
    checkpoints = list((tmp_path / 'runs').glob('*/run-state.json'))
    assert len(checkpoints) == 1
    state = json.loads(checkpoints[0].read_text(encoding='utf-8'))
    assert state['report_state'] == 'partial'
    assert state['progress']['planned_games'] == 2
    assert state['progress']['started_legs'] == 2
    assert state['progress']['not_started_legs'] == 0
    assert state['progress']['interrupted_legs'][0]['leg'] == 1


def test_finite_population_interval_handles_constant_scores_and_census():
    """同点標本は幅0にせず、全数[0, 0.5, 1]は手計算平均0.5の一点区間にする。"""
    interval = confidence_interval([1.0] * 20, 1000)
    assert interval['lower'] < interval['upper'] == 1.0
    # 全数平均は(0+0.5+1)/3=0.5。同点標本N=1000には未観測ケースが残る。
    census = confidence_interval([0.0, 0.5, 1.0], 3)
    assert census['lower'] == census['mean'] == census['upper'] == 0.5


def _arithmetic_games(scores=(0.0, 1.0)):
    """母集合/trace証明ではない、手計算用の先後交換1ペアを作る。"""
    return [
        {
            'pair_id': 'pair', 'case_id': 'case', 'leg': leg,
            'game_status': 'complete', 'protocol_status': 'verified', 'budget_status': 'verified',
            'colors': {'B': 'A', 'W': 'B'} if leg == 0 else {'B': 'B', 'W': 'A'},
            'seeds': {'A': 1, 'B': 2}, 'score_a': score,
        }
        for leg, score in enumerate(scores)
    ]


@pytest.mark.parametrize('scores', [(0.0, 1.0), (0, 1), (False, True)])
def test_statistics_typing_preserves_pair_values_and_arithmetic_scope(scores):
    """既存の等値比較はJSON boolも0/1として扱う。型付けで変換/拒否しない。"""
    from harness import statistics
    games = _arithmetic_games(scores)
    before = copy.deepcopy(games)
    pairs, reasons = statistics.pair_scores(games)
    assert reasons == {}
    assert pairs == {'pair': {'case_id': 'case', 'score': 0.5, 'game_scores': list(scores)}}
    assert all(type(actual) is type(expected) for actual, expected in zip(pairs['pair']['game_scores'], scores))
    result = estimate_pair_scores(games, 1)
    assert result['status'] == 'computed'
    assert result['result_scope'] == 'arithmetic_only'
    assert result['formal_eligibility'] == 'not_assessed'
    assert result['point_rate'] == 0.5
    assert result['interval'] == {
        'mean': 0.5, 'lower': 0.5, 'upper': 0.5, 'half_width': 0.0,
        'method': 'finite_population_census', 'confidence': 0.95,
        'calculation_status': 'computed', 'result_scope': 'arithmetic_only',
        'formal_eligibility': 'not_assessed',
    }
    assert games == before


@pytest.mark.parametrize('key,value,reason', [
    ('leg', 1, 'invalid_pair_legs'),
    ('game_status', 'incomplete', 'incomplete_or_invalid_game'),
    ('protocol_status', 'failed', 'protocol_not_verified'),
    ('budget_status', 'unknown', 'budget_not_verified'),
    ('colors', {}, 'roles_not_swapped'),
    ('case_id', 'other', 'case_id_mismatch'),
    ('seeds', {'A': 3, 'B': 2}, 'player_seed_mismatch'),
    ('score_a', None, 'invalid_game_score'),
    ('score_a', '1', 'invalid_game_score'),
    ('score_a', float('nan'), 'invalid_game_score'),
    ('score_a', float('inf'), 'invalid_game_score'),
    ('score_a', 2.0, 'invalid_game_score'),
])
def test_statistics_typing_preserves_invalid_pair_reasons(key, value, reason):
    """状態/得点の未検証入力は、元と同じ検査順序で理由付き除外する。"""
    from harness import statistics
    games = _arithmetic_games()
    games[0][key] = value
    assert statistics.pair_scores(games) == ({}, {'pair': reason})


def test_statistics_typing_preserves_missing_values_and_empty_pairs():
    """欠損得点を0へ補わず、ペア識別子欠損と空入力の既存挙動も維持する。"""
    from harness import statistics
    games = _arithmetic_games()
    del games[0]['score_a']
    assert statistics.pair_scores(games) == ({}, {'pair': 'invalid_game_score'})
    for game in games:
        del game['pair_id']
    assert statistics.pair_scores(games) == ({}, {})
    assert estimate_pair_scores([], 1)['reason'] == 'at least one observation is required'
    assert statistics.paired_difference([], [], 1) == {
        'status': 'unavailable', 'reason': 'no_complete_pairs', 'interval': None,
    }


@pytest.mark.parametrize('values,population,confidence,value_range,message', [
    ([], 1, 0.95, (0.0, 1.0), 'at least one observation'),
    ([0.5], 0, 0.95, (0.0, 1.0), 'population size'),
    ([0.5], True, 0.95, (0.0, 1.0), 'population size'),
    ([0.5], 1.0, 0.95, (0.0, 1.0), 'population size'),
    ([0.5], 2, 0.0, (0.0, 1.0), 'confidence'),
    ([0.5], 2, 1.0, (0.0, 1.0), 'confidence'),
    ([-0.1], 2, 0.95, (0.0, 1.0), 'observations'),
    ([float('nan')], 2, 0.95, (0.0, 1.0), 'observations'),
    ([float('inf')], 2, 0.95, (0.0, 1.0), 'observations'),
    ([0.5], 2, 0.95, (1.0, 1.0), 'observations'),
])
def test_statistics_typing_preserves_interval_boundary_errors(
        values, population, confidence, value_range, message):
    """型の宣言にかかわらず、既存の厳密int検査と算術のValueErrorを維持する。"""
    with pytest.raises(ValueError, match=message):
        confidence_interval(values, population, confidence, value_range)


@pytest.mark.parametrize('mutation,error', [
    ('missing_case', KeyError), ('null_colors', AttributeError), ('unhashable_pair', TypeError),
])
def test_statistics_typing_does_not_repair_malformed_input(mutation, error):
    """元実装が例外にする構造不正を、新しい分岐で黙って正常化しない。"""
    from harness import statistics
    games = _arithmetic_games()
    if mutation == 'missing_case':
        for game in games:
            del game['case_id']
    elif mutation == 'null_colors':
        games[0]['colors'] = None
    else:
        games[0]['pair_id'] = ['not-hashable']
    with pytest.raises(error):
        statistics.pair_scores(games)


@pytest.mark.parametrize('value,expected', [(None, 0.8), (0.25, 0.25), (0.95, 0.95)])
def test_analysis_typing_confidence_uses_original_value(value, expected):
    """保存値の既定利用と明示指定の優先を保ち、入力を変更しない。"""
    inspection = {'snapshot': {'analysis_spec': {'confidence': 0.8}}}
    original = copy.deepcopy(inspection)
    assert analysis._confidence(inspection, value) == expected
    assert inspection == original


@pytest.mark.parametrize('value', [True, False, 0, 1, -1, 2, float('nan'), float('inf'), -float('inf'), '0.9', [], {}])
def test_analysis_typing_confidence_rejects_same_values(value):
    with pytest.raises(ValueError, match='^confidence_invalid$'):
        analysis._confidence({}, value)


@pytest.mark.parametrize('value', [None, [], 0, 'bad'])
def test_analysis_typing_metadata_get_preserves_native_exception(value):
    """不正なprivate入力を補完せず、元のget属性例外のname/objも保つ。"""
    with pytest.raises(AttributeError) as captured:
        analysis._confidence({'snapshot': {'analysis_spec': value}}, None)
    assert captured.value.name == 'get'
    assert captured.value.obj is value


@pytest.mark.parametrize('required', [False, True, [], ['requested']])
def test_analysis_typing_budget_truthiness_is_not_early_normalized(required):
    inspection = {
        'decoded': {'conditions': {'evaluation_profile': 'smoke', 'require_budget_verification': required}},
        'snapshot': {}, 'input_status': 'valid', 'status': 'unknown', 'integrity_errors': [],
        'gameplay': {'status': 'verified'}, 'coverage': {'status': 'verified'}, 'reasons': [],
    }
    result = analysis._assess_inspection(inspection)
    assert result['reference_available']
    assert result['formal_eligibility'] == 'not_requested'
    assert result['requirements']['budget_required'] is bool(required)
    assert result['request_success'] is (not bool(required))


@pytest.mark.parametrize('kind', ['single', 'paired'])
def test_analysis_typing_updates_original_arithmetic_result(monkeypatch, kind):
    """型付けのためのdict展開/copyが、算術辞書の同一性と未知metadataを壊さない。"""
    marker = object()
    games = [{'unverified': marker}]
    inspection = {
        'decoded': {'conditions': {'planned_games': 2}}, 'status': 'verified', 'reasons': [],
        'derived': {'games': games, 'coverage_kind': 'census_verified'},
        'population': {'verified_N': 1}, 'selection': {'planned_n': 1, 'selected_case_ids': ['c']},
    }
    supplied = {'status': 'computed', 'reason': None, 'interval': {'lower': 0.5}, 'opaque': marker}
    def compute(*arguments, **keywords):
        """算術への原game参照を観測し、同じ辞書を返す。"""
        assert arguments[0] is games
        return supplied
    if kind == 'single':
        monkeypatch.setattr(analysis, '_assess_inspection', lambda *arguments: {
            'requirements': {'formal_requested': True}, 'formal_eligibility': 'eligible',
            'formal_reason': None, 'input_status': 'valid', 'confidence': 0.9,
        })
        monkeypatch.setattr(analysis.statistics, 'estimate_pair_scores', compute)
        result = analysis._estimate_inspection(inspection, 0.9)
    else:
        monkeypatch.setattr(analysis, '_assess_pair_inspections', lambda *arguments: {
            'formal_pair_eligibility': 'eligible', 'formal_reason': None,
        })
        monkeypatch.setattr(analysis.statistics, 'paired_difference', compute)
        result = analysis._paired_inspections([inspection, inspection], 0.9)
    assert result is supplied and result['opaque'] is marker
    assert result['status'] == 'available' and result['formal_eligibility'] == 'eligible'


def test_analysis_typing_arithmetic_preserves_unvalidated_ids():
    """低水準算術のIDをstrへ正規化せず、正式なID証明とは別に保持する。"""
    games = _arithmetic_games()
    for game in games:
        game['pair_id'], game['case_id'] = 7, 9
    result = estimate_pair_scores(games, 1)
    assert result['pairs'][7]['case_id'] == 9
    assert result['formal_eligibility'] == 'not_assessed'


@pytest.mark.parametrize('field,operation', [('colors', 'get'), ('seeds', 'items')])
def test_analysis_typing_arithmetic_preserves_attribute_errors(field, operation):
    games = _arithmetic_games()
    games[0][field] = None
    with pytest.raises(AttributeError) as captured:
        estimate_pair_scores(games, 1)
    assert captured.value.name == operation and captured.value.obj is None


@pytest.mark.parametrize('field,operation', [('conditions', 'get'), ('budget_configs', 'get'), ('budget_config_hashes', 'pop'), ('targets', 'get')])
def test_analysis_typing_comparison_does_not_repair_null_metadata(field, operation):
    report = {'conditions': {}}
    if field.startswith('budget_'):
        report['conditions'][field] = None
    else:
        report[field] = None
    with pytest.raises(AttributeError) as captured:
        contracts.comparison_mismatches(report, {})
    assert captured.value.name == operation and captured.value.obj is None


@pytest.mark.parametrize('score', [0.5, 'unchecked', None])
def test_analysis_typing_derived_mapping_does_not_certify_fields(integrity_report, score):
    report = copy.deepcopy(integrity_report)
    report['games'][0]['score_a'] = score
    result = contracts.inspect_evaluation(report)
    assert all(isinstance(game, dict) for game in result['derived']['games'])
    assert result['derived']['games'][0]['score_a'] == score
    assert report['games'][0]['score_a'] == score


def test_statistics_typing_preserves_difference_direction_and_coverage():
    """先後平均after=.5、before=0なので、全数1ペアの対応差は+.5となる。"""
    from harness import statistics
    before = _arithmetic_games((0.0, 0.0))
    after = _arithmetic_games((0.0, 1.0))
    result = statistics.paired_difference(before, after, 1)
    assert result['mean_delta'] == 0.5
    assert result['interval']['lower'] == result['interval']['upper'] == 0.5
    assert result['result_scope'] == 'arithmetic_only'
    assert result['formal_eligibility'] == 'not_assessed'
    assert statistics.paired_difference(before, after, 1, expected_case_ids=['other'])['reason'] == 'expected_cases_mismatch'
    assert estimate_pair_scores(after, 1, eligible=False)['reason'] == 'evaluation_not_eligible'
    assert estimate_pair_scores(after, 1, expected_pair_count=2)['reason'] == 'incomplete_pairs'
