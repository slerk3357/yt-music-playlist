"""YouTube上の今の状態を、手元の config.yaml に反映する（手元で実行するコマンド）。

    python -m ytpl.pull_config                  # 変更内容を表示し、config.yaml を更新
    python -m ytpl.pull_config --dry-run        # 表示だけ
    python -m ytpl.pull_config --channel-names  # {id, name} で書いたチャンネルの name も今の名前に更新

反映する内容（照合はすべて id で行い、名前で照合することはない）：
  1. 再生リストの name を、YouTube上の今のタイトルに更新
  2. YouTube上にない再生リスト・チャンネルを警告（config.yaml は変えない）
  3. YouTube上にあるが config.yaml にも ignore_playlists にもない再生リストを、
     ignore_playlists に「# 要確認」付きで追加（以後は無視される）
  4. --channel-names のときだけ、{id, name} で書いたチャンネルの name を今のチャンネル名に更新
     （3分類の項目の name は、利用者が付ける識別名なので書き換えない）

APIは読み取りだけ（再生リスト一覧：50件ごとに1、チャンネル情報：50件ごとに1）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from . import rules
from .api import YouTube
from .store import Config, dq


def _playlist_nodes(doc):
    """(場所の説明, {id, name} のノード) を列挙する。"""
    ps = doc.get("playlists") or {}
    for kind in rules.DEFAULT_NAMES:
        if isinstance(ps.get(kind), dict):
            yield f"playlists.{kind}", ps[kind]
    for key, _ in rules.TIERS:
        for i, e in enumerate(doc.get(key) or []):
            w = f"{key}「{e.get('name')}」"
            if isinstance(e.get("playlist"), dict):
                yield f"{w}.playlist", e["playlist"]
            other = e.get("other")
            if isinstance(other, dict) and isinstance(other.get("playlist"), dict):
                yield f"{w}.other.playlist", other["playlist"]
            for j, sp in enumerate(e.get("specials") or []):
                if isinstance(sp.get("playlist"), dict):
                    yield f"{w}.specials[{j}].playlist", sp["playlist"]
    for i, s in enumerate(doc.get("songs") or []):
        if isinstance(s.get("playlist"), dict):
            yield f"songs「{s.get('name')}」.playlist", s["playlist"]
    for i, r in enumerate(doc.get("ignore_playlists") or []):
        if isinstance(r, dict):
            yield f"ignore_playlists[{i}]", r


def _channel_nodes(doc):
    """(場所の説明, {id, name} のノード) を列挙する。名前による指定（文字列）は含まない。"""
    for key, _ in rules.TIERS:
        for e in doc.get(key) or []:
            w = f"{key}「{e.get('name')}」"
            for r in e.get("channels") or []:
                if isinstance(r, dict):
                    yield f"{w}.channels", r
            for j, sp in enumerate(e.get("specials") or []):
                for fld in ("published_by", "mentions"):
                    for r in sp.get(fld) or []:
                        if isinstance(r, dict):
                            yield f"{w}.specials[{j}].{fld}", r
    for s in doc.get("songs") or []:
        for r in s.get("official_channels") or []:
            if isinstance(r, dict):
                yield f"songs「{s.get('name')}」.official_channels", r


def referenced_playlist_ids(cfg: Config) -> set[str]:
    """config.yaml のどこかに（ignore_playlists も含めて）id が書かれている再生リスト。"""
    return {n.get("id") for _, n in _playlist_nodes(cfg.doc) if n.get("id")}


def add_unknown_to_ignore(cfg: Config, playlists: list[dict]) -> list[dict]:
    """config.yaml のどこにも載っていない再生リストを、ignore_playlists に「# 要確認」付きで追加する。

    追加した再生リストを返す（保存はしない）。毎日の実行と pull_config の両方から使う。
    """
    known = referenced_playlist_ids(cfg)
    new = sorted((p for p in playlists if p["id"] not in known), key=lambda p: p["title"])
    if not new:
        return []
    seq = cfg.doc.get("ignore_playlists")
    if not isinstance(seq, CommentedSeq):
        seq = CommentedSeq()
        cfg.doc["ignore_playlists"] = seq
    for p in new:
        m = CommentedMap([("id", dq(p["id"])), ("name", dq(p["title"]))])
        m.fa.set_flow_style()
        seq.append(m)
        seq.yaml_add_eol_comment("要確認", len(seq) - 1, column=0)   # 「 # 要確認」で統一
    from .store import _plain_tree
    cfg.raw = _plain_tree(cfg.doc)
    cfg.dirty = True
    return new


def add_occasional_channels(cfg: Config, found: list[dict], today: str) -> list[dict]:
    """監視していないチャンネルを occasional の先頭に「# 要確認」付きで追加する（保存はしない）。

    found は [{id, name, where}]（where は見つけた場所。コメントに書く）。追加したものを返す。
    3分類の name は重複できないので、同じ名前があれば「名前（チャンネルID）」にする。
    """
    from .rules import tier_entries
    from .store import _plain_tree

    if not found:
        return []
    names = {e.get("name") for _, _, _, e in tier_entries(cfg)}
    seq = cfg.doc.get("occasional")
    if not isinstance(seq, CommentedSeq):
        seq = CommentedSeq()
        cfg.doc["occasional"] = seq
    added = []
    for f in found:
        name = f.get("name") or f["id"]
        if name in names:
            name = f"{name}（{f['id']}）"
        names.add(name)
        ch = CommentedMap([("id", dq(f["id"])), ("name", dq(f.get("name") or f["id"]))])
        ch.fa.set_flow_style()
        chans = CommentedSeq([ch])
        chans.fa.set_flow_style()
        m = CommentedMap([("name", dq(name)), ("channels", chans)])
        note = f"要確認（{today} 自動追加：{f.get('where')}）" if f.get("where") else f"要確認（{today} 自動追加）"
        m.yaml_add_eol_comment(note.replace("#", "＃"), "name", column=0)
        # 先頭に入れる（末尾に入れると、最後の項目の後ろにある次の章の見出しコメントより後ろに書き出されるため）
        seq.insert(len(added), m)
        added.append({**f, "name": name})
    cfg.raw = _plain_tree(cfg.doc)
    cfg.dirty = True
    return added


def pull(cfg: Config, yt: YouTube, channel_names: bool = False) -> list[str]:
    """cfg.doc を書き換え、変更・警告の一覧を返す（保存はしない）。"""
    log: list[str] = []
    playlists = yt.my_playlists()
    title = {p["id"]: p["title"] for p in playlists}

    # 1・2. 再生リスト
    referenced = set()
    for where, node in _playlist_nodes(cfg.doc):
        pid = node.get("id")
        if not pid:
            continue
        referenced.add(pid)
        if pid not in title:
            log.append(f"⚠ 再生リストが見つかりません: {where} {pid}（{node.get('name')}）")
        elif node.get("name") != title[pid]:
            log.append(f"✎ 再生リスト名を更新: {where}「{node.get('name')}」→「{title[pid]}」")
            node["name"] = dq(title[pid])

    # 3. 未登録の再生リスト → ignore_playlists（要確認）
    for p in add_unknown_to_ignore(cfg, playlists):
        log.append(f"＋ ignore_playlists に追加（要確認）:「{p['title']}」{p['id']}")

    # 2・4. チャンネル
    nodes = list(_channel_nodes(cfg.doc))
    cids = sorted({n.get("id") for _, n in nodes if n.get("id")})
    info = yt.channels_info(cids) if cids else {}
    for where, node in nodes:
        cid = node.get("id")
        if not cid:
            continue
        if cid not in info:
            log.append(f"⚠ チャンネルが見つかりません: {where} {cid}（{node.get('name')}）")
        elif channel_names and info[cid]["title"] and node.get("name") != info[cid]["title"]:
            log.append(f"✎ チャンネル名を更新: {where}「{node.get('name')}」→「{info[cid]['title']}」")
            node["name"] = dq(info[cid]["title"])

    if any(line.startswith(("✎", "＋")) for line in log):
        cfg.dirty = True
    return log


def main(argv=None):
    ap = argparse.ArgumentParser(description="YouTube上の今の状態を config.yaml に反映する")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true", help="表示だけで config.yaml を変えない")
    ap.add_argument("--channel-names", action="store_true",
                    help="{id, name} で書いたチャンネルの name も今のチャンネル名に更新する")
    a = ap.parse_args(argv)

    cfg = Config(a.config)
    errors = rules.validate(cfg)
    if errors:
        print("config.yaml にエラーがあるため中止しました（python -m ytpl.check で確認）:")
        for e in errors:
            print(f"✗ {e}")
        sys.exit(1)
    yt = YouTube.from_env(3000)
    log = pull(cfg, yt, channel_names=a.channel_names)
    for line in log or ["変更はありません"]:
        print(line)
    if a.dry_run:
        print(f"\n（ドライラン：{a.config} は変更していません）")
    elif cfg.dirty:
        cfg.save()
        print(f"\n{a.config} を更新しました（playlist-tools ではこのあと自動でコミットします。手元なら git diff で確認してからコミット）。")
    print(f"クォータ使用量: {yt.used}")


if __name__ == "__main__":
    main()
