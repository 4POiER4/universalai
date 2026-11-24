#!/usr/bin/env python3
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1] / "static" / "messenger" / "stickers"
for path in root.glob("*.svg"):
    text = path.read_text(encoding="utf-8")
    text2 = re.sub(r'\sstyle="[^"]*"', "", text, count=1)
    if "background" not in text2[:220].lower():
        text2 = text2.replace("<svg ", '<svg style="background:transparent" ', 1)
    text2 = text2.replace(' clip-path="url(#__lottie_element_2)"', "")
    if text2 != text:
        path.write_text(text2, encoding="utf-8")
        print("cleaned", path.name)
    else:
        print("unchanged", path.name)
