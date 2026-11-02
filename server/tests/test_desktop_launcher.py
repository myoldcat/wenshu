"""Desktop lifecycle checks without opening a real library or model session."""
from pathlib import Path
import json
import subprocess
import sys

import pytest

from desktop import launcher


def test_default_profile_is_current_user_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert launcher.default_profile() == tmp_path / 'Wenshu'


def test_source_and_frozen_children_use_explicit_role_and_paths(monkeypatch, tmp_path):
    arguments = dict(data_dir=tmp_path / 'data', frontend_dir=tmp_path / 'frontend',
                     control_dir=tmp_path / 'control', port=18787)
    command = launcher.service_command('worker', **arguments)
    assert command[:4] == [sys.executable, '-m', 'desktop.service', '--worker']
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'installed' / 'Wenshu.exe'))
    command = launcher.service_command('pdf', **arguments)
    assert command[:2] == [str(tmp_path / 'installed' / 'WenshuService.exe'), '--pdf']
    assert command[command.index('--data-dir') + 1] == str(arguments['data_dir'])


def test_frozen_library_cannot_be_written_to_extraction_directory(monkeypatch, tmp_path):
    frontend = tmp_path / 'bundle/frontend'
    frontend.mkdir(parents=True)
    (frontend / 'index.html').write_text('main')
    (frontend / 'operations.html').write_text('operations')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path / 'bundle'), raising=False)
    with pytest.raises(RuntimeError, match='资料目录'):
        launcher.validate_paths(tmp_path / 'profile', tmp_path / 'bundle/data', frontend)
    assert not (tmp_path / 'bundle/data').exists()


def test_frontend_must_include_independent_operations_entry(tmp_path):
    frontend = tmp_path / 'frontend'
    frontend.mkdir()
    (frontend / 'index.html').write_text('main')
    with pytest.raises(RuntimeError, match='界面文件'):
        launcher.validate_paths(tmp_path / 'profile', tmp_path / 'data', frontend)


def test_busy_port_never_starts_or_recovers_a_worker(monkeypatch, tmp_path):
    processes = launcher.DesktopProcesses(profile=tmp_path, data_dir=tmp_path / 'data',
                                         frontend_dir=tmp_path / 'frontend', port=18787)
    monkeypatch.setattr(launcher, 'port_available', lambda port: False)
    monkeypatch.setattr(processes, 'spawn', lambda role: pytest.fail('must not spawn'))
    with pytest.raises(RuntimeError, match='端口'):
        processes.start()


def test_stop_only_signals_then_waits_without_terminating_jobs(tmp_path):
    class Process:
        def __init__(self): self.waited = False
        def wait(self): self.waited = True
        def terminate(self): pytest.fail('active jobs must not be terminated')
        def kill(self): pytest.fail('active jobs must not be killed')
    processes = launcher.DesktopProcesses(profile=tmp_path, data_dir=tmp_path / 'data',
                                         frontend_dir=tmp_path / 'frontend', port=18787)
    processes.running = True
    api, worker = Process(), Process()
    processes.processes = [('api', api), ('worker', worker)]
    processes.stop()
    assert (processes.control / 'stop').is_file()
    assert not api.waited and not worker.waited
    processes.wait()
    assert api.waited and worker.waited


def test_hidden_child_receives_session_only_in_environment(monkeypatch, tmp_path):
    captured = {}
    def popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        return object()
    monkeypatch.setattr(subprocess, 'Popen', popen)
    processes = launcher.DesktopProcesses(profile=tmp_path, data_dir=tmp_path / 'data',
                                         frontend_dir=tmp_path / 'frontend', port=18787)
    processes.spawn('api')
    assert processes.token not in ' '.join(captured['command'])
    assert captured['env']['WENSHU_DESKTOP_TOKEN'] == processes.token
    assert captured['env']['WENSHU_DATA_DIR'] == str(tmp_path / 'data')
    assert captured['stdin'] == subprocess.DEVNULL
    assert captured['close_fds'] is True
    for stream in processes.logs:
        stream.close()


def test_failed_startup_reclaims_only_worker_that_never_became_ready(tmp_path):
    class InitializingProcess:
        def __init__(self): self.terminated = False
        def wait(self, timeout=None):
            assert timeout == 10
            if not self.terminated:
                raise subprocess.TimeoutExpired('initializing-helper', timeout)
        def terminate(self): self.terminated = True
    processes = launcher.DesktopProcesses(profile=tmp_path, data_dir=tmp_path / 'data',
                                         frontend_dir=tmp_path / 'frontend', port=18787)
    worker = InitializingProcess()
    processes.processes = [('worker', worker)]
    processes.stop()
    processes.wait()
    assert worker.terminated
