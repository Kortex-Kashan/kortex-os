# PyInstaller spec for the KORTEX backend production/desktop entrypoint.
#
# --onedir, not --onefile (implementation_plan.md Part 2 SS D6): a
# --onefile build re-extracts its full ~150MB+ of native dependencies to a
# fresh temp directory on every single launch, which is both a startup-time
# regression and (per SS D9/D10) not the layout this spec's own resource
# resolution in desktop_entrypoint.py assumes.
#
# Run from the repository root:
#   .venv/Scripts/pyinstaller installer/pyinstaller/kortex_backend.spec
#
# Output: dist/kortex-backend/kortex-backend.exe (+ alembic.ini, alembic/,
# and all bundled dependencies as siblings in the same directory).

import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), "..", ".."))
BACKEND_SRC = os.path.join(REPO_ROOT, "backend", "src")
BACKEND_ALEMBIC_INI = os.path.join(REPO_ROOT, "backend", "alembic.ini")
BACKEND_ALEMBIC_DIR = os.path.join(REPO_ROOT, "backend", "alembic")

# rapidocr_onnxruntime loads config.yaml + models/*.onnx via a
# Path(__file__)-relative path *inside the third-party package itself*
# (implementation_plan.md Part 2 SS D5) -- collect_data_files preserves
# that exact package-relative layout automatically.
datas = []
datas += collect_data_files("rapidocr_onnxruntime")
datas += [(BACKEND_ALEMBIC_INI, ".")]
# alembic/__pycache__ and versions/__pycache__ are excluded implicitly --
# collect only the .py/.mako/.ini assets Alembic's ScriptDirectory needs.
for root, _dirs, files in os.walk(BACKEND_ALEMBIC_DIR):
    if "__pycache__" in root:
        continue
    rel_root = os.path.relpath(root, BACKEND_ALEMBIC_DIR)
    dest = "alembic" if rel_root == "." else os.path.join("alembic", rel_root)
    for f in files:
        if f.endswith(".pyc"):
            continue
        datas.append((os.path.join(root, f), dest))

# opencv's bundled ffmpeg DLL is loaded by cv2's own extension module at
# runtime, not imported as Python -- invisible to static analysis
# (implementation_plan.md Part 2 SS D5). collect_dynamic_libs finds it.
binaries = []
binaries += collect_dynamic_libs("cv2")
binaries += collect_dynamic_libs("onnxruntime")
binaries += collect_dynamic_libs("numpy")

# kortex.engines.document.adapters.{dummy_adapter,macro_adapter} are
# reachable only via pkgutil.iter_modules dynamic discovery
# (document/loader.py) -- never statically imported anywhere, so PyInstaller's
# static import-graph analysis cannot find them without this explicit
# declaration (implementation_plan.md Part 2 SS D5).
hiddenimports = [
    "kortex.engines.document.adapters.dummy_adapter",
    "kortex.engines.document.adapters.macro_adapter",
    # SQLAlchemy loads its DBAPI driver (aiosqlite) through its own dialect
    # plugin registry, not a direct `import aiosqlite` anywhere in this
    # codebase -- invisible to PyInstaller's static import-graph analysis.
    # Discovered by this milestone's own proof-of-concept build (a real
    # ModuleNotFoundError at migration time), not assumed in advance.
    "aiosqlite",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

a = Analysis(
    [os.path.join(BACKEND_SRC, "kortex", "api", "desktop_entrypoint.py")],
    pathex=[BACKEND_SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "chromadb", "ollama"],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="kortex-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="kortex-backend",
)
