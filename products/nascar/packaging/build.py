"""Build a standalone Windows distribution without any F1 product modules."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def collect_licenses(folder: Path):
    licenses = folder / "THIRD_PARTY_LICENSES"
    licenses.mkdir(exist_ok=True)
    inventory = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"]
        inventory.append({"name":name,"version":distribution.version,"license":distribution.metadata.get("License-Expression") or distribution.metadata.get("License", "See package metadata")})
        for item in distribution.files or []:
            if item.name.lower().startswith(("license", "licence", "copying", "notice", "thirdpartynotice")):
                path = distribution.locate_file(item)
                if path.is_file():
                    # Preserve notices with the same basename from different
                    # bundled components instead of overwriting one another.
                    target = name.replace("/", "_") + "-" + "_".join(item.parts).replace(":", "_")
                    (licenses / target).write_bytes(path.read_bytes())
    # RapidOCR's wheel declares Apache-2.0 but omits a copy of the license.
    for path in (ROOT / "packaging/licenses").glob("*.txt"):
        (licenses / path.name).write_bytes(path.read_bytes())
    (licenses / "inventory.json").write_text(json.dumps(inventory,indent=2),encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", action="store_true")
    args = parser.parse_args()
    from PIL import Image, ImageDraw, ImageFont
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    image = Image.new("RGBA", (256,256), (0,0,0,0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0,0,255,255), radius=58, fill="#ffc15c")
    draw.text((52,-15), "P", font=ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf",220), fill="#0b1118")
    icon = build / "nascar.ico"
    image.save(icon, sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    command = [sys.executable,"-m","PyInstaller","--noconfirm","--windowed","--name","YourPitBox NASCAR",
               "--paths",str(ROOT/"src"),"--add-data",str(ROOT/"src/yourpitbox_nascar/static")+";yourpitbox_nascar/static",
               "--collect-all","rapidocr","--collect-all","onnxruntime","--icon",str(icon),"--specpath",str(build),str(ROOT/"packaging/entry.py")]
    subprocess.run(command,cwd=ROOT,check=True)
    folder = ROOT/"dist/YourPitBox NASCAR"
    (folder/"NOTICE.txt").write_text((ROOT/"NOTICE.txt").read_text(),encoding="utf-8")
    collect_licenses(folder)
    if args.installer:
        candidates=[Path.home()/"AppData/Local/Programs/Inno Setup 6/ISCC.exe",Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe")]
        compiler=next((x for x in candidates if x.exists()),None)
        if not compiler: raise RuntimeError("Install Inno Setup 6 to produce the installer")
        subprocess.run([str(compiler),"/DSourceDir="+str(folder),"/DOutputDir="+str(build/"artifacts"),str(ROOT/"packaging/nascar.iss")],check=True)


if __name__=="__main__": main()
