"""今ある再生リストから config.yaml の下書きを作る（初回セットアップ用）。

    python -m ytpl.draft_config > config.draft.yaml

名前でチャンネルを検索することはしません。自分の再生リストの一覧と中身だけを読み、
中身の動画に付いている「投稿したチャンネルのID」から推定します。
「# 要確認」の行は必ず見直してください。
"""
from __future__ import annotations

import json
from collections import Counter

from .api import YouTube
from .rules import DEFAULT_NAMES


def q(s) -> str:
    return json.dumps("" if s is None else str(s), ensure_ascii=False)


def ref(id_, name) -> str:
    return f"{{id: {q(id_)}, name: {q(name)}}}"


def main():
    yt = YouTube.from_env(3000)
    pls = yt.my_playlists()
    pid = {p["title"]: p["id"] for p in pls}
    contents = {p["title"]: yt.playlist_items(p["id"]) for p in pls}
    chan_name: dict[str, str] = {}
    for items in contents.values():
        for it in items:
            if it.get("channel_id"):
                chan_name[it["channel_id"]] = it.get("channel_title") or ""

    def counts(items):
        return Counter(it["channel_id"] for it in items if it.get("channel_id"))

    def chan(cid):
        return ref(cid, chan_name.get(cid, "")) if cid else '{id: "", name: ""}  # 要確認：チャンネルを記入'

    def candidates(c: Counter, skip=(), indent="    ", hint="サブ・TOPIC等なら channels に追加"):
        rest = [(x, n) for x, n in c.most_common() if x not in skip][:8]
        if not rest:
            return []
        return [f"{indent}# 中身に登場する他のチャンネル（{hint}）:"] + \
               [f"{indent}#   - {ref(x, chan_name.get(x, ''))}  # {n} 曲" for x, n in rest]

    fixed_titles = set(DEFAULT_NAMES.values())
    titles = set(pid)
    entries: dict[str, dict] = {}
    by_cid: dict[str, str] = {}
    songs, small = [], []
    for t, items in contents.items():
        if t in fixed_titles or (t.startswith("その他") and f"{t[3:]}チャンネル" in titles):
            continue
        c = counts(items)
        if t.endswith("チャンネル"):
            name = t[: -len("チャンネル")]
            cid = c.most_common(1)[0][0] if c else None
            entries[name] = {"tier": "top_favorites" if f"その他{name}" in titles else "favorites",
                             "cid": cid, "counts": c, "specials": [], "own": t}
            if cid:
                by_cid.setdefault(cid, name)
        elif len(c) >= 3:
            songs.append((t, c))
        else:
            small.append((t, c))

    # 中身のチャンネルが少ない再生リスト → 主なチャンネルの特別再生リスト候補
    for t, c in small:
        cids = [x for x, _ in c.most_common(2)]
        main_cid = cids[0] if cids else None
        name = by_cid.get(main_cid)
        if name is None:
            name = chan_name.get(main_cid) or t
            entries.setdefault(name, {"tier": "occasional", "cid": main_cid, "counts": c, "specials": [], "own": None})
            by_cid.setdefault(main_cid, name)
        entries[name]["specials"].append((t, cids))

    listed = {e["cid"] for e in entries.values()}
    occ = Counter()
    for t in [*[x[0] for x in songs], DEFAULT_NAMES["misc"]]:
        for it in contents.get(t, []):
            if it.get("channel_id") and it["channel_id"] not in listed:
                occ[it["channel_id"]] += 1

    L = ["# 自動生成された下書きです。内容を確認して config.yaml として保存してください。",
         "# 書式は docs/config.md を参照。文字列はすべてダブルクオートで囲みます。", "",
         "playlists:"]
    for k, t in DEFAULT_NAMES.items():
        L.append(f"  {k}: {ref(pid[t], t)}" if t in pid else f"  {k}: {{name: {q(t)}}}  # 未作成：初回実行で作成")
    L.append("")

    def entry_lines(name, e):
        out = [f"  - name: {q(name)}", "    channels:", f"      - {chan(e['cid'])}"]
        out += candidates(e["counts"], {e["cid"]}, indent="      ")
        if e["own"]:
            out.append(f"    playlist: {ref(pid[e['own']], e['own'])}")
        if f"その他{name}" in pid:
            out += ["    other:", f"      playlist: {ref(pid['その他' + name], 'その他' + name)}"]
        if e["specials"]:
            out.append("    specials:  # 要確認：置き場所（どの人の項目か）と条件を見直す")
            for t, cids in e["specials"]:
                out += [f"      - playlist: {ref(pid[t], t)}"]
                if len(cids) > 1:
                    partner = by_cid.get(cids[1])
                    out += ["        mentions:", f"          - {q(partner) if partner else chan(cids[1])}"]
        return out

    for key in ("top_favorites", "favorites", "occasional"):
        L.append(f"{key}:")
        for name, e in entries.items():
            if e["tier"] == key:
                L += entry_lines(name, e)
        if key == "occasional":
            L.append("  # 以下は曲の再生リストや「その他の曲」に登場するチャンネル（多い順・要確認）")
            for cid, n in occ.most_common():
                L += [f"  - name: {q(chan_name.get(cid) or cid)}  # {n} 曲", "    channels:", f"      - {chan(cid)}"]
        L.append("")
    L.append("songs:")
    for t, c in songs:
        L += [f"  - name: {q(t)}", f"    playlist: {ref(pid[t], t)}",
              f"    aliases: [{q(t)}]  # 英語表記・ローマ字などを追加",
              "    official_channels: []  # 本家のチャンネル（下の候補から）"]
        L += candidates(c, hint="本家なら official_channels に追加")
    print("\n".join(L))
    print(f"\n# クォータ使用量: {yt.used}")


if __name__ == "__main__":
    main()
