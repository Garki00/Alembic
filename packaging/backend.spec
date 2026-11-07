# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 规格：把 FastAPI 后端打成可独立运行的程序。

构建（在仓库根目录）：

    .\\.venv\\Scripts\\python.exe -m PyInstaller packaging\\backend.spec --noconfirm --clean

产物：

    dist/smart-agent-backend/smart-agent-backend.exe

采用 onedir（而不是 onefile）：启动快，且 Electron 可以直接把整个目录放进
resources，不用每次解压到临时目录。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

PROJECT = Path(SPECPATH).resolve().parent  # spec 在 packaging/ 下

# 前后端分离后，web/static 已移除：前端由 frontend/ 单独构建。
# 后端只提供 /api，因此这里只带运行时数据。
datas = []
# Windows 不自带 IANA 时区库，zoneinfo 依赖 tzdata 的数据文件
datas += collect_data_files("tzdata")
# python-docx 读取时不一定用到模板，但带上可避免打包后创建文档失败
datas += collect_data_files("docx")

hiddenimports = [
    # uvicorn 的实现模块是运行时按字符串导入的，PyInstaller 静态分析看不到
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # 两家模型协议在运行时可切换，显式带上
    "langchain_deepseek",
    "langchain_openai",
    # 文档工具在函数内延迟导入，静态分析看不到
    "pypdf",
    "docx",
    "openpyxl",
    "pptx",
    "et_xmlfile",
]

a = Analysis(
    [str(PROJECT / "run_backend.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="smart-agent-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 保留控制台：直接双击运行时能看到端口与日志
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="smart-agent-backend",
)
