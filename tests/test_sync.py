"""偽のYouTube APIで同期処理を検証する。  pytest -q"""
from datetime import datetime, timedelta, timezone

import pytest

from ytpl import rules
from ytpl.api import COST, QuotaExceeded, YouTube, uploads_playlist_id
from ytpl.store import load_yaml
from ytpl.sync import run

JST = timezone(timedelta(hours=9))


def C(name: str) -> str:
    """テスト用のチャンネルID（UC＋22文字）。"""
    return "UC" + name.ljust(22, "0")[:22]


A, AT, B, CA, D, O = C("alice"), C("alicetopic"), C("bob"), C("carol"), C("dave"), C("official")


class FakeYouTube(YouTube):
    handles = {A: "@alice_official", D: "@dave"}

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


def V(id, title, ch, day, cat="10", dur=240, desc="", ch_title=None):
    return {"id": id, "title": title, "channel_id": ch, "channel_title": ch_title or ch, "description": desc,
            "published_at": f"2026-{day}T00:00:00Z", "category": cat, "duration": dur, "live": "none"}


CONFIG = f"""
music:
  include_keywords: ["歌ってみた", "cover"]
  exclude_keywords: ["切り抜き", "歌枠"]
promotion:
  min_versions: 3
playlists:
  unsorted: {{id: "PLunsorted", name: "未仕分け"}}
  misc: {{id: "PLmisc", name: "その他の曲"}}
top_favorites:
  - name: "アリス"
    channels:
      - {{id: "{A}", name: "アリス"}}
      - {{id: "{AT}", name: "アリス - Topic"}}
    aliases: ["Alice"]
    playlist: {{id: "PLalice", name: "アリスチャンネル"}}
    other_playlist: {{id: "PLaliceOther", name: "その他アリス"}}
favorites:
  - name: "ボブ"
    channels: [{{id: "{B}", name: "ボブ"}}]
    playlist: {{id: "PLbob", name: "ボブチャンネル"}}
occasional:
  - name: "キャロル"
    channels: [{{id: "{CA}", name: "キャロル"}}]
    specials:
      - playlist: {{name: "キャロルとデイブ"}}
        mentions: [{{id: "{D}", name: "デイブ"}}]
  - name: "デイブ"
    channels: [{{id: "{D}", name: "デイブ"}}]
songs:
  - name: "星の歌"
    aliases: ["星の歌", "Hoshi no Uta"]
    official_channels: [{{id: "{O}", name: "公式"}}]
"""


def setup(tmp_path, config=CONFIG):
    (tmp_path / "config.yaml").write_text(config, encoding="utf-8")
    vids = [
        V("a1", "『オリジナル曲』アリス MV", A, "01-01"),
        V("a1t", "オリジナル曲", AT, "01-01", desc="Provided to YouTube by Label", ch_title="アリス - Topic"),
        V("a2", "【歌ってみた】星の歌 / アリス", A, "01-02", cat="24"),
        V("a3", "アリス 雑談配信", A, "01-03", cat="24", dur=7200),
        V("a4", "オリジナル曲 (Instrumental)", A, "01-03"),
        V("o1", "星の歌 (Official MV)", O, "01-01"),
        V("o2", "全然関係ない曲", O, "01-01"),
        V("b1", "【cover】夜の曲 / ボブ", B, "01-04", cat="24"),
        V("c1", "Hoshi no Uta covered by キャロル", CA, "01-05"),
        V("c2", "夜の曲 / キャロル × デイブ", CA, "01-06", desc="with @dave"),
        V("c3", "朝の曲 / キャロル", CA, "01-07", desc="mix: Alice"),
        V("c5", "昼の曲 / キャロル", CA, "01-07", desc="mix: @alice_official"),
        V("d1", "夜の曲 歌ってみた デイブ", D, "01-08", cat="24"),
        V("d2", "切り抜き", D, "01-09"),
    ]
    playlists = {
        "PLalice": {"title": "アリスチャンネル", "items": ["a1"]},
        "PLaliceOther": {"title": "その他アリス", "items": []},
        "PLunsorted": {"title": "未仕分け", "items": []},
        "PLmisc": {"title": "その他の曲", "items": ["c2"]},
        "PLbob": {"title": "ボブチャンネル", "items": []},
    }
    return FakeYouTube(vids, playlists)


def kw(tmp_path, **k):
    return dict(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data",
                report_path=tmp_path / "reports/latest.md", **k)


def titles(yt):
    return {p["title"]: p["items"] for p in yt.pl.values()}


def day(m, d):
    return datetime(2026, m, d, 4, tzinfo=JST)


