"""Desktop PDF isolation and native runtime tests; no database or model calls."""
import importlib.util
import io
import os
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from desktop import runtime
from server import writing_pdf


@pytest.fixture(autouse=True)
def isolated_pdf_temporary_directory(tmp_path, monkeypatch):
    directory = tmp_path / 'pdf-temporary'
    directory.mkdir()
    monkeypatch.setattr(writing_pdf.tempfile, 'tempdir', str(directory))


def test_runtime_only_writes_cache_in_control_directory(tmp_path, monkeypatch):
    resources = tmp_path / 'installed & read-only resources'
    native = resources / 'native'
    native.mkdir(parents=True)
    (resources / 'fonts').mkdir()
    windows = tmp_path / 'Windows'
    (windows / 'Fonts').mkdir(parents=True)
    monkeypatch.setenv('WINDIR', str(windows))
    monkeypatch.setattr(runtime.os, 'add_dll_directory', lambda directory: directory, raising=False)
    for name in ('PATH', 'FONTCONFIG_FILE', 'FONTCONFIG_PATH', 'XDG_CACHE_HOME', 'WEASYPRINT_DLL_DIRECTORIES'):
        monkeypatch.setenv(name, '')
    before = sorted(path.relative_to(resources) for path in resources.rglob('*'))
    control = tmp_path / 'per-user run'
    result = runtime.configure_runtime(control, resource_root=resources, windows=True)
    document = ET.parse(result['fontconfig']).getroot()
    assert document.tag == 'fontconfig'
    assert [element.text for element in document.findall('dir')] == [
        (resources / 'fonts').as_posix(), (windows / 'Fonts').as_posix()]
    assert document.find('cachedir').text == (control / 'runtime-cache/fontconfig').as_posix()
    assert document.find('include') is None
    assert os.environ['WEASYPRINT_DLL_DIRECTORIES'] == str(native)
    assert sorted(path.relative_to(resources) for path in resources.rglob('*')) == before
    initial = Path(result['fontconfig']).stat().st_mtime_ns
    runtime.configure_runtime(control, resource_root=resources, windows=True)
    assert Path(result['fontconfig']).stat().st_mtime_ns == initial
    assert os.environ['PATH'].split(os.pathsep).count(str(native)) == 1


def test_non_windows_runtime_does_not_mutate_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('FONTCONFIG_FILE', 'unchanged')
    assert runtime.configure_runtime(tmp_path / 'control', windows=False) == {}
    assert not (tmp_path / 'control').exists()
    assert os.environ['FONTCONFIG_FILE'] == 'unchanged'


def test_frozen_pdf_uses_console_helper_beside_gui(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(runtime.sys, 'executable', str(tmp_path / 'Wenshu.exe'))
    assert runtime.pdf_command() == [str(tmp_path / 'WenshuService.exe'), '--pdf']


def test_source_pdf_uses_python_module(monkeypatch):
    monkeypatch.delattr(runtime.sys, 'frozen', raising=False)
    assert runtime.pdf_command() == [runtime.sys.executable, '-m', 'server.writing_pdf']


def test_pdf_spawn_preserves_binary_stdin_stdout_and_private_error_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(writing_pdf.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(writing_pdf.sys, 'executable', str(tmp_path / 'WenshuService.exe'))
    monkeypatch.setattr(writing_pdf, 'build_pdf_html', lambda *_: '<!doctype html><p>中文测试</p>')
    monkeypatch.setattr(writing_pdf, '_frozen_image_resources', lambda *_: {})
    output = b'%PDF-1.7\nsynthetic binary\n%%EOF'

    def child(command, **options):
        assert command == [str(tmp_path / 'WenshuService.exe'), '--pdf']
        assert options['input'] == '<!doctype html><p>中文测试</p>'.encode()
        assert options['stdout'] == writing_pdf.subprocess.PIPE
        assert options['stderr'] == writing_pdf.subprocess.DEVNULL
        assert options['env']['PYTHONIOENCODING'] == 'utf-8'
        assert options['timeout'] == writing_pdf.PDF_TIMEOUT_SECONDS
        assert options['check'] is False
        if os.name == 'nt':
            assert options['creationflags'] == writing_pdf.subprocess.CREATE_NO_WINDOW
        assert 'shell' not in options and 'text' not in options
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(writing_pdf.subprocess, 'run', child)
    assert writing_pdf.render_pdf({}, {}) == output


def test_pdf_timeout_releases_slot_and_never_exposes_child_output(monkeypatch):
    monkeypatch.setattr(writing_pdf, 'build_pdf_html', lambda *_: '<p>private manuscript</p>')
    monkeypatch.setattr(writing_pdf, '_frozen_image_resources', lambda *_: {})

    def timeout(command, **options):
        raise writing_pdf.subprocess.TimeoutExpired(command, options['timeout'], output=b'private manuscript')

    monkeypatch.setattr(writing_pdf.subprocess, 'run', timeout)
    with pytest.raises(writing_pdf.PDFExportError) as caught:
        writing_pdf.render_pdf({}, {})
    assert caught.value.detail['code'] == 'pdf_timeout'
    assert 'private' not in str(caught.value)
    assert writing_pdf._PDF_SLOTS.acquire(blocking=False)
    writing_pdf._PDF_SLOTS.release()


def test_pdf_main_returns_only_binary_pdf_on_stdout(monkeypatch):
    stdout = io.BytesIO()
    stderr = io.StringIO()
    monkeypatch.setattr(writing_pdf.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO('<p>中文</p>'.encode())))
    monkeypatch.setattr(writing_pdf.sys, 'stdout', SimpleNamespace(buffer=stdout))
    monkeypatch.setattr(writing_pdf.sys, 'stderr', stderr)
    monkeypatch.setattr(writing_pdf, '_render_in_child', lambda *_: b'%PDF-1.7\n%%EOF')
    monkeypatch.setattr(writing_pdf, '_process_limits', lambda: None)
    assert writing_pdf.main() == 0
    assert stdout.getvalue() == b'%PDF-1.7\n%%EOF'
    assert not stderr.getvalue()


@pytest.mark.skipif(os.name != 'nt' or not importlib.util.find_spec('weasyprint') or
                    not importlib.util.find_spec('pypdf'), reason='Requires the Windows build environment')
def test_real_windows_pdf_preserves_chinese_text_through_subprocess(tmp_path, monkeypatch):
    from pypdf import PdfReader

    root = Path(__file__).resolve().parents[2]
    if not (root / 'output/windows-native/libpango-1.0-0.dll').is_file():
        pytest.skip('Run prepare-windows-native.py before the native smoke test')
    control = tmp_path / '中文运行目录'
    monkeypatch.setenv('WENSHU_DESKTOP_CONTROL_DIR', str(control))
    runtime.configure_runtime(control, resource_root=root)
    source = ('<!doctype html><meta charset="utf-8"><style>body{font-family:FandolSong;'
              'font-size:16pt}</style><h1>文枢单机版</h1><p>中文排版验证，保留标点与原文。</p>')
    monkeypatch.setattr(writing_pdf, 'build_pdf_html', lambda *_: source)
    monkeypatch.setattr(writing_pdf, '_frozen_image_resources', lambda *_: {})
    content = writing_pdf.render_pdf({}, {})
    reader = PdfReader(io.BytesIO(content), strict=True)
    extracted = ''.join(page.extract_text() or '' for page in reader.pages)
    assert '文枢单机版' in extracted
    assert '中文排版验证，保留标点与原文。' in extracted
    assert '\ufffd' not in extracted and not reader.is_encrypted
