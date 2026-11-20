"""偽のYouTube APIで同期処理を検証する。  pytest -q"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ytpl import rules
from ytpl.api import COST, QuotaExceeded, YouTube, uploads_playlist_id
from ytpl.store import load_yaml
from ytpl.sync import run

JST = timezone(timedelta(hours=9))


class FakeYouTube(YouTube):
    def __init__(self, videos, playlists, budget=10000):
        super().__init__(None, budget)
        self.v = {x["id"]: x for x in videos}
        self.pl = playlists  # id -> {"title", "items": [vid]}
        self.n = 0

    def _spend(self, kind):
        if not self.can_afford(kind):
            raise QuotaExceeded("fake")
        self.used += COST[kind]

    def my_playlists(self):
        self._spend("list")
        return [{"id": k, "title": p["title"], "count": len(p["items"])} for k, p in self.pl.items()]

    def playlist_pages(self, pid):
        if pid.startswith("UU"):
            cid = "UC" + pid[2:]
            ids = [v["id"] for v in sorted(self.v.values(), key=lambda x: x["published_at"], reverse=True)
                   if v["channel_id"] == cid]
            rows = [{"item_id": f"u-{i}", "video_id": i} for i in ids]
        else:
            rows = [{"item_id": f"{pid}-{i}", "video_id": i, "title": self.v[i]["title"],
                     "channel_id": self.v[i]["channel_id"], "channel_title": self.v[i]["channel_title"],
                     "position": n, "privacy": self.v[i].get("privacy", "public")}
                    for n, i in enumerate(self.pl[pid]["items"])]
        for i in range(0, max(len(rows), 1), 50):
            self._spend("list")
            yield rows[i:i + 50]

    def videos(self, ids):
        out = []
        for i in range(0, len(ids), 50):
            self._spend("list")
            out += [dict(self.v[x]) for x in ids[i:i + 50] if x in self.v]
        return out

    handles = {"UCalice": "@alice_official", "UCdave": "@dave"}

    def channel_handles(self, ids):
        self._spend("list")
        return {i: self.handles[i] for i in ids if i in self.handles}

    def create_playlist(self, title, privacy):
        self._spend("insert")
        self.n += 1
        pid = f"PLnew{self.n}"
        self.pl[pid] = {"title": title, "items": []}
        return pid

    def add_item(self, pid, vid):
        self._spend("insert")
        self.pl[pid]["items"].append(vid)
        return f"{pid}-{vid}"

    def delete_item(self, item_id):
        self._spend("delete")
        pid, vid = item_id.split("-", 1)
        self.pl[pid]["items"].remove(vid)


def V(id, title, ch, day, cat="10", dur=240, desc=""):
    return {"id": id, "title": title, "channel_id": ch, "channel_title": ch, "description": desc,
            "published_at": f"2026-{day}T00:00:00Z", "category": cat, "duration": dur, "live": "none"}


CONFIG = """
music:
  include_keywords: [歌ってみた, cover]
  exclude_keywords: [切り抜き, 歌枠]
promotion:
  min_versions: 3
top_favorites:
  - name: アリス
    channel_ids: [UCalice]
    aliases: [アリス, Alice]
favorites:
  - name: ボブ
    channel_ids: [UCbob]
occasional:
  - name: キャロル
    channel_ids: [UCcarol]
  - name: デイブ
    channel_ids: [UCdave]
songs:
  - title: 星の歌
    aliases: [星の歌, Hoshi no Uta]
    official_channel_ids: [UCofficial]
combos:
  - playlist: キャロルとデイブ
    channel_ids: [UCcarol]
    partner_aliases: [デイブ]
