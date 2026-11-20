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
    pid = {p["title"]: p["id"] for p in pls}
    contents = {p["title"]: yt.playlist_items(p["id"]) for p in pls}
    chan_name: dict[str, str] = {}
    for items in contents.values():
        for it in items:
            if it.get("channel_id"):
                chan_name[it["channel_id"]] = it.get("channel_title") or ""

    def counts(items):
        return Counter(it["channel_id"] for it in items if it.get("channel_id"))

    def ids(cid):
        return f"[{q(cid)}]  # {chan_name.get(cid, '')}" if cid else "[]  # 要確認：チャンネルIDを記入"

    def candidates(c: Counter, skip=(), indent="    "):
        rest = [(x, n) for x, n in c.most_common() if x not in skip][:8]
        if not rest:
            return []
        return [f"{indent}# 中身に登場する他のチャンネル（サブ・TOPIC等なら channel_ids に追加）:"] + \
               [f"{indent}#   {x}  {chan_name.get(x, '')}（{n} 曲）" for x, n in rest]

    top, fav, songs, combos, listed = [], [], [], [], set()
    for t, items in contents.items():
        if t in SPECIAL or (t.startswith("その他") and f"{t[3:]}チャンネル" in titles):
            continue
        c = counts(items)
        if t.endswith("チャンネル"):
            name = t[: -len("チャンネル")]
            cid = c.most_common(1)[0][0] if c else None
            listed.add(cid)
            (top if f"その他{name}" in titles else fav).append((name, cid, c))
        elif len(c) >= 3:
            songs.append((t, c))
        else:
            combos.append((t, c))

    occ = Counter()
    for t in [*[x[0] for x in songs], DEFAULTS["titles"]["misc"], *[x[0] for x in combos]]:
        for it in contents.get(t, []):
            if it.get("channel_id") and it["channel_id"] not in listed:
                occ[it["channel_id"]] += 1

    L = ["# 自動生成された下書きです。内容を確認して config.yaml として保存してください。",
         "# 書式の説明は config.example.yaml を参照。チャンネルIDは再生リストの中身から取得しています。", ""]
    special_ids = {k: pid[DEFAULTS["titles"][k]] for k in ("unsorted", "misc") if DEFAULTS["titles"][k] in pid}
    if special_ids:
        L += ["playlist_ids:"] + [f"  {k}: {q(v)}  # {DEFAULTS['titles'][k]}" for k, v in special_ids.items()] + [""]
    L += ["top_favorites:"]
    for name, cid, c in top:
        L += [f"  - name: {q(name)}", f"    playlist_id: {q(pid[name + 'チャンネル'])}",
              f"    other_playlist_id: {q(pid['その他' + name])}",
              f"    channel_ids: {ids(cid)}",
              "    aliases: []  # 任意：名前でも探す場合のみ（同名の別人が混ざる可能性あり）"]
        L += candidates(c, {cid})
    L += ["", "favorites:"]
    for name, cid, c in fav:
        L += [f"  - name: {q(name)}", f"    playlist_id: {q(pid[name + 'チャンネル'])}",
              f"    channel_ids: {ids(cid)}"]
        L += candidates(c, {cid})
    L += ["", "# 曲・コラボ組の再生リストや「その他の曲」に登場するチャンネル（多い順）", "occasional:"]
    for cid, n in occ.most_common():
        L += [f"  - name: {q(chan_name.get(cid) or cid)}  # 要確認（{n} 曲）", f"    channel_ids: [{q(cid)}]"]
    L += ["", "songs:"]
    for t, c in songs:
        L += [f"  - title: {q(t)}", f"    playlist_id: {q(pid[t])}",
              f"    aliases: [{q(t)}]  # 英語表記・ローマ字などを追加",
              "    official_channel_ids: []  # 本家のチャンネルID（下の候補から）"]
        L += candidates(c)
    L += ["", "# チャンネル数が少ない再生リスト → コラボ組と推定（曲の再生リストなら songs へ移動）", "combos:"]
    for t, c in combos:
        cids = [x for x, _ in c.most_common(2)]
        L += [f"  - playlist: {q(t)}  # 要確認", f"    playlist_id: {q(pid[t])}",
              f"    channel_ids: {ids(cids[0] if cids else None)}",
              f"    partner_channel_ids: {ids(cids[1] if len(cids) > 1 else None)}"]
    print("\n".join(L))
    print(f"\n# クォータ使用量: {yt.used}")


if __name__ == "__main__":
    main()
