"""Version-bound author decisions and separately downloadable check reports.

Acknowledging an issue records an author's decision. It never changes evidence,
sentence support judgments or manuscript text, and cannot validate a later edit.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json

from .store import dumps, now

WORKFLOW_VERSION = 'writing-six-step-v1'


def six_step(project):
    return project.get('workflowVersion') == WORKFLOW_VERSION


def review_skipped(report):
    """Only a decision bound to this exact report can waive its reminders."""
    if not isinstance(report, dict):
        return False
    decision = report.get('disposition')
    return bool(isinstance(decision, dict) and decision.get('status') == 'skipped'
            and report.get('version') is not None and report.get('reportId')
            and decision.get('version') == report['version']
            and decision.get('reportId') == report['reportId'])


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def finalize_review(project, report):
    """Attach stable issue identities and apply only a matching author decision."""
    identities = {}
    for issue in report['issues']:
        identity = {key: value for key, value in issue.items()
                    if key not in ('id', 'acknowledged', 'confirmed', 'acknowledgedAt')}
        digest = _digest(identity)
        identities[digest] = identities.get(digest, 0) + 1
        issue['id'] = 'check_' + digest[:24] + '_' + str(identities[digest])
    report['reportId'] = 'report_' + _digest({
        'version': report['version'],
        'issues': report['issues'], 'wordCounts': report['wordCounts'],
        'coverage': report['coverage'], 'citationReviewMethods': report['citationReviewMethods'],
    })[:32]
    decision = project.get('reviewDisposition')
    current = (isinstance(decision, dict) and decision.get('version') == project['version']
               and decision.get('reportId') == report['reportId']
               and decision.get('status') in ('acknowledged', 'skipped'))
    report['disposition'] = copy.deepcopy(decision) if current else None
    accepted = set(decision.get('issueIds', [])) if current and decision['status'] == 'acknowledged' else set()
    for issue in report['issues']:
        issue['acknowledged'] = issue['confirmed'] = issue['id'] in accepted
        if issue['acknowledged']:
            issue['acknowledgedAt'] = decision['decidedAt']
    unresolved = [issue for issue in report['issues'] if not issue['acknowledged']]
    errors = sum(issue['severity'] == 'error' for issue in unresolved)
    report['unconfirmedCounts'] = ({'errors': 0, 'warnings': 0, 'total': 0}
                                   if review_skipped(report) else
                                   {'errors': errors, 'warnings': len(unresolved) - errors,
                                    'total': len(unresolved)})
    # Preserve machine findings, even after the author has accepted them.
    report['authorConfirmed'] = bool(current and decision['status'] == 'acknowledged'
                                     and not unresolved)
    from .writing_review import review_requires_draft
    report['requiresDraftConsent'] = review_requires_draft(report)
    return report


def _location(project, issue):
    section = next((item for item in project.get('sections', [])
                    if item.get('id') == issue.get('sectionId')), None)
    label = section['title'] if section else '全文'
    if issue.get('paragraphIndex'):
        label += ' · 第 ' + str(issue['paragraphIndex']) + ' 段'
    return label


def render_review_html(project, report):
    escape = lambda value: html.escape(str(value))
    skipped = review_skipped(report)
    label = ('已由作者确认' if report.get('authorConfirmed') else
             '作者已跳过校验' if skipped else '待作者核对')
    pieces = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>' + escape(project['title']) + ' · 核对报告</title>',
        '<style>body{max-width:900px;margin:40px auto;padding:0 24px;font-family:system-ui,sans-serif;line-height:1.8;color:#222}article{border-top:1px solid #ddd;margin-top:24px;padding-top:12px}p{white-space:pre-wrap;overflow-wrap:anywhere}small{color:#555}</style></head><body>',
        '<h1>' + escape(project['title']) + ' · 核对报告</h1>',
        '<p>文稿版本 ' + str(report['version']) + ' · ' + escape(report['checkedAt']) + '</p>',
        '<p>' + label + '</p>', '<p>' + escape(report['notice']) + '</p>',
        '<p>全文 ' + str(report['wordCounts']['total']) + ' 字；发现 '
        + str(report['counts']['errors']) + ' 项问题、' + str(report['counts']['warnings'])
        + ' 项提醒' + ('。已跳过本次校验。' if skipped else
          '；尚未确认 ' + str(report['unconfirmedCounts']['total']) + ' 项。') + '</p>',
    ]
    for issue in report['issues']:
        pieces.extend([
            '<article><h2>' + escape(_location(project, issue)) + '</h2>',
            '<p>' + ('已跳过校验' if skipped else
                     '作者已确认' if issue.get('acknowledged') else '待核对') + ' · '
            + ('问题' if issue['severity'] == 'error' else '提醒') + '</p>',
            '<p>' + escape(issue['message']) + '</p>',
        ])
        if issue.get('sentence') and issue['sentence'] not in issue['message']:
            pieces.append('<p>对应正文：' + escape(issue['sentence']) + '</p>')
        pieces.append('</article>')
    if not report['issues']:
        pieces.append('<p>当前检查未发现待办事项；检查结果不代替事实核实。</p>')
    pieces.append('</body></html>')
    return '\n'.join(pieces)


class WritingCheckReportMixin:
    def review_confirm(self, project_id, expected, issue_ids=None, all_issues=False):
        from .writing import WritingConflict, _version
        if not isinstance(all_issues, bool):
            raise ValueError('全局确认须为布尔值')
        if issue_ids is None:
            issue_ids = []
        if (not isinstance(issue_ids, list) or len(issue_ids) > 10000
                or any(not isinstance(value, str) or not value or len(value) > 100 for value in issue_ids)):
            raise ValueError('确认事项须为有效的核对事项编号列表')
        if all_issues and issue_ids:
            raise ValueError('请按事项确认，或选择全局确认')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            self._assert_no_active_tasks(db, project_id)
            report = self._review(db, project)
            known = {issue['id'] for issue in report['issues']}
            requested = set(issue_ids)
            if not requested <= known:
                raise WritingConflict('核对事项已经变化，请刷新报告后确认')
            if not all_issues and not requested:
                raise ValueError('请选择要确认的事项，或选择全局确认')
            accepted = {issue['id'] for issue in report['issues'] if issue.get('acknowledged')}
            accepted.update(known if all_issues else requested)
            project['reviewDisposition'] = {
                'version': project['version'], 'reportId': report['reportId'],
                'status': 'acknowledged', 'issueIds': sorted(accepted), 'decidedAt': now(),
            }
            report = self._review(db, project)
            project['review'] = report
            db.execute('UPDATE writing_projects SET content_json=? WHERE id=? AND version=?',
                       (dumps(project), project_id, expected))
        return {'project': self.get(project_id), 'review': report}

    def review_skip(self, project_id, expected):
        from .writing import _version
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            self._assert_no_active_tasks(db, project_id)
            report = self._review(db, project)
            project['reviewDisposition'] = {
                'version': project['version'], 'reportId': report['reportId'],
                'status': 'skipped', 'issueIds': [], 'decidedAt': now(),
            }
            report = self._review(db, project)
            project['review'] = report
            db.execute('UPDATE writing_projects SET content_json=? WHERE id=? AND version=?',
                       (dumps(project), project_id, expected))
        return {'project': self.get(project_id), 'review': report}

    def export_review(self, project_id, expected, format='html'):
        from .writing import _version
        if format not in ('html', 'json'):
            raise ValueError('核对报告导出格式须为 html 或 json')
        with self.store.connect() as db:
            db.execute('BEGIN')
            project = self._load(db, project_id)
            _version(project, expected)
            report = self._review(db, project)
        content = (render_review_html(project, report) if format == 'html' else
                   json.dumps({'title': project['title'], 'review': report}, ensure_ascii=False, indent=2))
        return {'content': content, 'title': project['title'] + ' · 核对报告',
                'version': project['version'], 'format': format}
