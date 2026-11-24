import re
from pathlib import Path

text = Path(r"D:\UniverslaAI\static\messenger\js\rlottie-wasm.js").read_text(encoding="utf-8", errors="ignore")
print("RlottieWasm count", text.count("RlottieWasm"))
print("Rlottie count", text.count("Rlottie"))
# method names often appear as Ascii strings for embind
for needle in ("load", "frames", "render", "frameCount", "totalFrame", "framesCount", "size"):
    print(needle, text.count(f'"{needle}"'), text.count(f"'{needle}'"))
# dump nearby unique short ascii strings containing frame/load/render
found = sorted(set(re.findall(r'"([A-Za-z][A-Za-z0-9_]{2,40})"', text)))
keep = [x for x in found if re.search(r"frame|load|render|lottie|size|width|height|play", x, re.I)]
print("interesting:", keep)
