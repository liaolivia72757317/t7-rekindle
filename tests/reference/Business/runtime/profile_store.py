"""Atomic local player configuration, separate from launcher settings and scripts."""
import json
import os
from pathlib import Path
import tempfile

MAX_BYTES = 1024 * 1024


def load(path, validate):
    path = Path(path)
    warnings = []
    for candidate in (path, path.with_suffix(path.suffix + '.bak')):
        try:
            with candidate.open('rb') as source:
                data = source.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError('player profile exceeds size limit')
            value = json.loads(data.decode('utf-8'))
            if not validate(value):
                raise ValueError('invalid player profile')
            if candidate != path:
                warnings.append('已从备份恢复武将配置。')
            return value, warnings
        except FileNotFoundError:
            continue
        except (OSError, ValueError, UnicodeError) as error:
            warnings.append(f'武将配置读取失败，原文件已保留：{type(error).__name__}')
    if warnings:
        raise ValueError('武将配置及备份无效；请保留文件并修复后重试。')
    return None, []


def _replace(path, value):
    encoded = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
    if len(encoded) > MAX_BYTES:
        raise ValueError('player profile exceeds size limit')
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(path, value, previous):
    path = Path(path)
    if previous is not None:
        _replace(path.with_suffix(path.suffix + '.bak'), previous)
    _replace(path, value)
