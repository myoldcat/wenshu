"""Windows desktop lifecycle for the existing local document application.

The window owns two hidden child processes. A stop marker prevents new claims
and lets requests already in progress finish before those processes exit.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, build_opener


APP_NAME = '文枢'
APP_MUTEX = r'Local\Wenshu.Desktop'
ACTIVATE_EVENT = r'Local\Wenshu.Desktop.Activate'
DEFAULT_PORT = 18787


class DesktopStartupError(RuntimeError):
    """A fixed, user-facing startup message; dependency traces stay in logs."""


def bundle_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))


def default_profile():
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise DesktopStartupError('无法找到 Windows 用户数据目录。')
    return Path(base) / 'Wenshu'


def webview2_available():
    """Detect the installed shared runtime, independently of the Edge browser."""
    import winreg
    key_name = r'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as key:
                    version = winreg.QueryValueEx(key, 'pv')[0]
                    parts = tuple(int(part) for part in version.split('.'))
                    if len(parts) == 4 and parts > (0, 0, 0, 0):
                        return True
            except (OSError, ValueError, AttributeError):
                pass
    return False


def service_command(role, *, data_dir, frontend_dir, control_dir, port):
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable).parent / 'WenshuService.exe'
        command = [str(executable)]
    else:
        command = [sys.executable, '-m', 'desktop.service']
    return [*command, '--' + role, '--data-dir', str(data_dir),
            '--frontend-dir', str(frontend_dir), '--control-dir', str(control_dir),
            '--port', str(port)]


def validate_paths(profile, data_dir, frontend_dir):
    profile, data_dir, frontend_dir = map(lambda p: Path(p).resolve(), (profile, data_dir, frontend_dir))
    if not (frontend_dir / 'index.html').is_file() or not (frontend_dir / 'operations.html').is_file():
        raise DesktopStartupError('程序界面文件不完整，请重新安装文枢。')
    # Never use the executable's read-only installation tree or its temporary
    # extraction folder for durable documents.
    root = bundle_root().resolve()
    if getattr(sys, 'frozen', False):
        for installed in (root, Path(sys.executable).resolve().parent):
            if data_dir == installed or data_dir.is_relative_to(installed):
                raise DesktopStartupError('资料目录必须位于程序安装目录之外。')
    for directory in (profile, data_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return profile, data_dir, frontend_dir


def port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == 'nt':
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def message(text, *, error=False):
    if os.name == 'nt':
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10 if error else 0x40)


class WindowsInstance:
    """The installer observes this mutex and never forcibly closes the app."""

    def __init__(self):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateEventW.restype = wintypes.HANDLE
        self.kernel.SetEvent.argtypes = [wintypes.HANDLE]
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.mutex = self.kernel.CreateMutexW(None, True, APP_MUTEX)
        if not self.mutex:
            raise OSError('无法创建文枢运行锁。')
        self.primary = ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
        self.event = self.kernel.CreateEventW(None, False, False, ACTIVATE_EVENT)
        if not self.event:
            self.close()
            raise OSError('无法创建文枢窗口通知。')
        if not self.primary:
            self.kernel.SetEvent(self.event)

    def listen(self, window, finished):
        while not finished.is_set():
            if self.kernel.WaitForSingleObject(self.event, 500) == 0:
                try:
                    window.restore()
                    window.show()
                except Exception:
                    logging.warning('Unable to activate the existing window')

    def close(self):
        if getattr(self, 'event', None):
            self.kernel.CloseHandle(self.event)
            self.event = None
        if getattr(self, 'mutex', None):
            if self.primary:
                self.kernel.ReleaseMutex(self.mutex)
            self.kernel.CloseHandle(self.mutex)
            self.mutex = None


class DesktopProcesses:
    def __init__(self, *, profile, data_dir, frontend_dir, port):
        self.profile, self.data_dir, self.frontend_dir, self.port = profile, data_dir, frontend_dir, port
        self.control = profile / 'run' / secrets.token_hex(12)
        self.control.mkdir(parents=True)
        self.token = secrets.token_urlsafe(32)
        self.processes = []
        self.logs = []
        self.stopping = threading.Event()
        self.running = False
        self.opener = build_opener(ProxyHandler({}))

    def spawn(self, role):
        logs = self.profile / 'logs'
        logs.mkdir(exist_ok=True)
        log = open(logs / (role + '.log'), 'ab', buffering=0)
        self.logs.append(log)
        env = os.environ.copy()
        env.update({'WENSHU_DATA_DIR': str(self.data_dir), 'WENSHU_DESKTOP_TOKEN': self.token,
                    'WENSHU_DESKTOP_PARENT_PID': str(os.getpid()),
                    'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
                    'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1'})
        process = subprocess.Popen(
            service_command(role, data_dir=self.data_dir, frontend_dir=self.frontend_dir,
                            control_dir=self.control, port=self.port),
            cwd=bundle_root(), env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
        self.processes.append((role, process))
        return process

    def start(self):
        if not port_available(self.port):
            raise DesktopStartupError('文枢需要的本地端口已被占用，请关闭其他文枢实例后重试。')
        api = self.spawn('api')
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if api.poll() is not None:
                raise DesktopStartupError('文枢本地服务未能启动，请查看用户目录中的 logs/api.log。')
            try:
                with self.opener.open(f'http://127.0.0.1:{self.port}/desktop/ready', timeout=1) as response:
                    ready = json.load(response)
                    if ready.get('application') == 'wenshu-desktop' and ready.get('status') == 'ready':
                        break
            except (OSError, ValueError):
                pass
            time.sleep(.15)
        else:
            raise DesktopStartupError('文枢本地服务启动超时，请重新打开。')
        worker = self.spawn('worker')
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                raise DesktopStartupError('文枢后台任务服务未能启动，请查看 logs/worker.log。')
            state = self.worker_state()
            if state.get('status') in ('running', 'ready', 'stopping'):
                self.running = True
                return
            time.sleep(.15)
        raise DesktopStartupError('文枢后台任务服务启动超时，请重新打开。')

    def worker_state(self):
        try:
            return json.loads((self.control / 'worker-state.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def stop(self):
        self.stopping.set()
        (self.control / 'stop').touch(exist_ok=True)

    def wait(self):
        # No forced termination: preserve in-flight model jobs and database
        # transactions. The window displays a waiting message during this phase.
        for role, process in reversed(self.processes):
            if self.running or (role == 'worker' and self.worker_state().get('status')):
                process.wait()
                continue
            # Before the worker publishes its first ready state it cannot
            # claim a job. A broken DLL/import must not leave an invisible
            # launcher holding the application mutex forever.
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if role == 'worker' and self.worker_state().get('status'):
                    process.wait()
                else:
                    process.terminate()
                    process.wait(timeout=10)
        for stream in self.logs:
            stream.close()


EXIT_HTML = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>文枢</title>
<style>body{margin:0;background:#faf6ef;color:#4e2923;font:18px/1.8 "Microsoft YaHei",sans-serif;
display:grid;place-content:center;min-height:100vh}main{max-width:620px;padding:48px}h1{color:#9d3026;font-size:28px}p{font-size:16px}</style>
<main><h1>正在安全退出文枢</h1><p>正在等待当前后台任务完成。自动写作可能需要一些时间。</p>
<p>尚未开始的任务会保留，下次打开文枢时继续。</p></main></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description='文枢 Windows 桌面版')
    parser.add_argument('--profile-dir', type=Path)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--frontend-dir', type=Path)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if os.name != 'nt':
        raise SystemExit('文枢桌面窗口需要 Windows。')
    instance, processes = None, None
    finished = threading.Event()
    try:
        instance = WindowsInstance()
        if not instance.primary:
            return 0
        if not 1024 <= args.port <= 65535:
            raise DesktopStartupError('本地端口须在 1024–65535 之间。')
        profile = args.profile_dir or default_profile()
        data_dir = args.data_dir or profile / 'data'
        frontend_dir = args.frontend_dir or (bundle_root() / ('frontend' if getattr(sys, 'frozen', False) else 'frontend/dist/client'))
        profile, data_dir, frontend_dir = validate_paths(profile, data_dir, frontend_dir)
        log_dir = profile / 'logs'
        log_dir.mkdir(exist_ok=True)
        logging.basicConfig(filename=log_dir / 'desktop.log', level=logging.WARNING,
                            format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
        if not webview2_available():
            raise DesktopStartupError('未找到可用的 WebView2 运行库。请重新运行文枢安装程序以自动下载安装。')
        import webview
        processes = DesktopProcesses(profile=profile, data_dir=data_dir, frontend_dir=frontend_dir, port=args.port)
        processes.start()
        webview.settings['ALLOW_DOWNLOADS'] = True
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
        url = f'http://127.0.0.1:{args.port}/desktop/launch?token={quote(processes.token, safe="")}'
        window = webview.create_window(APP_NAME, url, width=1440, height=960,
                                       min_size=(1024, 700), background_color='#faf6ef', text_select=True)
        closing = threading.Event()
        operations = []

        def show_operations():
            if closing.is_set():
                return
            current_url = window.get_current_url()
            if not current_url or urlsplit(current_url).path != '/':
                message('文枢界面正在加载，请稍后再打开写作运维。')
                return
            for current in operations:
                if current in webview.windows:
                    current.restore()
                    current.show()
                    return
            operations.clear()
            operations.append(webview.create_window('文枢 · 写作运维',
                f'http://127.0.0.1:{args.port}/operations.html', width=1440, height=960,
                min_size=(1024, 700), background_color='#faf6ef', text_select=True))

        def finish_close():
            try:
                window.load_html(EXIT_HTML)
                processes.wait()
            finally:
                finished.set()
                for current in list(webview.windows):
                    current.destroy()

        def on_closing():
            if finished.is_set():
                return True
            if closing.is_set():
                return False
            if not window.create_confirmation_dialog(APP_NAME, '确定退出文枢吗？请先保存未保存的修改。\n\n退出前会等待当前后台任务完成，尚未开始的任务将在下次打开时继续。如需提前停止自动写作，请先在写作页面中中断。'):
                return False
            closing.set()
            processes.stop()
            threading.Thread(target=finish_close, name='wenshu-shutdown', daemon=True).start()
            return False

        window.events.closing += on_closing
        threading.Thread(target=instance.listen, args=(window, finished), name='wenshu-activate', daemon=True).start()
        from webview.menu import Menu, MenuAction
        webview.start(gui='edgechromium', private_mode=False, storage_path=str(profile / 'webview'),
                      menu=[Menu('文枢', [MenuAction('写作运维', show_operations), MenuAction('退出', window.destroy)])],
                      localization={'global.quit': '退出', 'global.ok': '确定', 'global.cancel': '取消',
                                    'global.saveFile': '保存文件', 'global.openFile': '打开文件'})
        return 0
    except Exception as exc:
        logging.exception('Desktop startup or window failed')
        detail = str(exc) if isinstance(exc, DesktopStartupError) else '桌面窗口未能加载，请检查安装是否完整。'
        message('文枢未能正常启动。' + detail + '\n\n详细信息见文枢用户目录中的 logs 文件夹。', error=True)
        return 1
    finally:
        finished.set()
        if processes:
            processes.stop()
            processes.wait()
        if instance:
            instance.close()
