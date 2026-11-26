"""チャンネルの動画が、どの取得元にあるかを調べる（読み取りだけ。YouTube には何も書き込まない）。

    python -m ytpl.probe --channel UCxxxx [--channel UCyyyy] [--video 動画ID ...] [--playlist 再生リストID ...] [--search]

調べる取得元
  1. アップロード一覧（UU…）
  1b. メンバー限定の動画の一覧（UUMO…。非公式。読めるかどうかも分かる）
  2. チャンネル情報に書かれたアップロード一覧のID
  3. チャンネルページの棚（種類・題名と、並ぶ再生リスト）
  4. チャンネルが公開している再生リストの一覧（リリース OLAK5uy_… を含むか）
  5. （--search のときだけ・100ユニット）検索で見つかるチャンネルの再生リスト
  6. --playlist で指定した再生リスト
それぞれの再生リストの中身（件数・投稿者・公開設定）と、アップロード一覧にない本人の動画、
--video で指定した動画がどこにあるかを Markdown で出力する。
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from .api import ApiError, YouTube, uploads_playlist_id
from .sync import MEMBERS_PREFIX, RELEASE_PREFIX


def _items(yt: YouTube, pid: str, max_pages: int) -> tuple[list[dict] | None, str | None, bool]:
    """(中身, エラー, 途中で打ち切ったか)"""
    out = []
    try:
        for n, page in enumerate(yt.playlist_pages(pid)):
            out += page
            if n + 1 >= max_pages:
                return out, None, True
    except ApiError as e:
        return None, f"{e.status} {e.reason}", False
    return out, None, False


def probe_channel(yt: YouTube, cid: str, videos: list[str], extra_playlists: list[str],
                  search: bool, max_pages: int) -> list[str]:
    md = []
    info = {}
    try:
        info = yt.channels_info([cid]).get(cid) or {}
    except ApiError as e:
        md.append(f"- チャンネル情報を取得できません: {e.status} {e.reason}")
    md.append(f"## {info.get('title') or '（名前不明）'}（{cid}）{(' ' + info['handle']) if info.get('handle') else ''}")
    if not info:
        md.append("- ⚠ チャンネルが見つかりません")

    sources: dict[str, str] = {}          # 再生リストID -> どこで見つけたか
    titles: dict[str, str] = {}

    def found(pid, where, title=None):
        sources[pid] = sources[pid] + "・" + where if pid in sources else where
        if title:
            titles.setdefault(pid, title)

    uu = uploads_playlist_id(cid)
    found(uu, "1. アップロード一覧", "アップロード一覧")
    found(MEMBERS_PREFIX + cid[2:], "1b. メンバー限定の一覧（非公式）", "メンバー限定の一覧")
    try:
        rel = yt.uploads_id(cid)
    except ApiError as e:
        rel = None
        md.append(f"- チャンネル情報のアップロード一覧: 取得できません（{e.status} {e.reason}）")
    if rel and rel != uu:
        found(rel, "2. チャンネル情報のアップロード一覧", "アップロード一覧（チャンネル情報）")
    md.append(f"- チャンネル情報のアップロード一覧: {rel or '（なし）'}{'（UU… と同じ）' if rel == uu else ''}")

    md.append("\n### 3. 棚")
    try:
        secs = yt.channel_sections(cid)
        if not secs:
            md.append("- （棚なし）")
        for s in secs:
            md.append(f"- {s['type']}「{s.get('title') or ''}」: 再生リスト {len(s['playlists'])} 件")
            for pid in s["playlists"]:
                found(pid, "3. 棚")
    except ApiError as e:
        md.append(f"- 取得できません: {e.status} {e.reason}")

    md.append("\n### 4. 公開している再生リスト")
    try:
        pls = yt.channel_playlists(cid)
        rels = [p for p in pls if p["id"].startswith(RELEASE_PREFIX)]
        md.append(f"- {len(pls)} 件（うちリリース {RELEASE_PREFIX}… が {len(rels)} 件）")
        for p in pls:
            found(p["id"], "4. 公開再生リスト" + ("（リリース）" if p["id"].startswith(RELEASE_PREFIX) else ""), p["title"])
    except ApiError as e:
        md.append(f"- 取得できません: {e.status} {e.reason}")

    if search:
        md.append("\n### 5. 検索で見つかる再生リスト（100ユニット）")
        try:
            hits = yt.search_channel_playlists(cid)
            md.append(f"- {len(hits)} 件")
            for p in hits:
                found(p["id"], "5. 検索", p["title"])
        except ApiError as e:
            md.append(f"- 取得できません: {e.status} {e.reason}")
    for pid in extra_playlists:
        found(pid, "6. 指定")

    # 再生リストの題名と件数
    try:
        meta = yt.playlists_info([p for p in sources if p != uu])
    except ApiError:
        meta = {}

    md.append("\n### 再生リストの中身")
    md.append("| 再生リスト | 見つけた場所 | 件数 | 本人の動画 | うちアップロード一覧にない | 公開設定 |")
    md.append("|---|---|---|---|---|---|")
    uu_items, uu_err, uu_cut = _items(yt, uu, max_pages)
    uu_ids = {x["video_id"] for x in uu_items or []}
    where_video: dict[str, list[str]] = {}
    missing_from_uu: dict[str, str] = {}
    for pid in sources:
        items, err, cut = (uu_items, uu_err, uu_cut) if pid == uu else _items(yt, pid, max_pages)
        title = titles.get(pid) or (meta.get(pid) or {}).get("title") or ""
        name = f"{title}（{pid}）" if title else pid
        if items is None:
            md.append(f"| {name} | {sources[pid]} | 取得できません: {err} | | | |")
            continue
        own = [x for x in items if x.get("channel_id") == cid]
        not_uu = [x for x in own if x["video_id"] not in uu_ids] if pid != uu else []
        for x in not_uu:
            missing_from_uu.setdefault(x["video_id"], x.get("title") or "")
        for x in items:
            where_video.setdefault(x["video_id"], []).append(name)
        privacy = Counter(x.get("privacy") or "?" for x in items)
        md.append(f"| {name} | {sources[pid]} | {len(items)}{'（途中まで）' if cut else ''} | {len(own)} | "
                  f"{len(not_uu) if pid != uu else '-'} | {'、'.join(f'{k} {v}' for k, v in privacy.most_common())} |")
    if uu_cut:
        md.append(f"\n（アップロード一覧は {max_pages * 50} 件までしか読んでいないため、「アップロード一覧にない」には"
                  "古い動画が混ざることがあります。--max-pages で増やせます）")

    md.append(f"\n### アップロード一覧にない本人の動画（{len(missing_from_uu)} 件）")
    for vid, t in list(missing_from_uu.items())[:100]:
        md.append(f"- {t}（{vid}）: {'、'.join(where_video.get(vid, []))}")

    if videos:
        md.append("\n### 指定した動画")
        try:
            vinfo = {v["id"]: v for v in yt.videos(videos)}
        except ApiError:
            vinfo = {}
        for vid in videos:
            v = vinfo.get(vid)
            desc = (f"{v['title']} / 投稿: {v.get('channel_title')}（{v.get('channel_id')}）/ 公開設定: {v.get('privacy')}"
                    if v else "動画情報を取得できません（削除・非公開など）")
            md.append(f"- {vid}: {desc}")
            md.append(f"  - 見つかった再生リスト: {'、'.join(where_video.get(vid, [])) or '（どこにもない）'}")
    return md


def main(argv=None):
    ap = argparse.ArgumentParser(description="チャンネルの動画の取得元を調べる（読み取りだけ）")
    ap.add_argument("--channel", action="append", required=True, help="チャンネルID（UC…）。複数指定できる")
    ap.add_argument("--video", action="append", default=[], help="どこにあるか調べる動画ID。複数指定できる")
    ap.add_argument("--playlist", action="append", default=[], help="あわせて調べる再生リストID")
    ap.add_argument("--search", action="store_true", help="検索でチャンネルの再生リストも探す（100ユニット）")
    ap.add_argument("--max-pages", type=int, default=20, help="1つの再生リストで読む最大ページ数（1ページ50件）")
    ap.add_argument("--budget", type=int, default=1000, help="使ってよいクォータ")
    a = ap.parse_args(argv)

    split = lambda xs: [y for x in xs for y in x.replace(",", " ").split() if y]   # noqa: E731
    yt = YouTube.from_env(a.budget)
    out = ["# 取得元の診断", ""]
    for cid in split(a.channel):
        out += probe_channel(yt, cid, split(a.video), split(a.playlist), a.search, a.max_pages) + [""]
    out.append(f"クォータ使用量: {yt.used}")
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
