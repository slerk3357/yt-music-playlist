"""設定・カタログ・再生リスト台帳（バックアップ）の読み書き。"""
from __future__ import annotations

import io
import re
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


def _yaml() -> YAML:
    y = YAML()
    y.width = 4096
    y.allow_unicode = True
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_yaml(path: Path, default=None):
    if not path.exists():
        return default
    data = _yaml().load(path.read_text(encoding="utf-8"))
    return default if data is None else data


def dump_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    _yaml().dump(data, buf)
    path.write_text(buf.getvalue(), encoding="utf-8")


# --------------------------------------------------------------------------
# 設定
# --------------------------------------------------------------------------
DEFAULTS = {
    "playlist_privacy": "private",
    "quota": {"daily_budget": 9000},
    "titles": {"own": "{name}チャンネル", "other": "その他{name}",
               "unsorted": "未仕分け", "misc": "その他の曲"},
    "music": {
        "categories": ["10"],
        "include_keywords": [],
        "exclude_keywords": [],
        "min_seconds": 61,
        "max_seconds": 900,
    },
    "matching": {"use_description": True},
    "dedupe": {"enabled": True},
    "promotion": {"enabled": True, "min_versions": 5, "ignore": []},
    "unsorted": {"auto_remove_when_sorted": True},
}


class Config:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.raw = load_yaml(self.path, CommentedMap())

    def section(self, name: str) -> dict:
        base = DEFAULTS.get(name)
        val = self.raw.get(name)
        if isinstance(base, dict):
            merged = dict(base)
            merged.update(dict(val or {}))
            return merged
        return base if val is None else val

    def list(self, name: str) -> list:
        return list(self.raw.get(name) or [])

    @property
    def budget(self) -> int:
        return int(self.section("quota")["daily_budget"])

    def title(self, kind: str, name: str = "") -> str:
        return self.section("titles")[kind].format(name=name)

    def append_song(self, entry: dict) -> None:
        if self.raw.get("songs") is None:
            self.raw["songs"] = CommentedSeq()
        self.raw["songs"].append(CommentedMap(entry))

    def save(self) -> None:
        dump_yaml(self.path, self.raw)


# --------------------------------------------------------------------------
# カタログ：監視チャンネルの全動画メタデータ
# --------------------------------------------------------------------------
EXTERNAL = "_external"


class Catalog:
    def __init__(self, root: Path):
        self.dir = root
        self.channels: dict[str, dict] = {}
        self.dirty: set[str] = set()
        if root.exists():
            for p in sorted(root.glob("*.yaml")):
                doc = load_yaml(p, {})
                self.channels[doc["channel_id"]] = _plain(doc)
        self.index = {vid: v for ch in self.channels.values() for vid, v in ch["videos"].items()}

    def channel(self, channel_id: str) -> dict:
        if channel_id not in self.channels:
            self.channels[channel_id] = {"channel_id": channel_id, "channel_title": None,
                                         "initialized": None, "last_checked": None, "videos": {}}
        return self.channels[channel_id]

    def add(self, key: str, video: dict) -> None:
        ch = self.channel(key)
        rec = {k: v for k, v in video.items() if k != "id"}
        if key != EXTERNAL and video.get("channel_title"):
            ch["channel_title"] = video["channel_title"]
        ch["videos"][video["id"]] = rec
        self.index[video["id"]] = rec
        self.dirty.add(key)

    def get(self, vid: str) -> dict | None:
        rec = self.index.get(vid)
        return None if rec is None else {"id": vid, **rec}

    def __contains__(self, vid: str) -> bool:
        return vid in self.index

    def all(self):
        for vid, rec in self.index.items():
            yield {"id": vid, **rec}

    def save(self) -> None:
        for key in self.dirty | {k for k, c in self.channels.items() if not (self.dir / f"{k}.yaml").exists()}:
            dump_yaml(self.dir / f"{key}.yaml", self.channels[key])
        self.dirty.clear()


