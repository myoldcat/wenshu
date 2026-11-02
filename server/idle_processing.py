"""Local idle-work eligibility shared by queue claiming and model checkpoints.

The clock is always China Standard Time, independent of the container timezone.
No provider credentials or document text are stored in activity leases.
"""
from datetime import datetime, timedelta, timezone
import json
import re

BEIJING = timezone(timedelta(hours=8), 'Asia/Shanghai')
LEASE_SECONDS = 60
DEFAULT_IDLE = {'idleProcess': False, 'idleStart': '00:00', 'idleEnd': '08:00'}


def utc_now():
    return datetime.now(timezone.utc)


def validate_idle_window(preferences):
    for field in ('idleStart', 'idleEnd'):
        value = preferences.get(field)
        if not isinstance(value, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value):
            raise ValueError('闲时处理时间须为有效的 HH:MM 格式')
    if preferences['idleStart'] == preferences['idleEnd']:
        raise ValueError('闲时处理开始和结束时间不能相同')


def in_idle_window(preferences, moment=None):
    settings = DEFAULT_IDLE | preferences
    try:
        validate_idle_window(settings)
    except ValueError:
        return False
    moment = moment or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    clock = moment.astimezone(BEIJING).strftime('%H:%M')
    start, end = settings['idleStart'], settings['idleEnd']
    return start <= clock < end if start < end else clock >= start or clock < end


def stored_preferences(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_settings'").fetchone():
        return dict(DEFAULT_IDLE)
    row = db.execute("SELECT value_json FROM ai_settings WHERE key='preferences'").fetchone()
    return DEFAULT_IDLE | (json.loads(row[0]) if row else {})


def idle_block_reason(db, *, moment=None, exclude_job=None):
    """Read with the caller's transaction so claiming cannot race a new intent."""
    preferences = stored_preferences(db)
    if not preferences.get('idleProcess', False):
        return '闲时处理已关闭'
    if not preferences.get('defaultModelId'):
        return '请先设置默认模型'
    moment = moment or utc_now()
    if not in_idle_window(preferences, moment):
        return '等待设定的闲时时段（北京时间）'
    if db.execute('SELECT 1 FROM writing_activity WHERE active=1 AND expires_at>? LIMIT 1', (moment.timestamp(),)).fetchone():
        return '写作进行中，闲时处理已暂停'
    if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') AND origin<>'idle' AND id<>? LIMIT 1",
                  (exclude_job or '',)).fetchone():
        return '优先处理新文件或前台任务'
    return None


class IdlePaused(Exception):
    """Cooperative yield before the next provider request; checkpoints remain."""
