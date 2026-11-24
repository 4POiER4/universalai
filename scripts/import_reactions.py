#!/usr/bin/env python3
"""Import messenger reaction assets from ./reactions.

New layout (preferred):
  reactions/manifest.json
  reactions/001_❤/static_icon.webp
  reactions/001_❤/select_animation.tgs
  ...

Legacy layout (fallback):
  reactions/*.tgs          -> static/messenger/reactions/
  reactions/emoji/*.tgs    -> static/messenger/stickers/
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "reactions"
EMOJI_SRC = SRC / "emoji"
REACTIONS_DST = ROOT / "static" / "messenger" / "reactions"
STICKERS_DST = ROOT / "static" / "messenger" / "stickers"
IMAGE_EXT = {".svg", ".webp", ".png", ".gif", ".json"}


def is_gzip(data: bytes) -> bool:
    return len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B


def looks_like_html_error(data: bytes) -> bool:
    head = data[:120].lstrip().lower()
    if head.startswith(b"<svg") or head.startswith(b"<?xml"):
        return False
    return (
        head.startswith(b"<br")
        or head.startswith(b"<html")
        or head.startswith(b"<!doctype html")
        or b"<b>warning</b>" in head
        or b"open_basedir" in head
    )


def source_number(stem: str) -> str | None:
    match = re.match(r"^(\d{1,4})", stem)
    if match:
        return match.group(1)
    match = re.search(r"(\d{2,4})$", stem)
    if match:
        return match.group(1)
    match = re.search(r"_(\d{2,4})", stem)
    return match.group(1) if match else None


def clear_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for old in path.iterdir():
        if old.name in {".gitkeep", "pack.json"}:
            continue
        if old.is_file():
            old.unlink()
        elif old.is_dir():
            shutil.rmtree(old)


def safe_print(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        print(message.encode("ascii", "backslashreplace").decode("ascii"))


def parse_manifest_rel_path(rel: str) -> tuple[str, str]:
    parts = Path(str(rel).replace("\\", "/")).parts
    if "reactions" in parts:
        idx = parts.index("reactions")
        return parts[idx + 1], parts[-1]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", parts[-1] if parts else ""


def import_tgs(src: Path, dst: Path) -> bool:
    data = src.read_bytes()
    if looks_like_html_error(data):
        print(f"skip broken download: {src.name}")
        return False
    if not is_gzip(data):
        print(f"skip invalid tgs: {src.name}")
        return False
    try:
        payload = gzip.decompress(data)
        json.loads(payload)
    except Exception as error:  # noqa: BLE001
        print(f"skip bad tgs {src.name}: {error}")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(payload)
    return True


def import_manifest_reactions() -> int:
    manifest_path = SRC / "manifest.json"
    if not manifest_path.is_file():
        return 0

    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        return 0

    REACTIONS_DST.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    imported = 0

    for entry in raw:
        emoji = str(entry.get("emoji") or "").strip()
        title = str(entry.get("title") or emoji or "Reaction").strip()
        files = entry.get("files") or {}
        if not emoji or not isinstance(files, dict):
            continue

        folder = ""
        out_files: dict[str, str] = {}
        for key, rel in files.items():
            rel_folder, filename = parse_manifest_rel_path(str(rel))
            folder = folder or rel_folder
            src_file = SRC / rel_folder / filename
            if not src_file.is_file():
                src_file = ROOT / Path(str(rel).replace("\\", "/").lstrip("./"))
            if not src_file.is_file():
                print(f"skip missing {key}: {rel}")
                continue

            dst_folder = REACTIONS_DST / folder
            dst_folder.mkdir(parents=True, exist_ok=True)
            if src_file.suffix.lower() == ".tgs":
                out_name = f"{src_file.stem}.json"
                dst_file = dst_folder / out_name
                if import_tgs(src_file, dst_file):
                    out_files[key] = f"{folder}/{out_name}"
                    imported += 1
                    safe_print(f"imported tgs -> {dst_file.relative_to(ROOT).as_posix()}")
            else:
                dst_file = dst_folder / filename
                shutil.copy2(src_file, dst_file)
                out_files[key] = f"{folder}/{filename}"
                imported += 1
                safe_print(f"imported {src_file.name} -> {dst_file.relative_to(ROOT).as_posix()}")

        if not out_files.get("static_icon"):
            print(f"skip reaction without static_icon: {emoji}")
            continue

        out.append({
            "emoji": emoji,
            "title": title,
            "folder": folder,
            "files": out_files,
        })

    if not out:
        return 0

    (REACTIONS_DST / "manifest.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote manifest with {len(out)} reactions -> {(REACTIONS_DST / 'manifest.json').as_posix()}")
    return imported


def import_sticker_entry(path: Path, dst: Path, item_id: str) -> dict | None:
    ext = path.suffix.lower()
    data = path.read_bytes()
    if looks_like_html_error(data):
        print(f"skip broken download: {path.name}")
        return None

    if ext == ".tgs":
        if not is_gzip(data):
            print(f"skip invalid tgs: {path.name}")
            return None
        try:
            payload = gzip.decompress(data)
            json.loads(payload)
        except Exception as error:  # noqa: BLE001
            print(f"skip bad tgs {path.name}: {error}")
            return None
        out = dst / f"{item_id}.json"
        out.write_bytes(payload)
        safe_print(f"imported tgs -> {out.relative_to(ROOT).as_posix()}")
        return {"id": item_id, "file": out.name, "kind": "lottie", "title": path.stem}

    if ext not in IMAGE_EXT:
        print(f"skip unsupported: {path.name}")
        return None

    if ext == ".json":
        try:
            json.loads(data.decode("utf-8"))
        except Exception as error:  # noqa: BLE001
            print(f"skip bad json {path.name}: {error}")
            return None
        kind = "lottie"
    else:
        kind = ext.lstrip(".")

    out = dst / f"{item_id}{ext}"
    shutil.copy2(path, out)
    safe_print(f"imported {path.name} -> {out.relative_to(ROOT).as_posix()}")
    return {"id": item_id, "file": out.name, "kind": kind, "title": path.stem}


def sticker_file_order(path: Path) -> float:
    stat = path.stat()
    return float(getattr(stat, "st_birthtime", stat.st_ctime))


def import_sticker_pack() -> int:
    """Stickers from reactions/emoji only, ordered by file creation time (oldest first)."""
    clear_dir(STICKERS_DST)
    items: list[dict] = []

    if not EMOJI_SRC.is_dir():
        return 0

    files = sorted(
        [
            path for path in EMOJI_SRC.iterdir()
            if path.is_file() and path.suffix.lower() == ".tgs"
        ],
        key=sticker_file_order,
    )
    index = 1
    for path in files:
        item_id = f"s{index:03d}"
        entry = import_sticker_entry(path, STICKERS_DST, item_id)
        if entry:
            items.append(entry)
            index += 1

    if not items:
        return 0

    pack = {"id": "stickers", "title": "Stickers", "stickers": items}
    (STICKERS_DST / "pack.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(items)} stickers to {STICKERS_DST / 'pack.json'}")
    return len(items)


def import_files(src: Path, dst: Path, prefix: str, pack_id: str, pack_title: str) -> int:
    clear_dir(dst)
    items: list[dict] = []
    fallback_index = 1
    files = sorted(
        [path for path in src.iterdir() if path.is_file()],
        key=lambda path: path.name.lower(),
    )

    for path in files:
        ext = path.suffix.lower()
        data = path.read_bytes()
        if looks_like_html_error(data):
            print(f"skip broken download: {path.name}")
            continue

        number = source_number(path.stem) or f"{fallback_index:02d}"
        item_id = f"{prefix}{number}"

        if ext == ".tgs":
            if not is_gzip(data):
                print(f"skip invalid tgs: {path.name}")
                continue
            try:
                payload = gzip.decompress(data)
                json.loads(payload)
            except Exception as error:  # noqa: BLE001
                print(f"skip bad tgs {path.name}: {error}")
                continue
            out = dst / f"{item_id}.json"
            out.write_bytes(payload)
            items.append({"id": item_id, "file": out.name, "kind": "lottie", "title": path.stem})
            fallback_index += 1
            print(f"imported tgs -> {out.relative_to(ROOT)}")
            continue

        if ext not in IMAGE_EXT:
            print(f"skip unsupported: {path.name}")
            continue

        if ext == ".json":
            try:
                json.loads(data.decode("utf-8"))
            except Exception as error:  # noqa: BLE001
                print(f"skip bad json {path.name}: {error}")
                continue
            kind = "lottie"
        else:
            kind = ext.lstrip(".")

        out = dst / f"{item_id}{ext}"
        shutil.copy2(path, out)
        items.append({"id": item_id, "file": out.name, "kind": kind, "title": path.stem})
        fallback_index += 1
        print(f"imported {path.name} -> {out.relative_to(ROOT)}")

    pack = {"id": pack_id, "title": pack_title, "stickers": items}
    (dst / "pack.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(items)} items to {dst / 'pack.json'}")
    return len(items)


def main() -> None:
    if not SRC.is_dir():
        raise SystemExit(f"Missing source folder: {SRC}")

    manifest_count = import_manifest_reactions()
    sticker_count = import_sticker_pack()
    if manifest_count or sticker_count:
        print(f"done: {manifest_count} reaction asset files, {sticker_count} stickers")
        return

    if not EMOJI_SRC.is_dir():
        raise SystemExit(f"Missing emoji folder: {EMOJI_SRC}")

    reactions_count = import_files(
        SRC,
        REACTIONS_DST,
        prefix="r",
        pack_id="reactions",
        pack_title="Reactions",
    )
    emoji_count = import_files(
        EMOJI_SRC,
        STICKERS_DST,
        prefix="e",
        pack_id="emoji",
        pack_title="Emoji",
    )
    print(f"done: {reactions_count} reactions, {emoji_count} emoji")


if __name__ == "__main__":
    main()
