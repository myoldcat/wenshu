"""Prepare pinned native PDF DLLs from the official WeasyPrint Windows build.

No system-wide installation or PATH changes occur. Only the PE dependency
closure of Pango/HarfBuzz/Fontconfig is extracted; the upstream executable,
Python runtime, extension modules, and network TLS libraries are not copied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import urllib.request
import zipfile


VERSION = '70.0'
ARCHIVE_URL = f'https://github.com/Kozea/WeasyPrint/releases/download/v{VERSION}/weasyprint-windows-onedir.zip'
ARCHIVE_SHA256 = 'ab1151f210b4e6bb7aa7a79e91a67e8ddb760094c107bfda55241b6aaefe7d53'
PREFIX = 'onedir/weasyprint/_internal/'
ROOT_DLLS = ('libpango-1.0-0.dll', 'libpangoft2-1.0-0.dll', 'libgobject-2.0-0.dll',
             'libfontconfig-1.dll', 'libharfbuzz-0.dll', 'libharfbuzz-subset-0.dll')
LICENSE_IDS = ('LGPL-2.1-only', 'LGPL-2.0-only', 'GPL-3.0-only', 'GCC-exception-3.1',
               'MIT', 'FTL', 'Zlib', 'Libpng', 'Unicode-3.0', 'BSD-3-Clause')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def dll_imports(data):
    """Read PE imports using the standard library, without executing DLLs."""
    if data[:2] != b'MZ':
        raise ValueError('Native runtime contains a non-PE library')
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('Invalid PE signature')
    machine, sections = struct.unpack_from('<HH', data, pe + 4)
    if machine != 0x8664:
        raise ValueError('The desktop runtime requires Windows x64 DLLs')
    optional_size = struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    if struct.unpack_from('<H', data, optional)[0] != 0x20B:
        raise ValueError('Expected a 64-bit PE optional header')
    import_rva, _ = struct.unpack_from('<II', data, optional + 112 + 8)
    regions = []
    for number in range(sections):
        offset = optional + optional_size + number * 40
        virtual_size, virtual_address, size, position = struct.unpack_from('<IIII', data, offset + 8)
        regions.append((virtual_address, max(virtual_size, size), position))

    def position(rva):
        for start, length, raw in regions:
            if start <= rva < start + length:
                return raw + rva - start
        raise ValueError('Invalid PE import address')

    if not import_rva:
        return []
    offset, imports = position(import_rva), []
    while any(data[offset:offset + 20]):
        name_rva = struct.unpack_from('<I', data, offset + 12)[0]
        start = position(name_rva)
        end = data.index(b'\0', start)
        imports.append(data[start:end].decode('ascii').lower())
        offset += 20
    return imports


def download(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.download')
    try:
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open('wb') as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(archive, destination, *, fetch_licenses=True):
    archive = Path(archive).resolve()
    digest = sha256(archive.read_bytes())
    if digest != ARCHIVE_SHA256:
        raise ValueError('Official WeasyPrint archive SHA-256 verification failed')
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        libraries = {PurePosixPath(name).name.lower(): name for name in package.namelist()
                     if name.startswith(PREFIX) and len(PurePosixPath(name).parts) == 4
                     and name.lower().endswith('.dll')}
        pending, selected, system_imports = list(ROOT_DLLS), {}, set()
        while pending:
            name = pending.pop()
            if name in selected:
                continue
            if name not in libraries:
                raise ValueError(f'Missing pinned native library: {name}')
            data = package.read(libraries[name])
            imports = dll_imports(data)
            selected[name] = data
            for dependency in imports:
                if dependency in libraries:
                    pending.append(dependency)
                else:
                    system_imports.add(dependency)
        output_files = []
        for name, data in sorted(selected.items()):
            (destination / name).write_bytes(data)
            output_files.append({'name': name, 'bytes': len(data), 'sha256': sha256(data)})
        licenses = destination / 'licenses'
        licenses.mkdir(exist_ok=True)
        (licenses / 'WeasyPrint-BSD-3-Clause.txt').write_bytes(package.read('LICENSE'))
    license_sources = []
    if fetch_licenses:
        for identifier in LICENSE_IDS:
            url = f'https://raw.githubusercontent.com/spdx/license-list-data/main/text/{identifier}.txt'
            target = licenses / f'{identifier}.txt'
            if not target.exists():
                download(url, target)
            license_sources.append({'id': identifier, 'url': url, 'sha256': sha256(target.read_bytes())})
    notices = '''Windows PDF native runtime
==========================
These unmodified, dynamically linked libraries are extracted from the official
WeasyPrint v70.0 Windows x64 release. Source and build recipes:
https://github.com/Kozea/WeasyPrint/tree/v70.0
https://github.com/msys2/MINGW-packages
https://packages.msys2.org/

Pango, GLib/GObject, FriBidi, libthai: LGPL-2.1-or-later (some GLib files also
carry LGPL-2.0-or-later terms); the LGPL license texts are provided here.
Fontconfig, Expat, HarfBuzz, libffi, libdatrie, libiconv/libintl and their
components retain their respective upstream copyright notices and licenses;
see the corresponding source package and MSYS2 build recipe.
FreeType: FreeType License (FTL), provided here; its use in this product does
not imply endorsement by The FreeType Project (https://freetype.org/).
libpng: libpng license; zlib and Brotli: their respective Zlib/MIT licenses.
GCC runtime libraries: GPL-3.0-or-later with GCC Runtime Library Exception 3.1.
Windows UCRT and API-set runtime files, when required by upstream DLL imports,
are redistributed unmodified as included in the upstream Windows release.

The DLLs are separate files under native/ and may be replaced with compatible
modified versions. No upstream native component was modified for this build.
The application does not restrict reverse engineering for debugging changes
to LGPL-covered libraries. Native source and complete component license texts
can be obtained from the corresponding source packages and recipes above.
'''
    (destination / 'THIRD-PARTY-NOTICES.txt').write_text(notices, encoding='utf-8')
    manifest = {'upstream': f'WeasyPrint {VERSION} official Windows x64 onedir',
                'url': ARCHIVE_URL, 'archiveSha256': digest, 'files': output_files,
                'systemImports': sorted(system_imports), 'licenseSources': license_sources}
    (destination / 'PROVENANCE.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=root / 'output/windows-native-cache/weasyprint-windows-onedir.zip')
    parser.add_argument('--destination', type=Path, default=root / 'output/windows-native')
    parser.add_argument('--skip-license-download', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.archive.exists():
        download(ARCHIVE_URL, args.archive)
    manifest = prepare(args.archive, args.destination, fetch_licenses=not args.skip_license_download)
    total = sum(item['bytes'] for item in manifest['files'])
    print(f"Prepared {len(manifest['files'])} verified native PDF DLLs ({total:,} bytes): {args.destination}")


if __name__ == '__main__':
    main()
