"""Durable generation jobs; browser and web-worker lifetimes are independent."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .position_integration import read, save

BACKEND = Path(__file__).resolve().parents[1]
JOBS = BACKEND / 'outputs/.jobs'


def directory(job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', job_id):
        raise ValueError('任务编号格式无效。')
    return JOBS / job_id


def alive(job, pid):
    if not isinstance(pid, int):
        return False
    try:
        command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        return b'src.generation_jobs' in command and str(job).encode() in command
    except OSError:
        return False


def status(job_id, cursor=0):
    folder = directory(job_id)
    state = read(folder/'state.json')
    if state['state'] == 'running' and not alive(folder, state.get('pid')):
        state = {**state, 'state': 'interrupted', 'error': '生成进程已停止，已保存阶段结果；请检查任务后再重新生成。'}
    events, offset = [], max(0, int(cursor))
    partial = False
    path = folder/'events.jsonl'
    if path.exists():
        with path.open('rb') as stream:
            size = path.stat().st_size
            if offset > size:
                raise ValueError('进度游标无效。')
            stream.seek(offset)
            total = 0
            for _ in range(150):
                line = stream.readline()
                if not line.endswith(b'\n'):
                    partial = bool(line)
                    break
                events.append(json.loads(line))
                offset = stream.tell()
                total += len(line)
                if total > 512 * 1024:
                    break
    if state['state'] in ('failed', 'interrupted'):
        events = [event for event in events if event.get('type') != 'success']
    elif state['state'] == 'completed' and state.get('result'):
        # Replay the latest audited result while preserving the append-only log.
        events = [state['result'] if event.get('type') == 'success' else event for event in events]
    return {**state, 'events': events, 'cursor': offset,
            'has_more': path.exists() and offset < path.stat().st_size and not (partial and state['state'] != 'running')}


def submit(job_id, params):
    from .position_integration import check_request
    from . import registry
    if not isinstance(params, dict):
        raise ValueError('生成参数必须为对象。')
    check_request(params)
    folder = directory(job_id)
    JOBS.mkdir(parents=True, exist_ok=True)
    with (JOBS/'admission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if folder.exists():
            if read(folder/'request.json') != params:
                raise ValueError('同一任务编号不能提交不同内容。')
            return status(job_id)
        for state_path in JOBS.glob('*/state.json'):
            current = status(state_path.parent.name)
            if current['state'] == 'running':
                raise RuntimeError('已有剧本正在生成，请等待当前任务完成。')
        folder.mkdir(mode=0o700)
        save(folder/'request.json', params)
        sid = str(time.time_ns())
        save(folder/'state.json', {'job_id': job_id, 'session_id': sid, 'state': 'running', 'pid': None})
        registry.register_session(sid, {}, scene_id=','.join(params.get('scene_pool') or [params.get('scene_id', '')]),
            act_count=params.get('act_count', 1), label='生成中', form_data=registry.snapshot_form_data(params), status='running')
        try:
            with (folder/'worker.log').open('ab') as log:
                worker = subprocess.Popen([sys.executable, '-m', 'src.generation_jobs', 'worker', str(folder)],
                    cwd=BACKEND, stdout=log, stderr=log, start_new_session=True)
            # Worker waits for this record before starting, avoiding lost terminal states.
            save(folder/'state.json', {'job_id': job_id, 'session_id': sid, 'state': 'running', 'pid': worker.pid})
        except Exception:
            save(folder/'state.json', {'job_id': job_id, 'session_id': sid, 'state': 'failed', 'error': '启动生成任务失败。'})
            registry.update_session_status(sid, 'failed', '启动生成任务失败。')
            raise
    return status(job_id)


def worker(folder):
    from dotenv import load_dotenv
    load_dotenv(BACKEND/'.env')
    from .position_integration import configure_models
    configure_models()
    import certifi
    os.environ['SSL_CERT_FILE'] = certifi.where()
    from .autogen_bridge import AutoGenStreamBridge
    from .autogen_pipeline import run_autogen_pipeline
    from .resource_loader import ResourceLoader
    from . import registry
    folder = Path(folder)
    for _ in range(100):
        state = read(folder/'state.json')
        if state.get('pid') == os.getpid():
            break
        time.sleep(.05)
    else:
        raise RuntimeError('Worker was not admitted')
    params = read(folder/'request.json')
    params['_history_session_id'] = state['session_id']
    bridge = AutoGenStreamBridge(on_error=lambda error: registry.update_session_status(state['session_id'], 'failed', error))
    success, error = None, None
    try:
        bridge.run_in_thread(run_autogen_pipeline(bridge, ResourceLoader(), params))
        with (folder/'events.jsonl').open('a', encoding='utf-8') as stream:
            for line in bridge.flask_generator():
                event = json.loads(line)
                if event.get('type') == 'heartbeat':
                    continue
                stream.write(line)
                stream.flush()
                if event.get('type') == 'success':
                    success = event
                if event.get('type') == 'error':
                    error = event.get('message', '生成失败')
    except Exception as exc:
        error = str(exc)
    state.update(state='completed' if success else 'failed', result=success, error=error if not success else None)
    if not success:
        state['error'] = error or '生成进程未返回完整结果。'
        registry.update_session_status(state['session_id'], 'failed', state['error'])
    save(folder/'state.json', state)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'worker':
        worker(sys.argv[2])
    else:
        raise SystemExit('Use generation_jobs worker <job directory>')
