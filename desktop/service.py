"""Hidden child-process entry point for API, durable worker and PDF export."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import threading
import time


SERVICES_MUTEX = r"Local\Wenshu.Desktop.Services"


@contextmanager
def desktop_lifetime(control_dir: Path, *, parent_pid=None, windows=None, kernel=None):
    """Keep draining services visible to Setup and observe the launcher's life.

    A process handle refers to the original parent process even after Windows
    reuses its PID. Parent loss requests the same graceful stop as window exit;
    no in-flight job is killed. Standalone helpers without a parent PID remain
    usable for isolated acceptance checks.
    """
    if not (os.name == "nt" if windows is None else windows):
        yield
        return
    kernel = kernel or ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # No ownership: every API/worker holds its own reference, and Setup can
    # observe the object until the last draining service has really exited.
    mutex = kernel.CreateMutexW(None, False, SERVICES_MUTEX)
    if not mutex:
        raise RuntimeError("无法创建文枢后台服务运行锁")
    parent = None
    watcher = None
    finished = threading.Event()

    def stop():
        from desktop.http_app import request_stop
        request_stop(control_dir)

    try:
        selected_pid = os.environ.get("WENSHU_DESKTOP_PARENT_PID") if parent_pid is None else parent_pid
        if selected_pid not in (None, ""):
            try:
                selected_pid = int(selected_pid)
                if not 1 <= selected_pid <= 0xFFFFFFFF:
                    raise ValueError()
            except (TypeError, ValueError, OverflowError):
                stop()
            else:
                parent = kernel.OpenProcess(0x00100000, False, selected_pid)  # SYNCHRONIZE only
                if not parent:
                    stop()
                else:
                    def watch_parent():
                        while not finished.is_set():
                            result = kernel.WaitForSingleObject(parent, 250)
                            if result != 0x00000102:  # WAIT_TIMEOUT
                                if not finished.is_set():
                                    # Both process exit and failed monitoring
                                    # must stop the unattended helper safely.
                                    stop()
                                return

                    watcher = threading.Thread(target=watch_parent, name="wenshu-parent-watch", daemon=True)
                    watcher.start()
        yield
    finally:
        finished.set()
        if watcher is not None:
            watcher.join(timeout=1)
        if parent:
            kernel.CloseHandle(parent)
        kernel.CloseHandle(mutex)


def _write_state(path: Path, status: str, active_jobs: int = 0) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"status": status, "activeJobs": active_jobs}), encoding="utf-8")
    temporary.replace(path)


@contextmanager
def worker_lock(data_dir: Path):
    lock = (data_dir / "worker.lock").open("a+b")
    try:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("文枢后台服务已在运行") from None
        yield
    finally:
        lock.close()


def coordinate_worker(runner, control_dir: Path, *, sleep=time.sleep):
    """Stop claiming before waiting for existing jobs to finish."""
    stop_file, state_file = control_dir / "stop", control_dir / "worker-state.json"
    _write_state(state_file, "ready")
    try:
        while not stop_file.exists():
            worked = runner.tick()
            _write_state(state_file, "running", sum(not future.done() for future in runner._futures))
            if not worked:
                sleep(0.5)
    finally:
        _write_state(state_file, "draining", sum(not future.done() for future in runner._futures))
        runner.close()
        _write_state(state_file, "stopped")


def run_worker(data_dir: Path, control_dir: Path, writing_concurrency: int = 3):
    from server.store import Store
    from server.worker import ConcurrentWorker
    store = Store(data_dir)
    control_dir.mkdir(parents=True, exist_ok=True)
    with worker_lock(store.root):
        store.recover()
        runner = ConcurrentWorker(store, writing_limit=writing_concurrency)
        coordinate_worker(runner, control_dir)


def run_api(data_dir: Path, frontend_dir: Path, control_dir: Path, port: int):
    import uvicorn
    from desktop.http_app import create_desktop_app
    token = os.environ.get("WENSHU_DESKTOP_TOKEN", "")
    app = create_desktop_app(data_dir, frontend_dir, control_dir, token, port=port)
    # Launch URLs contain a one-use token. Disable request access logs entirely.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False,
                                         log_level="warning", log_config=None))
    finished = threading.Event()

    def watch_stop():
        while not finished.wait(0.2):
            if (control_dir / "stop").exists():
                server.should_exit = True
                return

    watcher = threading.Thread(target=watch_stop, name="wenshu-api-stop", daemon=True)
    watcher.start()
    try:
        server.run()
    finally:
        finished.set()
        watcher.join(timeout=1)


def parser():
    cli = argparse.ArgumentParser(description="文枢桌面后台服务")
    role = cli.add_mutually_exclusive_group(required=True)
    role.add_argument("--api", action="store_true")
    role.add_argument("--worker", action="store_true")
    role.add_argument("--pdf", action="store_true")
    cli.add_argument("--data-dir", type=Path)
    cli.add_argument("--frontend-dir", type=Path)
    cli.add_argument("--control-dir", type=Path)
    cli.add_argument("--port", type=int, default=18787)
    cli.add_argument("--writing-concurrency", type=int, default=3)
    return cli


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    os.environ["PYTHONUTF8"] = "1"
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if not args.pdf and (args.data_dir is None or args.control_dir is None):
        cli.error("须提供 --data-dir 和 --control-dir")
    if not 1024 <= args.port <= 65535 or args.writing_concurrency < 1:
        cli.error("服务端口或并发设置无效")
    if args.api and args.frontend_dir is None:
        cli.error("API 须提供 --frontend-dir")
    if args.control_dir is not None:
        args.control_dir = args.control_dir.resolve()
        os.environ["WENSHU_DESKTOP_CONTROL_DIR"] = str(args.control_dir)
    if args.data_dir is not None:
        os.environ["WENSHU_DATA_DIR"] = str(args.data_dir.resolve())
    # Configure DLL/font paths only after the explicitly selected data and
    # control paths are known. PDF children inherit the same control directory.
    from desktop.runtime import configure_runtime
    if args.pdf:
        configure_runtime(args.control_dir)
        from server.writing_pdf import main as pdf_main
        return pdf_main()
    data_dir, control_dir = args.data_dir.resolve(), args.control_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    control_dir.mkdir(parents=True, exist_ok=True)
    with desktop_lifetime(control_dir):
        if (control_dir / "stop").exists():
            return 0
        configure_runtime(control_dir)
        if (control_dir / "stop").exists():
            return 0
        if args.worker:
            run_worker(data_dir, control_dir, args.writing_concurrency)
        else:
            run_api(data_dir, args.frontend_dir.resolve(), control_dir, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
