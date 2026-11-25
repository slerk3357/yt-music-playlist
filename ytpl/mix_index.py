"""オリジナルミックスリスト用の索引（data/mix/index.json）を作る。

Cloudflare Worker（worker/）がこのファイルを非公開リポジトリから読み、ミックスを作る。
対象は config.yaml で管理している再生リストのうち、「未仕分け」と ignore_playlists を除いたもの。
動画は台帳で present（入っていて、削除・非公開でない）のものだけ。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import rules
from .store import Config, Ledger

KIND = {"unsorted": "unsorted", "misc": "misc", "rejected": "rejected", "own": "channel", "other": "other", "special": "special",
        "song": "song"}


def build(cfg: Config, ledger: Ledger, generated_at: str) -> dict:
    ignored = rules.ignored_playlist_ids(cfg)
    fixed = rules.fixed_playlists(cfg)
    excluded = {fixed["unsorted"].id, fixed["rejected"].id} - {None}   # 未仕分け・提案を却下はミックスに使わない
    playlists, included = [], set()
    for pl in rules.declared_playlists(cfg):
        pid = pl.id
        if not pid or pid in ignored or pid in excluded or pid in included:
            continue
        doc = ledger.docs.get(pid)
        if not doc or doc.get("deleted_since"):
            continue
        items = sorted((e for e in doc["items"].values() if e.get("status") == "present"),
                       key=lambda e: (e.get("position") is None, e.get("position") or 0))
        playlists.append({"id": pid, "name": doc.get("title") or pl.name,
                          "kind": KIND.get(pl.key.split(":")[0], "other"),
                          "videos": [e["video_id"] for e in items]})
        included.add(pid)

    # 特にお気に入りのチャンネルごとのまとまり：○○チャンネル＋その他○○＋特別再生リスト
    groups = []
    for key, _, i, e in rules.tier_entries(cfg):
        if key != "top_favorites":
            continue
        refs = [e.get("playlist"), (e.get("other") or {}).get("playlist")] + \
               [sp.get("playlist") for sp in e.get("specials") or []]
        pids = [r["id"] for r in refs if isinstance(r, dict) and r.get("id") in included]
        groups.append({"name": e["name"], "playlists": list(dict.fromkeys(pids))})
    return {"version": 1, "generated_at": generated_at, "playlists": playlists, "top_favorites": groups}


def write(path: Path, index: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 1再生リスト1行にして、毎日の差分を読みやすくする
    lines = ["{", f'  "version": {index["version"]},', f'  "generated_at": {json.dumps(index["generated_at"])},',
             '  "playlists": [']
    pls = [json.dumps(p, ensure_ascii=False, separators=(",", ":")) for p in index["playlists"]]
    lines += [f"    {p}{',' if n < len(pls) - 1 else ''}" for n, p in enumerate(pls)]
    lines += ["  ],", '  "top_favorites": [']
    gs = [json.dumps(g, ensure_ascii=False, separators=(",", ":")) for g in index["top_favorites"]]
    lines += [f"    {g}{',' if n < len(gs) - 1 else ''}" for n, g in enumerate(gs)]
    lines += ["  ]", "}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