"""


def setup(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    vids = [
        V("a1", "アリス - オリジナル曲 MV", "UCalice", "01-01"),
        V("a2", "【歌ってみた】星の歌 / アリス", "UCalice", "01-02", cat="24"),
        V("a3", "アリス 雑談配信", "UCalice", "01-03", cat="24", dur=7200),
        V("o1", "星の歌 (Official MV)", "UCofficial", "01-01"),
        V("o2", "全然関係ない曲", "UCofficial", "01-01"),
        V("b1", "【cover】夜の曲 / ボブ", "UCbob", "01-04", cat="24"),
        V("c1", "Hoshi no Uta covered by キャロル", "UCcarol", "01-05"),
        V("c2", "夜の曲 / キャロル × デイブ", "UCcarol", "01-06"),
        V("c3", "朝の曲 / キャロル", "UCcarol", "01-07", desc="mix: Alice"),
        V("d1", "夜の曲 歌ってみた デイブ", "UCdave", "01-08", cat="24"),
        V("d2", "切り抜き", "UCdave", "01-09"),
    ]
    playlists = {
        "PLalice": {"title": "アリスチャンネル", "items": ["a1"]},
        "PLun": {"title": "未仕分け", "items": []},
        "PLmisc": {"title": "その他の曲", "items": ["c2"]},
        "PLbob": {"title": "ボブチャンネル", "items": []},
    }
    return FakeYouTube(vids, playlists)


def kw(tmp_path, **k):
    return dict(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data",
                report_path=tmp_path / "reports/latest.md", **k)


def titles(yt):
    return {p["title"]: p["items"] for p in yt.pl.values()}


def test_first_run_and_incremental(tmp_path):
    yt = setup(tmp_path)
    run(yt=yt, now=datetime(2026, 1, 10, 4, tzinfo=JST), **kw(tmp_path))
    t = titles(yt)
    assert t["アリスチャンネル"] == ["a1", "a2"]            # 配信は除外
    assert set(t["星の歌"]) == {"o1", "a2", "c1"}            # 公式＋カバー
    assert t["その他アリス"] == ["c3"]                        # 概要欄にAlice
    assert t["キャロルとデイブ"] == ["c2"]
    assert t["未仕分け"] == []                                # 初回取込は未仕分けにしない
    # 「夜の曲」は b1,c2,d1 の3曲 → 昇格（min_versions: 3）
    assert set(t["夜の曲"]) == {"b1", "c2", "d1"}
    assert "夜の曲" in (tmp_path / "config.yaml").read_text(encoding="utf-8")

    # 新着
    yt.v["b2"] = V("b2", "ボブ 新曲 MV", "UCbob", "02-01")
    yt.v["d3"] = V("d3", "知らない曲 / デイブ", "UCdave", "02-02")
    yt.v["d4"] = V("d4", "星の歌 歌ってみた / デイブ", "UCdave", "02-03", cat="24")
    yt.v["a4"] = V("a4", "アリス 新曲", "UCalice", "02-04")
    run(yt=yt, now=datetime(2026, 2, 10, 4, tzinfo=JST), **kw(tmp_path))
    t = titles(yt)
    assert t["未仕分け"] == ["b2", "d3"]           # お気に入り(b2)・たまに聴く未分類(d3)
    assert "d4" in t["星の歌"] and "a4" in t["アリスチャンネル"]

    # ユーザーが手で仕分け：d3 を「その他の曲」へ、b2 を「ボブチャンネル」へ
    yt.pl["PLmisc"]["items"].append("d3")
    yt.pl["PLbob"]["items"].append("b2")
    # ユーザーが a2 を「アリスチャンネル」から手動削除 → 再追加されないこと
    yt.pl["PLalice"]["items"].remove("a2")
    run(yt=yt, now=datetime(2026, 2, 11, 4, tzinfo=JST), **kw(tmp_path))
    t = titles(yt)
    assert t["未仕分け"] == []
    assert "a2" not in t["アリスチャンネル"]
    led = load_yaml(tmp_path / "data/playlists/アリスチャンネル.yaml")
    st = {e["video_id"]: e["status"] for e in led["items"]}
    assert st["a2"] == "missing" and st["a1"] == "present"
    assert (tmp_path / "reports/latest.md").exists()


def test_quota_carry_over(tmp_path):
    yt = setup(tmp_path)
    yt.budget = 60 + 50 * 3  # 読み取り後わずかしか書けない
    run(yt=yt, now=datetime(2026, 1, 10, 4, tzinfo=JST), **kw(tmp_path))
    pending = sum(1 for p in (tmp_path / "data/playlists").glob("*.yaml")
                  for e in load_yaml(p)["items"] if e["status"] == "pending")
    assert pending > 0
    yt.budget, yt.used = 10000, 0
    run(yt=yt, now=datetime(2026, 1, 11, 4, tzinfo=JST), **kw(tmp_path))
    assert set(titles(yt)["星の歌"]) == {"o1", "a2", "c1"}


def test_deleted_video_keeps_title(tmp_path):
    yt = setup(tmp_path)
    run(yt=yt, now=datetime(2026, 1, 10, 4, tzinfo=JST), **kw(tmp_path))
    yt.v["a1"].update(title="Deleted video", privacy="privacyStatusUnspecified")
    run(yt=yt, now=datetime(2026, 1, 11, 4, tzinfo=JST), **kw(tmp_path))
    led = load_yaml(tmp_path / "data/playlists/アリスチャンネル.yaml")
    e = next(e for e in led["items"] if e["video_id"] == "a1")
    assert e["status"] == "unavailable" and e["title"] == "アリス - オリジナル曲 MV"


def test_extract_song_title():
    assert rules.extract_song_title("【歌ってみた】「夜に駆ける」/ 誰か") == "夜に駆ける"
    assert rules.extract_song_title("【cover】夜の曲 / ボブ") == "夜の曲"
    assert rules.extract_song_title("Song Name (Official Music Video)") == "Song Name"
    assert uploads_playlist_id("UCabc") == "UUabc"


def test_playlist_id_pin_survives_rename(tmp_path):
    yt = setup(tmp_path)
    cfg = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    cfg = cfg.replace("    aliases: [アリス, Alice]\n", "    aliases: [アリス, Alice]\n    playlist_id: PLalice\n")
    (tmp_path / "config.yaml").write_text(cfg, encoding="utf-8")
    yt.pl["PLalice"]["title"] = "アリス（改名）"
    run(yt=yt, now=datetime(2026, 1, 10, 4, tzinfo=JST), **kw(tmp_path))
    t = titles(yt)
    assert "アリスチャンネル" not in t and t["アリス（改名）"] == ["a1", "a2"]


def test_other_by_handle_without_aliases(tmp_path):
    yt = setup(tmp_path)
    cfg = (tmp_path / "config.yaml").read_text(encoding="utf-8").replace("    aliases: [アリス, Alice]\n", "")
    (tmp_path / "config.yaml").write_text(cfg, encoding="utf-8")
    yt.v["c5"] = V("c5", "昼の曲 / キャロル", "UCcarol", "01-08", desc="mix: @alice_official")
    run(yt=yt, now=datetime(2026, 1, 10, 4, tzinfo=JST), **kw(tmp_path))
    assert titles(yt)["その他アリス"] == ["c5"]  # 名前だけの c3 は対象外
