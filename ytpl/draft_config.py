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

    def candidates(c: Counter, skip=(), indent="    ", hint="サブ・TOPIC等なら channel_ids に追加"):
        rest = [(x, n) for x, n in c.most_common() if x not in skip][:8]
        if not rest:
            return []
        return [f"{indent}# 中身に登場する他のチャンネル（{hint}）:"] + \
               [f"{indent}#   {x}  {chan_name.get(x, '')}（{n} 曲）" for x, n in rest]

    entries: dict[str, dict] = {}   # name -> {tier, cid, counts, specials}
    by_cid: dict[str, str] = {}
    songs, specials = [], []
    for t, items in contents.items():
        if t in SPECIAL or (t.startswith("その他") and f"{t[3:]}チャンネル" in titles):
            continue
        c = counts(items)
        if t.endswith("チャンネル"):
            name = t[: -len("チャンネル")]
            cid = c.most_common(1)[0][0] if c else None
            entries[name] = {"tier": "top" if f"その他{name}" in titles else "favorite",
                             "cid": cid, "counts": c, "specials": []}
            if cid:
                by_cid.setdefault(cid, name)
        elif len(c) >= 3:
            songs.append((t, c))
        else:
            specials.append((t, c))

    # 少数チャンネルの再生リスト → 主なチャンネルの特別再生リスト候補
    for t, c in specials:
        cids = [x for x, _ in c.most_common(2)]
        main_cid = cids[0] if cids else None
        name = by_cid.get(main_cid)
        if name is None:
            name = chan_name.get(main_cid) or main_cid or t
            entries.setdefault(name, {"tier": "occasional", "cid": main_cid, "counts": c, "specials": []})
            by_cid.setdefault(main_cid, name)
        entries[name]["specials"].append((t, cids))

    listed = {e["cid"] for e in entries.values()}
    occ = Counter()
    for t in [*[x[0] for x in songs], DEFAULTS["titles"]["misc"]]:
        for it in contents.get(t, []):
            if it.get("channel_id") and it["channel_id"] not in listed:
                occ[it["channel_id"]] += 1

    L = ["# 自動生成された下書きです。内容を確認して config.yaml として保存してください。",
         "# 書式の説明は config.example.yaml を参照。チャンネルIDは再生リストの中身から取得しています。", ""]
    special_ids = {k: pid[DEFAULTS["titles"][k]] for k in ("unsorted", "misc") if DEFAULTS["titles"][k] in pid}
    if special_ids:
        L += ["playlist_ids:"] + [f"  {k}: {q(v)}  # {DEFAULTS['titles'][k]}" for k, v in special_ids.items()] + [""]

    def entry_lines(name, e):
        out = [f"  - name: {q(name)}", f"    channel_ids: {ids(e['cid'])}"]
        out += candidates(e["counts"], {e["cid"]})
        if f"{name}チャンネル" in pid:
            out.append(f"    playlist_id: {q(pid[name + 'チャンネル'])}")
        if f"その他{name}" in pid:
            if e["tier"] != "top":
                out.append("    other: true")
            out.append(f"    other_playlist_id: {q(pid['その他' + name])}")
        if e["tier"] == "occasional" and f"{name}チャンネル" in pid:
            out.append("    own: true")
        if e["specials"]:
            out.append("    specials:  # 要確認：条件（published_by / mentions / keywords）を見直す")
            for t, cids in e["specials"]:
                out += [f"      - playlist: {q(t)}", f"        playlist_id: {q(pid[t])}"]
                if len(cids) > 1:
                    out.append(f"        mentions: {ids(cids[1])}")
        return out

    for key, tier in (("top_favorites", "top"), ("favorites", "favorite"), ("occasional", "occasional")):
        L += [f"{key}:"]
        for name, e in entries.items():
            if e["tier"] == tier:
                L += entry_lines(name, e)
        if tier == "occasional":
            L.append("  # 以下は曲の再生リストや「その他の曲」に登場するチャンネル（多い順）")
            for cid, n in occ.most_common():
                L += [f"  - name: {q(chan_name.get(cid) or cid)}  # 要確認（{n} 曲）", f"    channel_ids: [{q(cid)}]"]
        L.append("")
    L += ["songs:"]
    for t, c in songs:
        L += [f"  - title: {q(t)}", f"    playlist_id: {q(pid[t])}",
              f"    aliases: [{q(t)}]  # 英語表記・ローマ字などを追加",
              "    official_channel_ids: []  # 本家のチャンネルID（下の候補から）"]
        L += candidates(c, hint="本家のチャンネルなら official_channel_ids に追加")
    print("\n".join(L))
    print(f"\n# クォータ使用量: {yt.used}")


if __name__ == "__main__":
    main()
