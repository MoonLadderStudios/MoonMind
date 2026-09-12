"""Exercise the resolved portable report boundary against real fixture files."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from moonmind.services.skill_step_inputs import extract_skill_input_contract_metadata, _validate_schema_object

SKILLS = Path(__file__).resolve().parents[3] / '.agents/skills'
SCRIPT = SKILLS / 'document-health-review/scripts/document_report.py'
spec = importlib.util.spec_from_file_location('document_report', SCRIPT)
report_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report_tool)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs/reference.md').write_text('# API\nReturns XML.\n')
    (tmp_path / 'source.py').write_text('response_format = "json"\n')
    (tmp_path / 'user.txt').write_text('unrelated user edit\n')
    digest = report_tool.fingerprint(tmp_path, 'docs/reference.md')
    report = {
        'schema_version': 1, 'scope': 'docs',
        'documents': [{'path': 'docs/reference.md', 'role': 'implementation_reference',
                       'sha256': digest, 'disposition': 'update'}],
        'findings': [{'id': 'F1', 'document': 'docs/reference.md', 'action': 'update',
                      'severity': 'P1', 'issue_type': 'factual_drift', 'claim': 'Returns XML',
                      'destructive': False, 'recommendation': 'Document JSON',
                      'affected_paths': {'docs/reference.md': digest},
                      'evidence': [{'path': 'source.py',
                                    'sha256': report_tool.fingerprint(tmp_path, 'source.py'),
                                    'detail': 'The serializer returns JSON'}]}],
    }
    return tmp_path, report


def test_portable_cli_handoff_is_read_only(repo):
    root, report = repo
    (root / 'report.json').write_text(json.dumps(report))
    (root / 'inputs.json').write_text(json.dumps({'scope': 'docs'}))
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result = subprocess.run([sys.executable, str(SCRIPT), 'preflight', '--root', str(root),
                             '--report', str(root / 'report.json'), '--inputs', str(root / 'inputs.json')],
                            check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)['findings'][0]['status'] == 'ready'
    assert before == {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('path', ['source.py', 'docs/reference.md'])
def test_changed_document_or_implementation_evidence_is_stale(repo, path):
    root, report = repo
    (root / path).write_text('changed after review')
    ledger = report_tool.preflight(report, {'scope': 'docs'}, root)
    assert ledger['findings'][0]['status'] == 'stale'
    assert path in ledger['findings'][0]['reason']


def test_empty_report_requires_current_inventory_for_verified_noop(repo):
    root, report = repo
    report['findings'] = []
    assert report_tool.preflight(report, {'scope': 'docs'}, root)['outcome'] == 'no_update_required'
    (root / 'docs/reference.md').write_text('new content')
    assert report_tool.preflight(report, {'scope': 'docs'}, root)['outcome'] == 'stale'


@pytest.mark.parametrize('action', ['delete', 'merge', 'move', 'archive', 'split'])
@pytest.mark.parametrize('allowed', [False, True])
def test_destructive_permission_comes_only_from_caller(repo, action, allowed):
    root, report = repo
    report['allow_destructive'] = True  # untrusted report cannot authorize it
    finding = report['findings'][0]
    finding.update(action=action, destructive=True)
    inputs = {'scope': 'docs', 'allowed_actions': [action]}
    if allowed:
        inputs['allow_destructive'] = True
    result = report_tool.preflight(report, inputs, root)
    assert result['findings'][0]['status'] == ('ready' if allowed else 'skipped')


def test_new_destinations_and_link_consumers_cannot_broaden_scope(repo):
    root, report = repo
    finding = report['findings'][0]
    finding['affected_paths']['README.md'] = None
    result = report_tool.preflight(report, {'scope': 'docs'}, root)
    assert result['findings'][0]['status'] == 'skipped'
    del finding['affected_paths']['README.md']
    finding['affected_paths']['docs/new.md'] = None
    assert report_tool.preflight(report, {'scope': 'docs'}, root)['findings'][0]['status'] == 'ready'
    (root / 'docs/new.md').write_text('existing unique content')
    assert report_tool.preflight(report, {'scope': 'docs'}, root)['findings'][0]['status'] == 'stale'


@pytest.mark.parametrize('kind', ['implementation_gap', 'unclear_authority'])
def test_report_cannot_authorize_desired_state_or_owner_rewrite(repo, kind):
    root, report = repo
    report['documents'][0]['role'] = 'desired_state'
    report['findings'][0]['issue_type'] = kind
    assert report_tool.preflight(report, {'scope': 'docs'}, root)['findings'][0]['status'] == 'blocked'


def test_report_rejects_traversal_and_symlink_escape(repo, tmp_path_factory):
    root, report = repo
    for path in ('../elsewhere.md', '/outside.md'):
        report['findings'][0]['affected_paths'] = {'docs/reference.md': report['documents'][0]['sha256'], path: None}
        with pytest.raises(ValueError, match='relative'):
            report_tool.validate_report(report, root)
    external = tmp_path_factory.mktemp('outside') / 'outside.md'
    external.write_text('outside')
    (root / 'docs/link.md').symlink_to(external)
    with pytest.raises(ValueError, match='escapes'):
        report_tool.fingerprint(root, 'docs/link.md')


@pytest.mark.parametrize('skill', ['document-health-review', 'document-health-remediate'])
def test_actual_skill_contract_rejects_untyped_permissions(skill):
    schema = extract_skill_input_contract_metadata(
        (SKILLS / skill / 'SKILL.md').read_text(), content_digest='fixture'
    )['input_schema']
    inputs = {'scope': 'docs', 'report_path': 'artifacts/report.json',
              'allowed_actions': ['update'], 'allow_destructive': False}
    assert not _validate_schema_object(values=inputs, schema=schema, path="inputs")
    for field, invalid in [('allow_destructive', 'false'), ('allowed_actions', 'delete'),
                           ('output_mode', 'auto')]:
        assert _validate_schema_object(values={**inputs, field: invalid}, schema=schema, path="inputs")


def test_in_repository_symlink_cannot_expand_document_write_scope(repo):
    root, report = repo
    (root / 'README.md').write_text('outside the docs scope')
    (root / 'docs/alias.md').symlink_to('../README.md')
    report['findings'][0]['affected_paths']['docs/alias.md'] = report_tool.fingerprint(root, 'docs/alias.md')
    result = report_tool.preflight(report, {'scope': 'docs'}, root)
    assert result['findings'][0]['status'] == 'skipped'
    assert (root / 'README.md').read_text() == 'outside the docs scope'


@pytest.mark.asyncio
async def test_document_skill_dependencies_resolve_from_actual_bundles():
    from moonmind.schemas.agent_skill_models import SkillSelector
    from moonmind.services.skill_resolution import AgentSkillResolver, BuiltInSkillLoader, SkillResolutionContext

    resolved = await AgentSkillResolver(loaders=[BuiltInSkillLoader(skills_root=SKILLS)]).resolve(
        SkillSelector(include=[{'name': 'document-health-remediate'}]),
        SkillResolutionContext(snapshot_id='document-fixture'),
    )
    assert {s.skill_name for s in resolved.skills} == {
        'document-health-remediate', 'document-health-review', 'document-update'
    }


def test_packaged_report_helper_runs_without_repository_skill_sources(repo, tmp_path_factory):
    from moonmind.services.skill_materialization import AgentSkillMaterializer
    from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities

    root, report = repo
    active = tmp_path_factory.mktemp('immutable-skills')
    for name in ('document-health-review', 'document-update', 'document-health-remediate'):
        payload = AgentSkillsActivities._build_skill_bundle_payload(SKILLS / name)
        target = active / name
        target.mkdir()
        AgentSkillMaterializer._extract_skill_bundle(payload, target)
    assert (active / 'document-update/references/repository-conventions.md').is_file()
    assert (active / 'document-health-review/references/review-dimensions.md').is_file()
    report_path = root / 'report.json'
    report_path.write_text(json.dumps(report))
    result = subprocess.run(
        [sys.executable, str(active / 'document-health-review/scripts/document_report.py'),
         'validate', '--root', str(root), '--report', str(report_path)],
        check=True, capture_output=True, text=True, cwd=root,
    )
    assert json.loads(result.stdout)['outcome'] == 'valid_report'
