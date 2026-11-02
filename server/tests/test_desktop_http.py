"""Isolated desktop sessions protect both API data and static frontend files."""
from __future__ import annotations

from pathlib import Path
import secrets

from fastapi.testclient import TestClient
import pytest

from desktop.http_app import COOKIE_NAME, create_desktop_app
from desktop.service import coordinate_worker


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    data = tmp_path / "isolated-desktop-data"
    frontend = tmp_path / "client"
    control = tmp_path / "control"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>桌面文枢</html>", encoding="utf-8")
    (frontend / "operations.html").write_text("<html>运维</html>", encoding="utf-8")
    (frontend / "assets").mkdir()
    (frontend / "assets" / "app.js").write_text("console.log('local')", encoding="utf-8")
    (frontend / "assets" / "app.js.map").write_text("private source map", encoding="utf-8")
    (frontend / ".hidden").write_text("hidden", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("private outside content", encoding="utf-8")
    monkeypatch.setenv("WENSHU_DATA_DIR", str(data))
    monkeypatch.setenv("WENSHU_ALLOWED_HOSTS", "")
    monkeypatch.setenv("WENSHU_ALLOWED_ORIGINS", "")
    token = secrets.token_urlsafe(48)
    app = create_desktop_app(data, frontend, control, token)
    with TestClient(app, base_url="http://127.0.0.1:18787") as client:
        yield client, token, data, control


def login(client, token):
    response = client.get("/desktop/launch", params={"token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()
    assert token not in response.text
    return response


def test_ready_is_minimal_and_every_other_resource_requires_session(desktop):
    client, token, data, control = desktop
    ready = client.get("/desktop/ready")
    assert ready.json() == {"application": "wenshu-desktop", "status": "ready"}
    assert str(data) not in ready.text and token not in ready.text
    for path in ("/", "/operations.html", "/assets/app.js", "/api/health", "/api/documents",
                 "/api/operations/writing/projects", "/api/writing/projects/example/export"):
        assert client.get(path).status_code == 403
    client.cookies.set(COOKIE_NAME, "forged")
    assert client.get("/api/documents").status_code == 403
    client.cookies.clear()
    assert client.post("/desktop/stop", headers={"X-Wenshu-Client": "local"}).status_code == 403
    assert not (control / "stop").exists()


def test_one_use_login_serves_frontend_api_and_operations(desktop):
    client, token, data, control = desktop
    assert client.get("/desktop/launch?token=wrong").status_code == 403
    login(client, token)
    assert "桌面文枢" in client.get("/").text
    assert "运维" in client.get("/operations.html").text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/documents").status_code == 200
    assert client.get("/api/operations/writing/projects").status_code == 200
    assert client.get("/desktop/launch", params={"token": token}).status_code == 403
    assert client.get("/").headers["referrer-policy"] == "no-referrer"
    assert client.get("/").headers["cache-control"] == "no-store"


def test_desktop_host_origin_and_local_mutation_boundary(desktop):
    client, token, data, control = desktop
    for path in ("/desktop/ready", "/desktop/launch"):
        assert client.get(path, headers={"Host": "evil.example:18787"}).status_code == 403
        assert client.get(path, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.get(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    login(client, token)
    assert client.get("/api/documents", headers={"Host": "app.test:18787"}).status_code == 403
    assert client.get("/api/documents", headers={"Origin": "http://127.0.0.1:8787"}).status_code == 403
    assert client.post("/desktop/stop").status_code == 403
    assert not (control / "stop").exists()
    response = client.post("/desktop/stop", headers={"X-Wenshu-Client": "local", "Origin": "http://127.0.0.1:18787"})
    assert response.status_code == 200
    assert response.json() == {"status": "stopping"}
    assert (control / "stop").is_file()


def test_static_host_never_serves_source_maps_hidden_or_outside_files(desktop):
    client, token, data, control = desktop
    login(client, token)
    for path in ("/assets/app.js.map", "/.hidden", "/../outside.txt", "/%2e%2e/outside.txt",
                 "/%252e%252e/outside.txt", "/..%5coutside.txt", "/assets/../../outside.txt",
                 "/assets%5c..%5c..%5coutside.txt", "/C:/Windows/win.ini", "/missing.html"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert "private outside content" not in response.text


def test_import_and_snapshot_download_keep_session_and_fixed_bytes(desktop):
    client, token, data, control = desktop
    login(client, token)
    raw = ("<!doctype html><html><head><meta charset='utf-8'><title>桌面样本</title></head>"
           "<body><article><h1>桌面样本</h1><p>这是一篇独立编写的桌面安装验收资料。"
           "资料保留原始快照、结构块和来源版本，便于通过正文检索查看。"
           "本项测试仅处理这一份临时文档，不调用任何模型服务。</p></article></body></html>").encode("utf-8")
    response = client.post("/api/imports/files", headers={"X-Wenshu-Client": "local"},
                           files=[("files", ("desktop-sample.html", raw, "text/html"))])
    assert response.status_code == 202
    job_id = response.json()["jobs"][0]["id"]
    from server.worker import run_one
    store = client.app.state.store
    assert run_one(store)
    job = store.job(job_id)
    assert job["status"] == "succeeded"
    url = f"/api/documents/{job['source_id']}/original?revision_id={job['revision_id']}"
    downloaded = client.get(url)
    assert downloaded.status_code == 200
    assert downloaded.content == raw
    assert "attachment;" in downloaded.headers["content-disposition"]
    preview = client.get(f"/api/documents/{job['source_id']}/preview?revision_id={job['revision_id']}")
    assert preview.status_code == 200
    assert "sandbox;" in preview.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in preview.headers["content-security-policy"]
    assert preview.headers["x-frame-options"] == "SAMEORIGIN"
    assert client.get("/api/search", params={"q": "桌面安装验收"}).json()["total"] == 1
    client.cookies.clear()
    assert client.get(url).status_code == 403


def test_worker_stop_stops_claiming_before_draining_existing_work(tmp_path):
    states = []

    class Future:
        def done(self):
            return False

    class Runner:
        _futures = {Future(): object()}

        def tick(self):
            states.append("claim")
            (tmp_path / "stop").touch()
            return False

        def close(self):
            import json
            state = json.loads((tmp_path / "worker-state.json").read_text())
            assert state == {"status": "draining", "activeJobs": 1}
            states.append("drain")

    coordinate_worker(Runner(), tmp_path, sleep=lambda _: None)
    assert states == ["claim", "drain"]
    assert '"status": "stopped"' in (tmp_path / "worker-state.json").read_text()


def test_worker_does_not_claim_on_start_if_stop_already_requested(tmp_path):
    (tmp_path / "stop").touch()
    closed = []

    class Runner:
        _futures = {}

        def tick(self):
            raise AssertionError("Stop must prevent claiming queued tasks")

        def close(self):
            closed.append(True)

    coordinate_worker(Runner(), tmp_path)
    assert closed == [True]


def test_service_configures_explicit_isolated_paths_before_starting(tmp_path, monkeypatch):
    import os
    from desktop import runtime, service
    calls = []
    data, control, frontend = [tmp_path / name for name in ("data", "control", "frontend")]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "must-not-use-default-user-directory"))
    monkeypatch.setenv("WENSHU_DATA_DIR", str(tmp_path / "old-data"))
    monkeypatch.setenv("WENSHU_DESKTOP_CONTROL_DIR", str(tmp_path / "old-control"))

    def configure(path):
        assert path == control
        assert os.environ["WENSHU_DATA_DIR"] == str(data)
        assert os.environ["WENSHU_DESKTOP_CONTROL_DIR"] == str(control)
        calls.append("runtime")

    def api(data_dir, frontend_dir, control_dir, port):
        assert (data_dir, frontend_dir, control_dir, port) == (data, frontend, control, 18787)
        calls.append("api")

    monkeypatch.setattr(runtime, "configure_runtime", configure)
    monkeypatch.setattr(service, "run_api", api)
    assert service.main(["--api", "--data-dir", str(data), "--frontend-dir", str(frontend),
                         "--control-dir", str(control)]) == 0
    assert calls == ["runtime", "api"]
    assert not (tmp_path / "must-not-use-default-user-directory").exists()


def test_invalid_service_arguments_do_not_initialize_runtime(tmp_path, monkeypatch):
    from desktop import runtime, service
    monkeypatch.setattr(runtime, "configure_runtime", lambda *_: pytest.fail("Invalid CLI must not write runtime state"))
    with pytest.raises(SystemExit) as error:
        service.main(["--api"])
    assert error.value.code == 2


def fake_kernel(*, parent=202, wait=0):
    from types import SimpleNamespace
    from unittest.mock import Mock
    return SimpleNamespace(CreateMutexW=Mock(return_value=101), OpenProcess=Mock(return_value=parent),
                           WaitForSingleObject=Mock(return_value=wait), CloseHandle=Mock(return_value=True))


@pytest.mark.parametrize("wait_result", [0, 0xFFFFFFFF])
def test_parent_exit_or_failed_monitor_requests_stop_without_killing_jobs(tmp_path, monkeypatch, wait_result):
    import threading
    from desktop import http_app, service
    kernel = fake_kernel(wait=wait_result)
    stopped = threading.Event()
    mark_stop = http_app.request_stop

    def signal(path):
        mark_stop(path)
        stopped.set()

    monkeypatch.setattr(http_app, "request_stop", signal)
    with service.desktop_lifetime(tmp_path, parent_pid=345, windows=True, kernel=kernel):
        assert stopped.wait(2)
        assert (tmp_path / "stop").is_file()
        kernel.OpenProcess.assert_called_once_with(0x00100000, False, 345)
        kernel.WaitForSingleObject.assert_called_once_with(202, 250)
        kernel.CreateMutexW.assert_called_once_with(None, False, service.SERVICES_MUTEX)
        kernel.CloseHandle.assert_not_called()
    assert [call.args[0] for call in kernel.CloseHandle.call_args_list] == [202, 101]


@pytest.mark.parametrize("parent_pid", ["invalid", "0", "4294967296"])
def test_invalid_parent_identity_requests_stop(tmp_path, parent_pid):
    from desktop import service
    kernel = fake_kernel()
    with service.desktop_lifetime(tmp_path, parent_pid=parent_pid, windows=True, kernel=kernel):
        assert (tmp_path / "stop").is_file()
        kernel.OpenProcess.assert_not_called()
    kernel.CloseHandle.assert_called_once_with(101)


def test_unavailable_parent_handle_requests_stop_and_releases_service_mutex(tmp_path):
    from desktop import service
    kernel = fake_kernel(parent=None)
    with service.desktop_lifetime(tmp_path, parent_pid=987, windows=True, kernel=kernel):
        assert (tmp_path / "stop").is_file()
        kernel.WaitForSingleObject.assert_not_called()
        kernel.CloseHandle.assert_not_called()
    kernel.CloseHandle.assert_called_once_with(101)


def test_standalone_helper_holds_service_mutex_without_parent_monitor(tmp_path, monkeypatch):
    from desktop import service
    kernel = fake_kernel()
    monkeypatch.delenv("WENSHU_DESKTOP_PARENT_PID", raising=False)
    with pytest.raises(RuntimeError, match="fixture failure"):
        with service.desktop_lifetime(tmp_path, windows=True, kernel=kernel):
            assert not (tmp_path / "stop").exists()
            kernel.OpenProcess.assert_not_called()
            kernel.CloseHandle.assert_not_called()
            raise RuntimeError("fixture failure")
    kernel.CloseHandle.assert_called_once_with(101)


def test_parent_monitor_does_not_run_on_linux(tmp_path, monkeypatch):
    from desktop import service
    kernel = fake_kernel()
    monkeypatch.setenv("WENSHU_DESKTOP_PARENT_PID", "345")
    with service.desktop_lifetime(tmp_path, windows=False, kernel=kernel):
        assert not (tmp_path / "stop").exists()
    kernel.CreateMutexW.assert_not_called()
    kernel.OpenProcess.assert_not_called()


def test_pdf_service_never_holds_mutex_or_monitors_parent(tmp_path, monkeypatch):
    from desktop import runtime, service
    from server import writing_pdf
    calls = []
    monkeypatch.setenv("WENSHU_DESKTOP_PARENT_PID", "invalid")
    monkeypatch.setattr(runtime, "configure_runtime", lambda path: calls.append(("runtime", path)))
    monkeypatch.setattr(service, "desktop_lifetime", lambda *_: pytest.fail("PDF must not monitor the GUI"))
    monkeypatch.setattr(writing_pdf, "main", lambda: 23)
    assert service.main(["--pdf", "--control-dir", str(tmp_path)]) == 23
    assert calls == [("runtime", tmp_path)]


def test_lost_parent_before_start_prevents_runtime_and_job_recovery(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from desktop import runtime, service
    control = tmp_path / "control"

    @contextmanager
    def lost_parent(path):
        assert path == control
        (path / "stop").touch()
        yield

    monkeypatch.setattr(service, "desktop_lifetime", lost_parent)
    monkeypatch.setattr(runtime, "configure_runtime", lambda *_: pytest.fail("No need to initialize after parent loss"))
    monkeypatch.setattr(service, "run_worker", lambda *_: pytest.fail("Parent loss must not recover or claim jobs"))
    assert service.main(["--worker", "--data-dir", str(tmp_path / "data"), "--control-dir", str(control)]) == 0
