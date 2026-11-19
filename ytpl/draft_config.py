"""今ある再生リストから config.yaml の下書きを作る（初回セットアップ用）。

    python -m ytpl.draft_config > config.draft.yaml

再生リスト名と中身のチャンネル構成から分類を推定します。
「# 要確認」の行は必ず見直してください。
"""
from __future__ import annotations

import json
from collections import Counter

from .api import YouTube
from .store import DEFAULTS

SPECIAL = {DEFAULTS["titles"]["unsorted"], DEFAULTS["titles"]["misc"]}


def q(s) -> str:
    return json.dumps(s, ensure_ascii=False)


def main():
    yt = YouTube.from_env(3000)
    pls = yt.my_playlists()
    titles = {p["title"] for p in pls}
    contents = {p["title"]: yt.playlist_items(p["id"]) for p in pls}
    chan_name: dict[str, str] = {}
    for items in contents.values():
        for it in items:
            if it.get("channel_id"):
                chan_name[it["channel_id"]] = it.get("channel_title") or ""

    def ids(cid):
        return f"[{q(cid)}]  # {chan_name.get(cid, '')}" if cid else "[]  # 要確認：チャンネルIDを記入"

    def top_channel(items):
        c = Counter(it["channel_id"] for it in items if it.get("channel_id"))
        return c.most_common(1)[0][0] if c else None

    top, fav, songs, combos, listed = [], [], [], [], set()
    for t, items in contents.items():
        if t in SPECIAL or (t.startswith("その他") and f"{t[3:]}チャンネル" in titles):
            continue
        if t.endswith("チャンネル"):
            name = t[: -len("チャンネル")]
            cid = top_channel(items)
            listed.add(cid)
            (top if f"その他{name}" in titles else fav).append((name, cid))
        else:
            chans = Counter(it["channel_id"] for it in items if it.get("channel_id"))
            if len(chans) >= 3:
                songs.append(t)
            else:
                combos.append((t, [c for c, _ in chans.most_common(2)]))

    occ = Counter()
    for t in [*songs, DEFAULTS["titles"]["misc"], *[c[0] for c in combos]]:
        for it in contents.get(t, []):
            if it.get("channel_id") and it["channel_id"] not in listed:
                occ[it["channel_id"]] += 1

    L = ["# 自動生成された下書きです。内容を確認して config.yaml として保存してください。",
         "# 書式の説明は config.example.yaml を参照。", "", "top_favorites:"]
    for name, cid in top:
        L += [f"  - name: {q(name)}", f"    channel_ids: {ids(cid)}",
              f"    aliases: [{q(name)}]  # 別名・英語表記などを追加"]
    L += ["", "favorites:"]
    for name, cid in fav:
        L += [f"  - name: {q(name)}", f"    channel_ids: {ids(cid)}"]
    L += ["", "# 曲・コラボ組の再生リストや「その他の曲」に登場するチャンネル（多い順）", "occasional:"]
    for cid, n in occ.most_common():
        L += [f"  - name: {q(chan_name.get(cid) or cid)}  # 要確認（{n} 曲）", f"    channel_ids: [{q(cid)}]"]
    L += ["", "songs:"]
    for t in songs:
        L += [f"  - title: {q(t)}", f"    aliases: [{q(t)}]  # 英語表記・ローマ字などを追加",
              "    official_channel_ids: []  # 公式チャンネルのID"]
    L += ["", "# チャンネル数が少ない再生リスト → コラボ組と推定（曲の再生リストなら songs へ移動）", "combos:"]
    for t, cids in combos:
        L += [f"  - playlist: {q(t)}  # 要確認",
              f"    channel_ids: {ids(cids[0] if cids else None)}",
              "    partner_aliases: []  # コラボ相手の名前"]
    print("\n".join(L))
    print(f"\n# クォータ使用量: {yt.used}")


if __name__ == "__main__":
    main()
