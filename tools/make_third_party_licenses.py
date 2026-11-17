"""Write THIRD_PARTY_LICENSES.txt: the licenses of the open-source software bundled in the app.

    .venv/bin/python tools/make_third_party_licenses.py

Run it after changing requirements.txt (the build script runs it too). The texts come from the installed
packages, so the file always matches what PyInstaller bundles. It is copied into the app
(Contents/Resources/THIRD_PARTY_LICENSES.txt) and linked from About and the README.
"""

from __future__ import annotations

import importlib.metadata as md
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "THIRD_PARTY_LICENSES.txt"
RULE = "=" * 78

MIT = """Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO
EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR
THE USE OR OTHER DEALINGS IN THE SOFTWARE."""

# (distribution, what it does in the app, license name, source URL, copyright line if the package ships no
# license file, file inside the dist-info to use)
PACKAGES = [
    ("mutagen", "reads tags and cover art", "GNU General Public License v2 or later", "https://github.com/quodlibet/mutagen",
     None, "COPYING"),
    ("pywebview", "the app window", "BSD 3-Clause", "https://github.com/r0x0r/pywebview", None, "licenses/LICENSE"),
    ("bottle", "used by pywebview", "MIT", "https://github.com/bottlepy/bottle", None, "licenses/LICENSE"),
    ("proxy_tools", "used by pywebview", "MIT", "https://github.com/jtushman/proxy_tools",
     "Copyright (c) Jonathan Tushman", None),
    ("pyobjc-core", "access to macOS; also covers the pyobjc-framework-* packages", "MIT", "https://github.com/ronaldoussoren/pyobjc",
     "Copyright (c) 2002-2025 Ronald Oussoren, Bill Bumgarner, Steve Majewski, Lele Gaifax and the PyObjC contributors", None),
    ("typing_extensions", "Python support library", "Python Software Foundation License", "https://github.com/python/typing_extensions",
     None, "licenses/LICENSE"),
]


def dist_text(name: str, rel: str) -> str:
    dist = md.distribution(name)
    path = Path(dist._path) / rel  # noqa: SLF001 - the dist-info folder
    return path.read_text(encoding="utf-8").strip()


def section(title: str, body: str) -> str:
    return f"{RULE}\n{title}\n{RULE}\n\n{body.strip()}\n"


def main() -> None:
    parts = [
        "BAKKIS MUSIC SORTER includes the open-source software listed below. Each part is used under its own\n"
        "license, reproduced in full. BAKKIS MUSIC SORTER itself is MIT licensed (see LICENSE); its complete\n"
        "source code is at https://github.com/andreasbakketeig-hub/BAKKIS-MUSIC-SORTER.\n\n"
        "mutagen is licensed under the GNU GPL v2 or later. Its source code is available at the address below\n"
        "and on PyPI (https://pypi.org/project/mutagen/); the app uses it unmodified. On request we will also\n"
        "send the exact source of every bundled component: bakkis@bakkis.com.\n\n"
        "BAKKIS MUSIC SORTER is not affiliated with or endorsed by the authors of these projects.\n"
    ]
    for name, role, lic, url, copyright_line, rel in PACKAGES:
        version = md.version(name)
        head = f"{name} {version} ({role})\nLicense: {lic}\nSource: {url}"
        body = dist_text(name, rel) if rel else f"{copyright_line}\n\n{MIT}"
        parts.append(section(head, body))

    py = Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"
    parts.append(section(
        f"Python {sys.version.split()[0]} (the runtime the app is built on)\nLicense: Python Software Foundation License\n"
        "Source: https://www.python.org", py.read_text(encoding="utf-8")))

    parts.append(section(
        "PyInstaller bootloader (starts the app)\nLicense: GNU GPL v2 or later with the Bootloader Exception\n"
        "Source: https://github.com/pyinstaller/pyinstaller",
        "Copyright (c) 2010-2023, PyInstaller Development Team. Copyright (c) 2005-2009, Giovanni Bajo.\n\n"
        "The Bootloader Exception gives unlimited permission to link or embed the compiled bootloader and related\n"
        "files into combinations with other programs, and to distribute those combinations without any\n"
        "restriction coming from the use of those files."))

    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {OUT.name} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
