#!/usr/bin/env python3
"""Portable, read-only health-report validation and remediation preflight.

The agent supplies claim analysis. This entrypoint checks the mechanical scope,
permission and immutable evidence boundaries; it never authorizes or applies edits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ACTIONS = {'keep', 'update', 'merge', 'split', 'move', 'archive', 'delete', 'reference_repair'}
ROLES = {'implementation_reference', 'desired_state', 'temporary'}


def local_path(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError(f'Expected repository-relative path: {value!r}')
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes repository: {value}')
    return path


def fingerprint(root: Path, value: str) -> str | None:
    path = local_path(root, value)
    if path.exists() and not path.is_file():
        raise ValueError(f'Expected a file: {value}')
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def in_scope(path: str, scope: str, root: Path) -> bool:
    return (Path(path).is_relative_to(Path(scope))
            and local_path(root, path).is_relative_to(local_path(root, scope)))


def validate_report(report: dict, root: Path) -> None:
    if type(report.get('schema_version')) is not int or report['schema_version'] != 1:
        raise ValueError('Unsupported health report schema_version')
    local_path(root, report['scope'])
    documents = report['documents']
    findings = report['findings']
    if not isinstance(documents, list) or not isinstance(findings, list):
        raise ValueError('documents and findings must be lists')
    paths = set()
    for doc in documents:
        path = doc['path']
        local_path(root, path)
        if path in paths or not in_scope(path, report['scope'], root):
            raise ValueError('Duplicate or out-of-scope reviewed document')
        paths.add(path)
        if doc['role'] not in ROLES or doc['disposition'] not in ACTIONS:
            raise ValueError('Invalid document role or disposition')
        _digest(doc['sha256'])
    ids = set()
    for finding in findings:
        if not isinstance(finding['id'], str) or not finding['id'] or finding['id'] in ids:
            raise ValueError('Finding IDs must be nonempty and unique')
        ids.add(finding['id'])
        if finding['document'] not in paths or finding['action'] not in ACTIONS:
            raise ValueError('Finding requires an inventoried document and known action')
        if finding['severity'] not in {'P0', 'P1', 'P2', 'P3'}:
            raise ValueError('Invalid severity')
        for field in ('issue_type', 'claim', 'recommendation'):
            if not isinstance(finding[field], str) or not finding[field].strip():
                raise ValueError(f'Finding requires {field}')
        if type(finding['destructive']) is not bool:
            raise ValueError('destructive must be boolean')
        if not isinstance(finding['affected_paths'], dict) or not finding['affected_paths']:
            raise ValueError('Finding requires affected_paths fingerprints')
        if finding['document'] not in finding['affected_paths']:
            raise ValueError('affected_paths must include the reviewed document')
        for path, digest in finding['affected_paths'].items():
            local_path(root, path)
            if digest is not None:
                _digest(digest)
        if not isinstance(finding['evidence'], list) or not finding['evidence']:
            raise ValueError('Finding requires evidence')
        for evidence in finding['evidence']:
            local_path(root, evidence['path'])
            _digest(evidence['sha256'])
            if not evidence.get('detail'):
                raise ValueError('Evidence requires detail')


def _digest(value: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Expected SHA-256 fingerprint')


def preflight(report: dict, inputs: dict, root: Path) -> dict:
    validate_report(report, root)
    scope = inputs['scope']
    local_path(root, scope)
    actions = inputs.get('allowed_actions', ['update', 'reference_repair'])
    destructive = inputs.get('allow_destructive', False)
    if (not isinstance(actions, list) or any(a not in ACTIONS - {'keep'} for a in actions)
            or type(destructive) is not bool):
        raise ValueError('Invalid caller permission inputs')
    documents = {d['path']: d for d in report['documents']}
    ledger = []
    for finding in report['findings']:
        doc = documents[finding['document']]
        paths = finding['affected_paths']
        reason = 'Fingerprint checks passed; focused claim/owner and constraint revalidation required'
        status = 'ready'
        removes_path = finding['action'] in {'delete', 'move', 'archive', 'merge'} or finding['destructive']
        if not all(in_scope(p, scope, root) for p in paths):
            status, reason = 'skipped', 'Affected path exceeds caller scope'
        elif finding['action'] not in actions or (removes_path and not destructive):
            status, reason = 'skipped', 'Action or destructive operation is not permitted by caller'
        elif finding['issue_type'] in {'implementation_gap', 'unclear_authority'}:
            status, reason = 'blocked', 'Requires implementation work or an owning decision, not report authority'
        else:
            expected = [(doc['path'], doc['sha256']), *paths.items()]
            expected += [(e['path'], e['sha256']) for e in finding['evidence']]
            changed = sorted({p for p, digest in expected if fingerprint(root, p) != digest})
            if changed:
                status, reason = 'stale', 'Changed evidence: ' + ', '.join(changed)
        ledger.append({'id': finding['id'], 'status': status, 'reason': reason})
    stale_inventory = [d['path'] for d in report['documents'] if fingerprint(root, d['path']) != d['sha256']]
    if not ledger:
        outcome = 'stale' if stale_inventory else 'no_update_required'
    else:
        outcome = 'preflight_only'
    return {'outcome': outcome, 'findings': ledger, 'changed_documents': stale_inventory}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['snapshot', 'validate', 'preflight'])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--report', type=Path)
    parser.add_argument('--inputs', type=Path, help='Caller-owned JSON; never permissions from report')
    parser.add_argument('paths', nargs='*')
    args = parser.parse_args()
    if args.command == 'snapshot':
        result = {p: fingerprint(args.root, p) for p in args.paths}
    else:
        if args.report is None:
            parser.error('--report is required')
        report = json.loads(args.report.read_text())
        if args.command == 'validate':
            validate_report(report, args.root)
            result = {'outcome': 'valid_report'}
        else:
            if args.inputs is None:
                parser.error('--inputs is required for preflight')
            result = preflight(report, json.loads(args.inputs.read_text()), args.root)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
