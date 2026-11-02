"""Paths and native libraries shared by the Windows desktop processes.

Only a caller-selected writable control directory receives generated files.
Installed application resources and the manuscript database are never used as
font caches. Import this module before importing WeasyPrint in a child process.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from xml.sax.saxutils import escape


_DLL_HANDLES = []


def bundle_root() -> Path:
    """Return PyInstaller's resource directory, or the repository root."""
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])).resolve()


def service_binary() -> Path:
    """The console-capable helper sits beside the windowed desktop launcher."""
    return Path(sys.executable).resolve().parent / 'WenshuService.exe'


def pdf_command() -> list[str]:
    if getattr(sys, 'frozen', False):
        return [str(service_binary()), '--pdf']
    return [sys.executable, '-m', 'server.writing_pdf']


def configure_runtime(control_dir=None, *, resource_root=None, windows=None) -> dict[str, str]:
    """Configure bundled Pango and fonts without touching global settings.

    ``windows`` is injectable for path/configuration tests on other hosts.
    Returned paths aid packaging diagnostics; no model or user data is read.
    """
    is_windows = os.name == 'nt' if windows is None else windows
    if not is_windows:
        return {}
    root = Path(resource_root).resolve() if resource_root is not None else bundle_root()
    native = root / 'native'
    if not native.is_dir() and not getattr(sys, 'frozen', False):
        native = root / 'output' / 'windows-native'
    fonts = root / 'fonts'
    if not fonts.is_dir() and not getattr(sys, 'frozen', False):
        fonts = root / 'deploy' / 'server' / 'fonts'
    control = Path(control_dir or os.environ.get('WENSHU_DESKTOP_CONTROL_DIR') or
                   Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Wenshu' / 'run').resolve()
    cache = control / 'runtime-cache'
    cache.mkdir(parents=True, exist_ok=True)
    font_cache = cache / 'fontconfig'
    font_cache.mkdir(exist_ok=True)
    windows_fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    font_dirs = [directory for directory in (fonts, windows_fonts) if directory.is_dir()]
    config = cache / 'fonts.conf'
    # Deliberately omit user's fontconfig includes and environment substitutions.
    # Fallback aliases cover the standard serif/sans-serif names in the export.
    body = ['<?xml version="1.0"?>', '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">', '<fontconfig>']
    body.extend(f'  <dir>{escape(directory.as_posix())}</dir>' for directory in font_dirs)
    body.extend([
        f'  <cachedir>{escape(font_cache.as_posix())}</cachedir>',
        '  <alias><family>serif</family><prefer><family>FandolSong</family></prefer></alias>',
        '  <alias><family>sans-serif</family><prefer><family>FandolHei</family></prefer></alias>',
        '</fontconfig>', '',
    ])
    text = '\n'.join(body)
    if not config.exists() or config.read_text(encoding='utf-8') != text:
        # Each process has the same deterministic contents. Atomic replace avoids
        # a PDF child observing a partially written configuration at startup.
        temporary = config.with_name(f'fonts-{os.getpid()}.tmp')
        temporary.write_text(text, encoding='utf-8')
        os.replace(temporary, config)
    os.environ['FONTCONFIG_FILE'] = str(config)
    os.environ['FONTCONFIG_PATH'] = str(cache)
    os.environ['XDG_CACHE_HOME'] = str(cache)
    if native.is_dir():
        os.environ['WEASYPRINT_DLL_DIRECTORIES'] = str(native)
        entries = os.environ.get('PATH', '').split(os.pathsep)
        if str(native) not in entries:
            os.environ['PATH'] = os.pathsep.join([str(native), *entries])
        if hasattr(os, 'add_dll_directory'):
            # Keep handles alive: closing one removes the DLL search directory.
            _DLL_HANDLES.append(os.add_dll_directory(str(native)))
    return {'native': str(native), 'fonts': str(fonts), 'fontconfig': str(config), 'cache': str(cache)}
