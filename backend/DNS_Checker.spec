# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('templates', 'templates'), ('static', 'static')]
binaries = []
hiddenimports = [
    # psutil — native Windows extension; must be listed explicitly AND collected
    'psutil',
    'psutil._pswindows',
    'psutil._common',
    # flask ecosystem
    'flask',
    'flask_cors',
    'jinja2',
    'jinja2.ext',
    'werkzeug',
    'werkzeug.routing',
    'werkzeug.serving',
    'itsdangerous',
    'click',
    # requests + TLS stack
    'requests',
    'certifi',
    'charset_normalizer',
    'urllib3',
    'urllib3.util',
    'urllib3.util.retry',
    # dnspython submodules (many are imported dynamically)
    'dns',
    'dns.resolver',
    'dns.rdatatype',
    'dns.rdataclass',
    'dns.rdtypes',
    'dns.rdtypes.ANY',
    'dns.rdtypes.IN',
    'dns.name',
    'dns.message',
    'dns.query',
]

# collect_all bundles Python files, native extensions (.pyd), and data files.
# Listing a package in hiddenimports alone is NOT enough for native extensions.
tmp_ret = collect_all('flask')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

tmp_ret = collect_all('flask_cors')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# psutil: collect_all is required to bundle _psutil_windows.pyd
tmp_ret = collect_all('psutil')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# dnspython: collect_all ensures all dns.* submodules are included
tmp_ret = collect_all('dns')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# requests: collect_all pulls in certifi CA bundle and charset_normalizer
tmp_ret = collect_all('requests')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

tmp_ret = collect_all('certifi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DNS_Checker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    windowed=True,
    icon=['icon.ico'],
)
