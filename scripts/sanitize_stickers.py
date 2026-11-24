#!/usr/bin/env python3
"""DEPRECATED — do not run on production sticker packs.

Removing track-matte layers exposes underlying sculpt colors (often pink/red)
and breaks Telegram sticker hues. Kept only for experiments.
"""

raise SystemExit("Refusing to sanitize: this breaks sticker colors. Use raw TGS import only.")

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STICKERS = ROOT / "static" / "messenger" / "stickers"


def sanitize(data: dict) -> dict:
    layers = list(data.get("layers") or [])
    if not layers:
        return data

    # Bodymovin: td marks a matte source; tt marks the destination that uses it.
    drop: set[int] = set()
    for index, layer in enumerate(layers):
        if layer.get("td") in (1, 2):
            drop.add(index)
            if index > 0 and layers[index - 1].get("tt") in (1, 2):
                drop.add(index - 1)
        if layer.get("tt") in (1, 2):
            drop.add(index)

    if not drop:
        return data

    cleaned = [layer for index, layer in enumerate(layers) if index not in drop]
    data = dict(data)
    data["layers"] = cleaned
    return data


def main() -> None:
    count = 0
    for path in sorted(STICKERS.glob("*.json")):
        if path.name == "pack.json":
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        before = len(raw.get("layers") or [])
        fixed = sanitize(raw)
        after = len(fixed.get("layers") or [])
        path.write_text(
            json.dumps(fixed, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        count += 1
        print(f"{path.name}: layers {before} -> {after}")
    print(f"sanitized {count} stickers")


if __name__ == "__main__":
    main()