# --------------------------------------------------------------------------
# 台帳：各再生リストに「入っている／入っていた／入るはず」の曲
# status: present | unavailable | missing | pending | removed | failed
# --------------------------------------------------------------------------
UNAVAILABLE_TITLES = {"Deleted video", "Private video", "削除された動画", "非公開動画"}


def _safe_name(title: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title).strip().strip(".")
    return s[:100] or "untitled"


def _plain(obj):
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


class Ledger:
    def __init__(self, root: Path):
        self.dir = root
        self.docs: dict[str, dict] = {}
        self.files: dict[str, Path] = {}
        if root.exists():
            for p in sorted(root.glob("*.yaml")):
                doc = _plain(load_yaml(p, {}))
                doc["items"] = {e["video_id"]: e for e in doc.get("items") or []}
                self.docs[doc["playlist_id"]] = doc
                self.files[doc["playlist_id"]] = p

    def doc(self, pid: str, title: str) -> dict:
        if pid not in self.docs:
            self.docs[pid] = {"playlist_id": pid, "title": title, "items": {}}
        self.docs[pid]["title"] = title
        return self.docs[pid]

    def apply_snapshot(self, pid: str, title: str, items: list[dict], today: str) -> None:
        doc = self.doc(pid, title)
        doc.pop("deleted_since", None)
        doc["last_synced"] = today
        seen = set()
        for it in items:
            vid = it["video_id"]
            if vid in seen:
                continue
            seen.add(vid)
            e = doc["items"].setdefault(vid, {"video_id": vid, "first_seen": today})
            unavailable = it.get("privacy") in ("private", "privacyStatusUnspecified") \
                or it.get("title") in UNAVAILABLE_TITLES
            if not unavailable or not e.get("title"):
                if not unavailable:
                    e["title"] = it.get("title")
                    e["channel_id"] = it.get("channel_id")
                    e["channel_title"] = it.get("channel_title")
            e["item_id"] = it["item_id"]
            e["position"] = it.get("position")
            if unavailable:
                if e.get("status") != "unavailable":
                    e["unavailable_since"] = today
                e["status"] = "unavailable"
            else:
                e["status"] = "present"
                e.pop("unavailable_since", None)
            e.pop("missing_since", None)
            e.pop("error", None)
        for vid, e in doc["items"].items():
            if vid not in seen and e.get("status") in ("present", "unavailable"):
                e["status"] = "missing"
                e["missing_since"] = today
                e.pop("item_id", None)
                e.pop("position", None)

    def mark_deleted_playlists(self, alive: set[str], today: str) -> list[str]:
        gone = []
        for pid, doc in self.docs.items():
            if pid not in alive and "deleted_since" not in doc:
                doc["deleted_since"] = today
                gone.append(doc["title"])
        return gone

    def status(self, pid: str, vid: str) -> str | None:
        e = self.docs.get(pid, {}).get("items", {}).get(vid)
        return e.get("status") if e else None

    def entry(self, pid: str, vid: str) -> dict:
        return self.docs[pid]["items"].setdefault(vid, {"video_id": vid})

    def present(self, pid: str) -> list[dict]:
        return [e for e in self.docs.get(pid, {}).get("items", {}).values() if e.get("status") == "present"]

    def save(self) -> None:
        used: set[str] = set()
        for pid, doc in self.docs.items():
            name = _safe_name(doc["title"])
            if name in used:
                name = f"{name}__{pid}"
            used.add(name)
            path = self.dir / f"{name}.yaml"
            old = self.files.get(pid)
            if old and old != path and old.exists():
                old.unlink()
            items = sorted(doc["items"].values(),
                           key=lambda e: (e.get("position") is None, e.get("position") or 0,
                                          e.get("first_seen") or "", e["video_id"]))
            out = {k: v for k, v in doc.items() if k != "items"}
            out["items"] = items
            dump_yaml(path, out)
            self.files[pid] = path