def test_first_run_and_incremental(tmp_path):
    yt = setup(tmp_path)
    before = (tmp_path / "config.yaml").read_bytes()
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    t = titles(yt)
    assert t["アリスチャンネル"] == ["a1", "a2"]      # TOPIC音源 a1t はMV a1 と重複、配信・インストは除外
    assert set(t["星の歌"]) == {"o1", "a2", "c1"}      # 公式＋カバー（新規作成）
    assert t["その他アリス"] == ["c5"]                  # @ハンドルで確認できたものだけ自動
    assert t["キャロルとデイブ"] == ["c2"]
    assert t["未仕分け"] == []                          # 初回取込は未仕分けにしない
    assert set(t["夜の曲"]) == {"b1", "c2", "d1"}      # 昇格（min_versions: 3）
    assert (tmp_path / "config.yaml").read_bytes() == before   # config.yaml は書き換えない
    promoted = load_yaml(tmp_path / "data/promoted_songs.yaml")
    assert promoted[0]["name"] == "夜の曲" and promoted[0]["playlist"]["id"].startswith("PLnew")
    created = load_yaml(tmp_path / "data/created_playlists.yaml")
    assert {v["name"] for v in created.values()} == {"星の歌", "キャロルとデイブ", "夜の曲"}

    # 新着
    yt.v["b2"] = V("b2", "ボブ 新曲 MV", B, "02-01")
    yt.v["d3"] = V("d3", "知らない曲 / デイブ", D, "02-02")
    yt.v["d4"] = V("d4", "星の歌 歌ってみた / デイブ", D, "02-03", cat="24")
    yt.v["a5"] = V("a5", "『新曲』アリス MV", A, "02-04")
    yt.v["c6"] = V("c6", "風の曲 / キャロル", CA, "02-05", desc="feat. Alice")
    run(yt=yt, now=day(2, 10), **kw(tmp_path))
    t = titles(yt)
    # b2: お気に入りの○○チャンネル候補 / d3: どこにも入らない / c6: 名前（aliases）だけで一致 → 確認のため
    assert t["未仕分け"] == ["b2", "d3", "c6"]
    assert "c6" not in t["その他アリス"]
    assert "d4" in t["星の歌"] and "a5" in t["アリスチャンネル"]
    assert len(set(t["星の歌"])) == len(t["星の歌"])     # 2回目以降も二重追加しない

    # ユーザーが手で仕分け・削除。未仕分けからは消さない（削除は一切しない）
    yt.pl["PLbob"]["items"].append("b2")
    yt.pl["PLalice"]["items"].remove("a2")
    run(yt=yt, now=day(2, 11), **kw(tmp_path))
    t = titles(yt)
    assert t["未仕分け"] == ["b2", "d3", "c6"]
    assert "a2" not in t["アリスチャンネル"]            # 手で消した曲は戻さない
    led = load_yaml(tmp_path / "data/playlists/アリスチャンネル.yaml")
    assert {e["video_id"]: e["status"] for e in led["items"]}["a2"] == "missing"


def test_user_removed_from_unsorted_is_not_readded(tmp_path):
    yt = setup(tmp_path)
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    yt.v["d3"] = V("d3", "知らない曲 / デイブ", D, "02-02")
    run(yt=yt, now=day(2, 10), **kw(tmp_path))
    yt.pl["PLunsorted"]["items"].remove("d3")
    run(yt=yt, now=day(2, 11), **kw(tmp_path))
    assert titles(yt)["未仕分け"] == []


def test_quota_carry_over(tmp_path):
    yt = setup(tmp_path)
    yt.budget = 60 + 50 * 3
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    pending = sum(1 for p in (tmp_path / "data/playlists").glob("*.yaml")
                  for e in load_yaml(p)["items"] if e["status"] == "pending")
    assert pending > 0
    yt.budget, yt.used = 10000, 0
    run(yt=yt, now=day(1, 11), **kw(tmp_path))
    assert set(titles(yt)["星の歌"]) == {"o1", "a2", "c1"}
    assert sum(1 for t in titles(yt) if t == "星の歌") == 1    # 再生リストを二重に作らない


def test_deleted_video_keeps_title(tmp_path):
    yt = setup(tmp_path)
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    yt.v["a1"].update(title="Deleted video", privacy="privacyStatusUnspecified")
    run(yt=yt, now=day(1, 11), **kw(tmp_path))
    led = load_yaml(tmp_path / "data/playlists/アリスチャンネル.yaml")
    e = next(e for e in led["items"] if e["video_id"] == "a1")
    assert e["status"] == "unavailable" and e["title"] == "『オリジナル曲』アリス MV"


def test_playlist_matched_by_id_not_name(tmp_path):
    yt = setup(tmp_path)
    yt.pl["PLalice"]["title"] = "アリス（改名）"
    yt.pl["PLdecoy"] = {"title": "アリスチャンネル", "items": []}   # 同名の別再生リスト
    text = run(yt=yt, now=day(1, 10), **kw(tmp_path))
    t = titles(yt)
    assert t["アリス（改名）"] == ["a1", "a2"] and t["アリスチャンネル"] == []
    assert "名前の不一致" in text


def test_other_requires_id(tmp_path):
    cfg = CONFIG.replace('    other_playlist: {id: "PLaliceOther", name: "その他アリス"}\n', "")
    yt = setup(tmp_path, cfg)
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    assert titles(yt)["その他アリス"] == []     # other_playlist がなければ扱わない


def test_unquoted_string_rejected(tmp_path):
    yt = setup(tmp_path, CONFIG.replace('name: "ボブ"\n', "name: ボブ\n"))
    with pytest.raises(SystemExit) as e:
        run(yt=yt, now=day(1, 10), **kw(tmp_path))
    assert "ダブルクオート" in str(e.value)


def test_no_delete_api():
    assert not hasattr(YouTube, "delete_item")
    assert uploads_playlist_id("UCabc") == "UUabc"
    assert rules.extract_song_title("【歌ってみた】「夜に駆ける」/ 誰か") == "夜に駆ける"
