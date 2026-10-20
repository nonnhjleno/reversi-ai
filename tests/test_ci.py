"""CIの必須Fullを省略・偽装しない構造とイベント境界の回帰。"""

import copy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EVENTS = {
    'push': {'branches': ['main']},
    'pull_request': {'types': ['opened', 'synchronize', 'reopened', 'ready_for_review', 'edited']},
    'merge_group': {'types': ['checks_requested']},
    'workflow_dispatch': '',
}


def _load_workflow(text):
    """onをboolにせず構造を読み、重複キーやaliasで契約が曖昧になるYAMLを拒否する。"""
    assert not any(
        isinstance(token, (yaml.AliasToken, yaml.AnchorToken)) for token in yaml.scan(text)
    )

    def unique(node):
        """マッピングの全階層で同じキーを黙って上書きさせない。"""
        if isinstance(node, yaml.MappingNode):
            keys = [key.value for key, _ in node.value]
            assert len(keys) == len(set(keys))
            for _, value in node.value:
                unique(value)
        elif isinstance(node, yaml.SequenceNode):
            for value in node.value:
                unique(value)

    unique(yaml.compose(text, Loader=yaml.BaseLoader))
    return yaml.load(text, Loader=yaml.BaseLoader)


def _assert_ci_contract(document):
    """実YAMLのjob/step全構造を照合する。GitHub上の実行や保護適用の証明とは別。"""
    assert set(document) == {'name', 'on', 'permissions', 'jobs'}
    assert document['name'] == 'Quality'
    assert document['on'] == EVENTS
    assert document['permissions'] == {'contents': 'read'}
    assert set(document['jobs']) == {'quality'}
    job = document['jobs']['quality']
    assert set(job) == {'runs-on', 'timeout-minutes', 'steps'}
    assert job['runs-on'] == 'windows-latest'
    assert job['timeout-minutes'] == '45'
    assert job['steps'] == [
        {'uses': 'actions/checkout@v7', 'with': {'persist-credentials': 'false'}},
        {
            'uses': 'actions/setup-python@v7',
            'with': {'python-version': '3.13.5', 'architecture': 'x64'},
        },
        {
            'name': 'Install development dependencies',
            'run': 'python -m pip install -r requirements-dev.txt',
        },
        {'name': 'Run required quality gate', 'run': 'python -B tools/quality.py'},
        {
            'name': 'Upload pytest diagnostics',
            'if': 'always()',
            'uses': 'actions/upload-artifact@v4',
            'with': {
                'name': 'pytest-diagnostics-${{ github.run_id }}',
                'path': 'evaluation_results/quality-*/pytest.txt\nevaluation_results/quality-*/pytest/pytest.xml\n',
                'if-no-files-found': 'warn',
                'retention-days': '7',
            },
        },
        {
            'name': 'Upload quality evidence',
            'if': 'always()',
            'uses': 'actions/upload-artifact@v4',
            'with': {
                'name': 'quality-evidence-${{ github.run_id }}-${{ github.run_attempt }}',
                'path': 'evaluation_results/quality-*/**\n!evaluation_results/quality-*/**/mypy-cache/**\n',
                'if-no-files-found': 'warn',
                'retention-days': '7',
            },
        },
    ]


def test_ci_always_requires_full_on_integration_events():
    """feature pushだけを外し、PR/main/queue/manualのFull jobを無条件で保持する。"""
    _assert_ci_contract(
        _load_workflow((ROOT / '.github/workflows/quality.yml').read_text(encoding='utf-8'))
    )


@pytest.mark.parametrize(
    'mutation',
    [
        'feature_push',
        'paths',
        'skip_draft',
        'head_checkout',
        'quick',
        'ignore_exit',
        'parallel',
        'cancel',
        'no_main',
        'no_pr',
        'no_queue',
        'missing_evidence',
        'write_token',
    ],
)
def test_ci_guard_rejects_required_check_bypasses(mutation):
    """workflow条件・終了無視・head検査への置換など、Fullを省略する改変を検出する。"""
    document = _load_workflow((ROOT / '.github/workflows/quality.yml').read_text(encoding='utf-8'))
    _assert_ci_contract(document)
    changed = copy.deepcopy(document)
    job = changed['jobs']['quality']
    if mutation == 'feature_push':
        changed['on']['push'] = None
    elif mutation == 'paths':
        changed['on']['pull_request']['paths'] = ['reversi_player/**']
    elif mutation == 'skip_draft':
        job['if'] = '${{ !github.event.pull_request.draft }}'
    elif mutation == 'head_checkout':
        job['steps'][0]['with']['ref'] = '${{ github.event.pull_request.head.sha }}'
    elif mutation == 'quick':
        job['steps'][3]['run'] = 'python -B -m tools.feedback --profile quick'
    elif mutation == 'ignore_exit':
        job['steps'][3]['continue-on-error'] = 'true'
    elif mutation == 'parallel':
        job['strategy'] = {'matrix': {'worker': ['1', '2']}}
    elif mutation == 'cancel':
        changed['concurrency'] = {'group': '${{ github.ref }}', 'cancel-in-progress': 'true'}
    elif mutation.startswith('no_'):
        del changed['on'][
            {'no_main': 'push', 'no_pr': 'pull_request', 'no_queue': 'merge_group'}[mutation]
        ]
    elif mutation == 'missing_evidence':
        job['steps'].pop()
    else:
        changed['permissions']['contents'] = 'write'
    with pytest.raises(AssertionError):
        _assert_ci_contract(changed)


def test_ci_loader_rejects_duplicate_and_aliased_keys():
    """一般YAML loaderの後勝ちやalias展開で検証対象が変わる反例を拒否する。"""
    for text in ('on: push\non: pull_request\n', 'on: &events [push]\nother: *events\n'):
        with pytest.raises(AssertionError):
            _load_workflow(text)
