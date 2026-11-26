"""設定・カタログ・再生リスト台帳（バックアップ）の読み書き。"""
from __future__ import annotations

import io
import re
from pathlib import Path

from ruamel.yaml import YAML


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
# 設定（システムが書き込むのは、作成した再生リストのIDと昇格した曲だけ）
# --------------------------------------------------------------------------
DEFAULTS = {
    "playlist_privacy": "private",
    "quota": {"daily_budget": 9000, "write_reserve": 2000},
    "playlists": {"unsorted": {"name": "未仕分け"}, "misc": {"name": "その他の曲"}},
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
}


def _plain_tree(obj):
    if isinstance(obj, dict):
        return {str(k): _plain_tree(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain_tree(v) for v in obj]
    if isinstance(obj, str):
        return str(obj)
    return obj


def unquoted_strings(node, path="") -> list[str]:
    """ダブルクオートで囲まれていない文字列値の場所を返す。"""
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString

    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += unquoted_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += unquoted_strings(v, f"{path}[{i}]")
    elif isinstance(node, str) and not isinstance(node, DoubleQuotedScalarString):
        out.append(f"{path}: {node}")
    return out


def dq(value):
    """システムが書き込む値。文字列はダブルクオート付きにする。"""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString

    if isinstance(value, str):
        return DoubleQuotedScalarString(value)
    if isinstance(value, dict):
        m = CommentedMap((k, dq(v)) for k, v in value.items())
        return m
    if isinstance(value, list):
        sq = CommentedSeq(dq(v) for v in value)
        sq.fa.set_flow_style()
        return sq
    return value


class Config:
    """config.yaml。システムが書き換えるのは「作成した再生リストのID」と「昇格した曲」だけ。

    ruamel.yaml の round-trip で読み書きするので、コメント・クオート・並び順は保たれる。
    インデント幅はファイルから推定し、ファイル全体をその幅にそろえて書き出す。
    """

    def __init__(self, path: str | Path):
        from ruamel.yaml.util import load_yaml_guess_indent

        self.path = Path(path)
        self.dirty = False
        text = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        self._indent = (2, 2)
        if text.strip():
            try:
                _, ind, bsi = load_yaml_guess_indent(text)
                self._indent = (ind or 2, bsi if bsi is not None else 2)
            except Exception:
                pass
        y = self._yaml()
        self.doc = y.load(text) if text.strip() else None
        if self.doc is None:
            from ruamel.yaml.comments import CommentedMap
            self.doc = CommentedMap()
        self.quote_errors = unquoted_strings(self.doc)
        self.raw = _plain_tree(self.doc)

    def _yaml(self) -> YAML:
        y = YAML()
        y.preserve_quotes = True
        y.width = 4096
        y.allow_unicode = True
        ind, bsi = self._indent
        y.indent(mapping=2, sequence=max(ind, bsi + 2), offset=bsi)
        return y

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

    def songs(self) -> list:
        return self.list("songs")

    @property
    def budget(self) -> int:
        return int(self.section("quota")["daily_budget"])

    # ---- システムによる書き込み -------------------------------------------
    def _node(self, path: tuple, create=False):
        from ruamel.yaml.comments import CommentedMap

        node = self.doc
        for i, key in enumerate(path):
            nxt = node[key] if (isinstance(key, int) or key in node) else None
            if nxt is None:
                if not create:
                    return None
                nxt = CommentedMap()
                if i == len(path) - 1:
                    nxt.fa.set_flow_style()
                node[key] = nxt
            node = nxt
        return node

    def set_playlist_id(self, path: tuple, pid: str, name: str) -> None:
        """path の再生リスト指定 {name} に id を書き込む（なければ {id, name} を作る）。"""
        node = self._node(path, create=True)
        if "name" not in node:
            node["name"] = dq(name)
        if "id" in node:
            node["id"] = dq(pid)
        else:
            node.insert(0, "id", dq(pid))
        self.raw = _plain_tree(self.doc)
        self.dirty = True

    def append_song(self, entry: dict) -> int:
        """昇格した曲を songs の末尾に追加し、その位置を返す。"""
        from ruamel.yaml.comments import CommentedSeq

        if self.doc.get("songs") is None:
            self.doc["songs"] = CommentedSeq()
        m = dq({k: v for k, v in entry.items() if k != "playlist"})
        if entry.get("playlist"):
            pl = dq(entry["playlist"])
            pl.fa.set_flow_style()
            m.insert(1, "playlist", pl)
        self.doc["songs"].append(m)
        self.raw = _plain_tree(self.doc)
        self.dirty = True
        return len(self.doc["songs"]) - 1

    def save(self) -> None:
        if not self.dirty:
            return
        buf = io.StringIO()
        self._yaml().dump(self.doc, buf)
        self.path.write_text(buf.getvalue(), encoding="utf-8")
        self.dirty = False


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
        # 監視外として記録していた動画を、監視しているチャンネルの分として記録し直す場合は、監視外から外す
        ext = self.channels.get(EXTERNAL)
        if key != EXTERNAL and ext and video["id"] in ext.get("videos", {}):
            del ext["videos"][video["id"]]
            self.dirty.add(EXTERNAL)

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

    def apply_snapshot(self, pid: str, title: str, items: list[dict], today: str) -> list[str]:
        """再生リストの今の中身を台帳に反映する。前回のバックアップのあとに新しく入った動画のIDを返す
        （初めてバックアップする再生リストでは空）。"""
        known_before = pid in self.docs and bool(self.docs[pid].get("last_synced"))
        new: list[str] = []
        doc = self.doc(pid, title)
        doc.pop("deleted_since", None)
        doc["last_synced"] = today
        seen = set()
        for it in items:
            vid = it["video_id"]
            if vid in seen:
                continue
            seen.add(vid)
            if vid not in doc["items"] and known_before:
                new.append(vid)
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
        return new

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
