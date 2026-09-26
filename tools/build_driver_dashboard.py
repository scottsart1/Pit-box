"""Build the public dashboard's self-contained offline HTML and PWA icons."""
from pathlib import Path
import base64
import re
import struct
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[1] / 'static' / 'driver-dashboard'

def write_generated(path: Path, content: bytes) -> None:
    """Avoid rewriting unchanged assets; publish complete files to readers."""
    if path.exists() and path.read_bytes() == content:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()

def build():
    modules = []
    for name in ('display.mjs', 'model.mjs', 'render.mjs', 'preferences.mjs', 'dashboard.js'):
        js = (ROOT / name).read_text(encoding='utf-8')
        js = re.sub(r'^import .*?;\s*', '', js, flags=re.M)
        js = re.sub(r'^export ', '', js, flags=re.M)
        if name == 'render.mjs':
            js = 'const e=escapeHTML;\n' + js
        modules.append(js)
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    html = html.replace('<link rel="stylesheet" href="dashboard.css">', '<style>' + (ROOT / 'dashboard.css').read_text(encoding='utf-8') + '</style>')
    html = html.replace('<script type="module" src="dashboard.js"></script>', '<script>\n(()=>{\n' + '\n'.join(modules) + '\n})();\n</script>')
    html = html.replace('<link rel="manifest" href="manifest.webmanifest">', '')
    icon = base64.b64encode((ROOT / 'icon.svg').read_bytes()).decode('ascii')
    html = html.replace('href="icon.svg"', 'href="data:image/svg+xml;base64,' + icon + '"')
    html = html.replace('<a href="downloads/driver-dashboard.html" download="YourPitBox-Driver-Dashboard.html">Download offline demo</a>', '<span>Offline demo · saved on your device</span>')
    (ROOT / 'downloads').mkdir(exist_ok=True)
    write_generated(ROOT / 'downloads' / 'driver-dashboard.html', html.encode('utf-8'))
    # Native vector logo rasterized geometrically; no external assets or fonts.
    for size in (192, 512):
        pixels = bytearray()
        for row in range(size):
            pixels.append(0)
            y = row * 512 / size
            for col in range(size):
                x = col * 512 / size + (y-128)*.325
                inside = any(left <= x < left+52 and top <= y < 384 for left,top in ((155,128),(235,128),(315,218)))
                pixels.extend((63,134,255) if inside else (7,11,15))
        def chunk(kind, payload):
            return struct.pack('!I',len(payload))+kind+payload+struct.pack('!I',zlib.crc32(kind+payload))
        png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR',struct.pack('!2I5B',size,size,8,2,0,0,0)) + chunk(b'IDAT',zlib.compress(pixels)) + chunk(b'IEND',b'')
        write_generated(ROOT / f'icon-{size}.png', png)
    print('Built offline HTML and 192/512 px PWA icons.')

if __name__ == '__main__':
    build()
