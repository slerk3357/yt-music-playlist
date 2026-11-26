"""偽のYouTube APIで同期処理を検証する。  pytest -q"""
from datetime import datetime, timedelta, timezone

import pytest

from ytpl import rules
from ytpl.api import COST, CREATED_MARK, QuotaExceeded, YouTube, uploads_playlist_id
from ytpl.store import Config, load_yaml
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
        return [{"id": k, "title": p["title"], "description": p.get("description", ""), "count": len(p["items"])}
                for k, p in self.pl.items()]

    topic_channels = set()   # アップロード一覧（UU…）が見つからないチャンネル
    albums = {}              # チャンネルID -> {再生リストID: [動画ID]}
    searchable = {}          # チャンネルID -> [動画ID]（検索で見つかる）

    def uploads_id(self, cid):
        self._spend("list")
        return None if cid in self.topic_channels else "UU" + cid[2:]

    def section_playlists(self, cid):
        self._spend("list")
        return list(self.albums.get(cid, {}))

    releases = {}            # チャンネルID -> {リリースの再生リストID: [動画ID]}（channel_playlists に出る）
    members_lists = {}       # チャンネルID -> [メンバー限定の動画ID]（UUMO…。ないチャンネルは 404）

    def uploads_ids(self, ids):
        self._spend("list")
        return {c: (None if c in self.topic_channels else "UU" + c[2:]) for c in ids}

    def channel_playlists(self, cid):
        self._spend("list")
        return [{"id": pid, "title": pid, "count": len(v)} for pid, v in self.releases.get(cid, {}).items()]

    def playlists_info(self, ids):
        self._spend("list")
        out = {}
        for group in list(self.albums.values()) + list(self.releases.values()):
            for pid, v in group.items():
                if pid in ids:
                    out[pid] = {"title": pid, "count": len(v), "channel_id": None}
        return out

    def search_channel_video_ids(self, cid, after=None):
        self._spend("search")
        return list(self.searchable.get(cid, []))

    def playlist_pages(self, pid):
        from ytpl.api import ApiError
        if pid.startswith("UUMO"):
            cid = "UC" + pid[4:]
            if cid not in self.members_lists:
                self._spend("list")
                raise ApiError(404, "playlistNotFound")
            self._spend("list")
            yield [{"item_id": f"m-{i}", "video_id": i} for i in self.members_lists[cid]]
            return
        if pid.startswith("UU") and "UC" + pid[2:] in self.topic_channels:
            self._spend("list")
            raise ApiError(404, "playlistNotFound")
        for albums in list(self.albums.values()) + list(self.releases.values()):
            if pid in albums:
                rows = [{"item_id": f"{pid}-{i}", "video_id": i, "title": self.v[i]["title"],
                         "channel_id": self.v[i]["channel_id"], "privacy": self.v[i].get("privacy", "public")}
                        for i in albums[pid]]
                self._spend("list")
                yield rows
                return
        if pid.startswith("UU"):
            cid = "UC" + pid[2:]
            ids = [v["id"] for v in sorted(self.v.values(), key=lambda x: x["published_at"], reverse=True)
                   if v["channel_id"] == cid and v.get("privacy", "public") == "public" and not v.get("release_only")]
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

    titles = {}          # チャンネルID -> 今のチャンネル名（なければIDのまま）
    missing_channels = set()

    def channels_info(self, ids):
        self._spend("list")
        return {i: {"title": self.titles.get(i, i), "handle": self.handles.get(i)}
                for i in ids if i not in self.missing_channels}

    def channel_handles(self, ids):
        return {c: v["handle"] for c, v in self.channels_info(ids).items() if v["handle"]}

    def create_playlist(self, title, privacy):
        self._spend("insert")
        self.n += 1
        pid = f"PLnew{self.n}"
        self.pl[pid] = {"title": title, "items": [], "description": CREATED_MARK}
        return pid

    def add_item(self, pid, vid):
        self._spend("insert")
        self.pl[pid]["items"].append(vid)
        return f"{pid}-{vid}"


def V(id, title, ch, day, cat="10", dur=240, desc="", ch_title=None):
    return {"id": id, "title": title, "channel_id": ch, "channel_title": ch_title or ch, "description": desc,
            "published_at": f"2026-{day}T00:00:00Z", "category": cat, "duration": dur, "live": "none"}


CONFIG = f"""
# 先頭のコメント
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
    other:
      playlist: {{id: "PLaliceOther", name: "その他アリス"}}  # 行末のコメント
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
# 曲のコメント
songs:
  - name: "星の歌"
    aliases: ["星の歌", "Hoshi no Uta"]
    official_channels: [{{id: "{O}", name: "公式"}}]
    original_artists: ["作者X"]
"""


def setup(tmp_path, config=CONFIG):
    (tmp_path / "config.yaml").write_text(config, encoding="utf-8")
    vids = [
        V("a1", "『オリジナル曲』アリス MV", A, "01-01"),
        V("a1t", "オリジナル曲", AT, "01-01", desc="Provided to YouTube by Label", ch_title="アリス - Topic"),
        V("a2", "【歌ってみた】星の歌 / アリス", A, "01-02", cat="24", desc="原曲: 作者X"),
        V("a3", "アリス 雑談配信", A, "01-03", cat="24", dur=7200),
        V("a4", "オリジナル曲 (Instrumental)", A, "01-03"),
        V("o1", "星の歌 (Official MV)", O, "01-01"),
        V("o2", "全然関係ない曲", O, "01-01"),
        V("b1", "【cover】夜の曲 / ボブ", B, "01-04", cat="24"),
        V("c1", "Hoshi no Uta covered by キャロル", CA, "01-05", desc="song by 作者X"),
        V("c2", "夜の曲 / キャロル × デイブ", CA, "01-06", desc="with @dave"),
        V("c3", "朝の曲 / キャロル", CA, "01-07", desc="mix: Alice"),
        V("c5", "昼の曲 / キャロル", CA, "01-07", desc="mix: @alice_official"),
        V("d1", "夜の曲 歌ってみた / デイブ", D, "01-08", cat="24"),
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
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    t = titles(yt)
    assert t["アリスチャンネル"] == ["a1", "a2"]      # TOPIC音源 a1t はMV a1 と重複、配信・インストは除外
    assert set(t["星の歌"]) == {"o1", "a2", "c1"}      # 公式＋カバー（新規作成）
    assert t["その他アリス"] == []                      # 「その他○○」は自動では追加しない
    assert t["キャロルとデイブ"] == ["c2"]
    assert t["未仕分け"] == []                          # 初回取込は未仕分けにしない
    assert set(t["夜の曲"]) == {"b1", "c2", "d1"}      # 昇格（min_versions: 3）
    # config.yaml に作成した再生リストのIDと昇格した曲が書き戻され、コメントとクオートは保たれる
    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    for c in ("# 先頭のコメント", "# 行末のコメント", "# 曲のコメント"):
        assert c in text
    cfg = Config(tmp_path / "config.yaml")
    assert rules.validate(cfg) == []
    assert cfg.raw["songs"][0]["playlist"]["id"].startswith("PLnew")       # 星の歌
    assert cfg.raw["songs"][1]["name"] == "夜の曲" and cfg.raw["songs"][1]["playlist"]["id"].startswith("PLnew")
    assert cfg.raw["occasional"][0]["specials"][0]["playlist"]["id"].startswith("PLnew")

    # 新着
    yt.v["b2"] = V("b2", "ボブ 新曲 MV", B, "02-01")
    yt.v["d3"] = V("d3", "知らない曲 / デイブ", D, "02-02")
    yt.v["d4"] = V("d4", "星の歌 歌ってみた / デイブ", D, "02-03", cat="24", desc="原曲 @official_x")
    yt.handles = {**yt.handles, O: "@official_x"}
    yt.v["d7"] = V("d7", "星の歌 / デイブ", D, "02-08", desc="同名の別の曲")    # 原曲の手がかりなし → 未仕分け
    yt.v["a5"] = V("a5", "『新曲』アリス MV", A, "02-04")
    yt.v["c6"] = V("c6", "風の曲 / キャロル", CA, "02-05", desc="feat. Alice")
    yt.v["c7"] = V("c7", "雨の曲 / キャロル", CA, "02-06", desc="feat. @alice_official")
    yt.v["d5"] = V("d5", "知らない曲 (Off Vocal) / デイブ", D, "02-07")
    run(yt=yt, now=day(2, 10), **kw(tmp_path))
    t = titles(yt)
    # b2: お気に入りの○○チャンネル候補 / d3: どこにも入らない / c6: 名前だけで一致 / c7: その他アリス候補
    # d5: オフボーカルは含める設定の再生リストがないので入れない
    assert t["未仕分け"] == ["d7", "c7", "c6", "d3", "b2"]       # 新しい順
    assert t["その他アリス"] == []
    assert "d4" in t["星の歌"] and "a5" in t["アリスチャンネル"]
    assert len(set(t["星の歌"])) == len(t["星の歌"])     # 2回目以降も二重追加しない

    # ユーザーが手で仕分け・削除。未仕分けからは消さない（削除は一切しない）
    yt.pl["PLbob"]["items"].append("b2")
    yt.pl["PLalice"]["items"].remove("a2")
    run(yt=yt, now=day(2, 11), **kw(tmp_path))
    t = titles(yt)
    assert t["未仕分け"] == ["d7", "c7", "c6", "d3", "b2"]
    assert "d7" not in t["星の歌"]
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
    yt = setup(tmp_path, CONFIG.replace("promotion:", "quota:\n  write_reserve: 0\npromotion:"))
    yt.budget = 30 + 50 * 3 + 50 * 3       # 読み取り・再生リスト3つの作成・追加3件分
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
    yt = setup(tmp_path, CONFIG.replace('playlist: {id: "PLaliceOther", name: "その他アリス"}',
                                        'playlist: {name: "その他アリス"}'))
    with pytest.raises(SystemExit) as e:
        run(yt=yt, now=day(1, 10), **kw(tmp_path))
    assert "other.playlist" in str(e.value)


def test_dry_run_does_not_touch_config(tmp_path):
    yt = setup(tmp_path)
    before = (tmp_path / "config.yaml").read_bytes()
    run(yt=yt, now=day(1, 10), dry_run=True, **kw(tmp_path))
    assert (tmp_path / "config.yaml").read_bytes() == before


def test_unquoted_string_rejected(tmp_path):
    yt = setup(tmp_path, CONFIG.replace('name: "ボブ"\n', "name: ボブ\n"))
    with pytest.raises(SystemExit) as e:
        run(yt=yt, now=day(1, 10), **kw(tmp_path))
    assert "ダブルクオート" in str(e.value)


def test_no_delete_api():
    assert not hasattr(YouTube, "delete_item")
    assert uploads_playlist_id("UCabc") == "UUabc"
    assert rules.extract_song_title("【歌ってみた】「夜に駆ける」/ 誰か") == "夜に駆ける"


def test_playlists_created_first_even_without_quota_for_reads(tmp_path):
    """読み取りでクォータが尽きても、再生リストの作成は先に終わっている。"""
    yt = setup(tmp_path)
    yt.budget = 1 + 50 * 2                  # 一覧の取得と、再生リスト2つ（星の歌・キャロルとデイブ）の作成だけ
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    t = titles(yt)
    assert t["星の歌"] == [] and t["キャロルとデイブ"] == []
    cfg = Config(tmp_path / "config.yaml")
    assert cfg.raw["songs"][0]["playlist"]["id"].startswith("PLnew")


def test_create_only_and_no_duplicate(tmp_path):
    yt = setup(tmp_path)
    run(yt=yt, now=day(1, 10), create_only=True, **kw(tmp_path))
    t = titles(yt)
    assert t["星の歌"] == [] and t["キャロルとデイブ"] == []      # 作成だけ。中身は追加しない
    assert yt.used == 1 + 50 * 2            # 星の歌・キャロルとデイブ（「提案を却下」は今は作らない）
    # config.yaml への書き戻しが失われた（push失敗など）場合でも、二重に作らない
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    run(yt=yt, now=day(1, 10), create_only=True, **kw(tmp_path))
    assert sum(1 for p in yt.pl.values() if p["title"] == "星の歌") == 1
    assert Config(tmp_path / "config.yaml").raw["songs"][0]["playlist"]["id"].startswith("PLnew")


def test_new_songs_before_backlog(tmp_path):
    """新曲（未仕分け・○○チャンネル）が、取り込み待ちより先に追加される（手で追加してもらう機能は使わない場合）。"""
    yt = setup(tmp_path, CONFIG.replace("promotion:", "quota:\n  write_reserve: 0\nhand_add:\n  mode: \"never\"\npromotion:"))
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    # 新しい曲名を songs に足す（過去の曲が取り込み待ちになる）と同時に、新曲を2つ公開
    cfg = (tmp_path / "config.yaml").read_text(encoding="utf-8") + '  - name: "朝の曲"\n'
    (tmp_path / "config.yaml").write_text(cfg, encoding="utf-8")
    yt.v["a6"] = V("a6", "『花の曲』アリス MV", A, "03-01")
    yt.v["d6"] = V("d6", "別の曲 / デイブ", D, "03-02")
    yt.used, yt.budget = 0, 0
    reads = 40
    yt.budget = reads + 50 * 1 + 50 * 2        # 再生リスト「朝の曲」の作成＋新曲2件だけ
    run(yt=yt, now=day(3, 10), **kw(tmp_path))
    t = titles(yt)
    assert "a6" in t["アリスチャンネル"] and "d6" in t["未仕分け"]
    assert t["朝の曲"] == []                    # 取り込み待ち（c3）は次回
    yt.used, yt.budget = 0, 10000
    run(yt=yt, now=day(3, 11), **kw(tmp_path))
    assert titles(yt)["朝の曲"] == ["c3"]


def test_reserve_defers_new_channel_and_unknown_playlists(tmp_path):
    yt = setup(tmp_path)
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    yt.pl["PLother"] = {"title": "管理外の再生リスト", "items": ["a1"]}
    cfg = (tmp_path / "config.yaml").read_text(encoding="utf-8").replace(
        "occasional:", f'occasional:\n  - name: "エド"\n    channels: [{{id: "{C("ed")}", name: "エド"}}]')
    (tmp_path / "config.yaml").write_text(cfg, encoding="utf-8")
    yt.v["e1"] = V("e1", "エドの曲", C("ed"), "01-01")
    yt.used, yt.budget = 0, 2000 + 10          # 一覧1＋管理対象9件のバックアップ分だけ予約枠の外
    text = run(yt=yt, now=day(2, 10), **kw(tmp_path))
    assert "次回以降に取り込むチャンネル" in text and "エド" in text
    # 未知の再生リストは ignore_playlists に記録され、バックアップの対象にしない
    assert "管理外として ignore_playlists に追加" in text and "管理外の再生リスト" in text
    cfg_text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert '- {id: "PLother", name: "管理外の再生リスト"} # 要確認' in cfg_text
    yt.used, yt.budget = 0, 10000
    text = run(yt=yt, now=day(2, 11), **kw(tmp_path))
    assert not (tmp_path / "data/playlists/管理外の再生リスト.yaml").exists()
    assert "管理外として ignore_playlists に追加" not in text      # 2回目は追加しない
    assert "e1" in {v for ch in __import__("ytpl.store", fromlist=["x"]).Catalog(tmp_path / "data/catalog").channels.values()
                    for v in ch["videos"]}


def test_ignore_playlists(tmp_path):
    cfg = CONFIG.replace("promotion:", 'ignore_playlists:\n  - {id: "PLprivate", name: "個人用"}\npromotion:')
    yt = setup(tmp_path, cfg)
    yt.pl["PLprivate"] = {"title": "個人用", "items": ["a1"]}
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    assert not (tmp_path / "data/playlists/個人用.yaml").exists()
    bad = cfg.replace('playlist: {id: "PLbob", name: "ボブチャンネル"}', 'playlist: {id: "PLprivate", name: "ボブチャンネル"}')
    (tmp_path / "config.yaml").write_text(bad, encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        run(yt=yt, now=day(1, 11), **kw(tmp_path))
    assert "ignore_playlists" in str(e.value)


def test_pull_config(tmp_path):
    from ytpl import pull_config
    yt = setup(tmp_path)
    p = tmp_path / "config.yaml"
    yt.pl["PLalice"]["title"] = "アリス（新しい名前）"         # 改名
    del yt.pl["PLaliceOther"]                                   # YouTube上で消えた
    yt.pl["PLfree"] = {"title": "作業用", "items": []}          # 未登録
    yt.titles = {A: "Alice Official", B: "ボブ"}
    yt.missing_channels = {D}
    before = p.read_text(encoding="utf-8")

    cfg = Config(p)
    log = "\n".join(pull_config.pull(cfg, yt))
    cfg.save()
    text = p.read_text(encoding="utf-8")
    assert "再生リスト名を更新" in log and "アリス（新しい名前）" in log
    assert "再生リストが見つかりません" in log and "PLaliceOther" in log
    assert "チャンネルが見つかりません" in log and D in log
    assert 'playlist: {id: "PLalice", name: "アリス（新しい名前）"}' in text
    assert '- {id: "PLfree", name: "作業用"} # 要確認' in text
    assert 'name: "アリス"\n' in text                    # 3分類の name は変えない
    assert '{id: "' + A + '", name: "アリス"}' in text     # チャンネル名は既定では変えない
    for c in ("# 先頭のコメント", "# 行末のコメント", "# 曲のコメント"):
        assert c in text
    cfg2 = Config(p)
    assert rules.validate(cfg2) == []
    # 2回目は変化なし（追加済みの「作業用」を二重に追加しない）
    assert not any(x.startswith(("✎", "＋")) for x in pull_config.pull(cfg2, yt))

    # --channel-names
    cfg3 = Config(p)
    log = "\n".join(pull_config.pull(cfg3, yt, channel_names=True))
    cfg3.save()
    text = p.read_text(encoding="utf-8")
    assert "チャンネル名を更新" in log
    assert '{id: "' + A + '", name: "Alice Official"}' in text
    assert 'name: "アリス"\n' in text                    # それでも3分類の name は変えない
    assert before != text


def test_mix_index(tmp_path):
    import json
    cfg = CONFIG.replace("promotion:", 'ignore_playlists:\n  - {id: "PLprivate", name: "個人用"}\npromotion:')
    yt = setup(tmp_path, cfg)
    yt.pl["PLprivate"] = {"title": "個人用", "items": ["a1"]}
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    yt.v["d3"] = V("d3", "知らない曲 / デイブ", D, "02-02")
    run(yt=yt, now=day(2, 10), **kw(tmp_path))                     # d3 は未仕分けへ
    idx = json.loads((tmp_path / "data/mix/index.json").read_text(encoding="utf-8"))
    by_name = {p["name"]: p for p in idx["playlists"]}
    assert "未仕分け" not in by_name and "個人用" not in by_name
    assert by_name["アリスチャンネル"]["videos"] == ["a1", "a2"] and by_name["アリスチャンネル"]["kind"] == "channel"
    assert by_name["その他の曲"]["kind"] == "misc"
    assert all("d3" not in p["videos"] for p in idx["playlists"])
    top = idx["top_favorites"][0]
    assert top["name"] == "アリス" and top["playlists"] == ["PLalice", "PLaliceOther"]


def test_topic_channel_fallbacks(tmp_path):
    """アップロード一覧が見つからないチャンネル：棚の再生リスト → 検索 の順に取得する。"""
    AT2, BT = C("alicetopic2"), C("bobtopic")
    cfg = CONFIG.replace('      - {{id: "{AT}", name: "アリス - Topic"}}'.format(AT=AT),
                         f'      - {{id: "{AT}", name: "アリス - Topic"}}\n      - {{id: "{AT2}", name: "アリス2 - Topic"}}')
    cfg = cfg.replace('    channels: [{id: "' + B + '", name: "ボブ"}]',
                      '    channels: [{id: "' + B + '", name: "ボブ"}, {id: "' + BT + '", name: "ボブ - Topic"}]')
    yt = setup(tmp_path, cfg)
    yt.topic_channels = {AT2, BT}
    yt.v["t1"] = V("t1", "空の歌", AT2, "01-05", desc="Provided to YouTube by Label", ch_title="アリス2 - Topic")
    yt.v["x1"] = V("x1", "他人の曲", C("someone"), "01-05")
    yt.albums = {AT2: {"OLAKalbum1": ["t1", "x1"]}}           # アルバムに他人の曲が混ざっていても除く
    yt.v["s1"] = V("s1", "海の歌", BT, "01-06", desc="Provided to YouTube by Label", ch_title="ボブ - Topic")
    yt.searchable = {BT: ["s1"]}
    text = run(yt=yt, now=day(1, 10), **kw(tmp_path))
    assert "t1" in titles(yt)["アリスチャンネル"] and "x1" not in titles(yt)["アリスチャンネル"]
    from ytpl.store import Catalog
    cat = Catalog(tmp_path / "data/catalog")
    assert cat.channels[AT2]["source"] == "playlists" and cat.channels[BT]["source"] == "search"
    assert cat.channels[AT2]["uploads_missing"] == "2026-01-10"          # UU… は7日ごとに試し直す
    assert "s1" in cat.channels[BT]["videos"]
    # 2回目：新しいアルバムも読む
    yt.v["t2"] = V("t2", "雲の歌", AT2, "02-01", desc="Provided to YouTube by Label", ch_title="アリス2 - Topic")
    yt.albums[AT2]["OLAKalbum2"] = ["t2"]
    run(yt=yt, now=day(2, 10), **kw(tmp_path))
    assert "t2" in titles(yt)["アリスチャンネル"]


def test_all_sources_are_read_by_default(tmp_path):
    """アップロード一覧が読めるチャンネルでも、棚の再生リストとリリースを読み、
    アップロード一覧にない動画（リリースにだけある動画・限定公開の動画）を取り込む。"""
    yt = setup(tmp_path)
    yt.v["r1"] = {**V("r1", "砂の歌", A, "01-02"), "release_only": True}        # リリースにだけある本人の動画
    yt.v["u1"] = {**V("u1", "風の歌", A, "01-02"), "privacy": "unlisted"}       # 限定公開（棚の再生リストにだけある）
    yt.v["t9"] = V("t9", "月の歌", AT, "01-02", desc="Provided to YouTube by Label", ch_title="アリス - Topic")
    yt.v["x9"] = V("x9", "他人の曲", C("someone"), "01-02")
    yt.releases = {A: {"OLAK5uy_rel1": ["r1", "t9", "x9"], "PLnotrelease": ["x9"]}}
    yt.albums = {A: {"PLshelf": ["u1"]}}
    run(yt=yt, now=day(1, 10), remote=None, **kw(tmp_path))
    got = titles(yt)["アリスチャンネル"]
    assert {"r1", "u1", "t9"} <= set(got)          # Topic（同じ項目のチャンネル）の曲も取り込む
    assert "x9" not in got                          # 関係のないチャンネルの動画は除く
    from ytpl.store import Catalog
    cat = Catalog(tmp_path / "data/catalog")
    assert cat.channels[A]["source"] == "uploads+playlists"
    assert "t9" in cat.channels[AT]["videos"]       # 投稿したチャンネルの分として記録
    assert set(cat.channels[A]["playlists"]) == {"OLAK5uy_rel1", "PLshelf"}   # リリース以外の再生リストは読まない

    # 件数が変わらなければ読み直さない。増えたら読み直し、新着として扱う
    run(yt=yt, now=day(1, 11), remote=None, **kw(tmp_path))
    assert Catalog(tmp_path / "data/catalog").channels[A]["playlists"]["OLAK5uy_rel1"]["read"] == "2026-01-10"
    yt.v["r2"] = {**V("r2", "雪の歌", A, "01-03"), "release_only": True}
    yt.releases[A]["OLAK5uy_rel1"].append("r2")
    yt.releases[A]["OLAK5uy_rel2"] = []
    text = run(yt=yt, now=day(1, 12), remote=None, **kw(tmp_path))
    assert "r2" in titles(yt)["アリスチャンネル"]
    # 30日たったら、件数が同じでも読み直す
    run(yt=yt, now=day(2, 12), remote=None, **kw(tmp_path))
    assert Catalog(tmp_path / "data/catalog").channels[A]["playlists"]["OLAK5uy_rel1"]["read"] == "2026-02-12"
    assert "新しく見つけた再生リスト（棚・リリース）" in text and "OLAK5uy_rel2" in text


def test_suggestions_for_other_playlists(tmp_path):
    """すでに別の再生リストに入っている曲が、ほかの再生リストの確認候補に当たっていれば提案する。"""
    yt = setup(tmp_path)
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    # c3（名前「Alice」だけで一致＝その他アリスの確認候補）を、ユーザーが「その他の曲」に入れた
    yt.pl["PLmisc"]["items"].append("c3")
    # b1（ボブの曲＝ボブチャンネルの確認候補）は、すでに「夜の曲」に入っている
    run(yt=yt, now=day(1, 11), **kw(tmp_path))
    sug = (tmp_path / "reports/suggestions.md").read_text(encoding="utf-8")
    assert "## 提案先: その他アリス" in sug and "朝の曲 / キャロル" in sug and "名前（aliases）だけの一致" in sug
    assert "今入っている: その他の曲" in sug
    assert "## 提案先: ボブチャンネル" in sug and "【cover】夜の曲 / ボブ" in sug and "今入っている: 夜の曲" in sug
    # 提案した曲を自分で入れて、また消した場合は、もう提案しない
    yt.pl["PLaliceOther"]["items"].append("c3")
    run(yt=yt, now=day(1, 12), **kw(tmp_path))
    yt.pl["PLaliceOther"]["items"].remove("c3")
    run(yt=yt, now=day(1, 13), **kw(tmp_path))
    sug = (tmp_path / "reports/suggestions.md").read_text(encoding="utf-8")
    assert "朝の曲 / キャロル" not in sug.split("## 提案先: その他アリス")[-1].split("##")[0] if "## 提案先: その他アリス" in sug else True


def test_suggestions_json(tmp_path):
    import json
    yt = setup(tmp_path)
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    yt.pl["PLmisc"]["items"].append("c3")
    run(yt=yt, now=day(1, 11), **kw(tmp_path))
    data = json.loads((tmp_path / "data/mix/suggestions.json").read_text(encoding="utf-8"))
    g = {x["name"]: x for x in data["groups"]}
    assert g["その他アリス"]["id"] == "PLaliceOther"
    item = g["その他アリス"]["items"][0]
    assert item["id"] == "c3" and item["in"] == ["その他の曲"] and "名前" in item["reason"]


class FakeRemote:
    """Worker の同期用 API の代わり。"""

    def __init__(self, decisions=(), fail=False):
        self.decisions = [dict(d) for d in decisions]
        self.acks = []
        self.fail = fail

    def fetch(self):
        from ytpl.decisions import RemoteError
        if self.fail:
            raise RemoteError("つながりません")
        return [dict(d) for d in self.decisions]

    def ack(self, results):
        from ytpl.decisions import RemoteError
        if self.fail:
            raise RemoteError("つながりません")
        self.acks.append(results)
        for r in results:
            for d in self.decisions:
                if (d["target"], d["video"], d["decided_at"]) == (r["target"], r["video"], r["decided_at"]):
                    d.update(applied_at=r["applied_at"], result=r["result"])
        return len(results)


def D_(target, video, action, at="2026-01-01T12:00:00Z", **k):
    return {"target": target, "video": video, "action": action, "decided_at": at, **k}


def test_decisions_from_worker_are_applied_next_run(tmp_path):
    """Worker の画面で「追加」にした曲は次の実行で入れ、「却下」にした曲はもう提案しない。"""
    import json
    yt = setup(tmp_path)
    run(yt=yt, now=day(1, 10), remote=None, **kw(tmp_path))
    yt.pl["PLmisc"]["items"].append("c3")
    run(yt=yt, now=day(1, 11), remote=None, **kw(tmp_path))
    sug = (tmp_path / "reports/suggestions.md").read_text(encoding="utf-8")
    assert "朝の曲 / キャロル" in sug and "【cover】夜の曲 / ボブ" in sug

    remote = FakeRemote([D_("PLaliceOther", "c3", "add", target_name="その他アリス", title="朝の曲 / キャロル"),
                         D_("PLbob", "b1", "reject")])
    text = run(yt=yt, now=day(1, 12), remote=remote, **kw(tmp_path))
    assert "c3" in titles(yt)["その他アリス"]
    assert "[提案から追加] その他アリス ← 朝の曲 / キャロル" in text
    assert "b1" not in titles(yt)["ボブチャンネル"]                       # 却下は追加しない
    sug = (tmp_path / "reports/suggestions.md").read_text(encoding="utf-8")
    assert "朝の曲 / キャロル" not in sug and "【cover】夜の曲 / ボブ" not in sug
    groups = json.loads((tmp_path / "data/mix/suggestions.json").read_text(encoding="utf-8"))["groups"]
    assert not any(it["id"] in ("b1", "c3") for g in groups for it in g["items"])
    results = {r["video"]: r["result"] for r in remote.acks[-1]}
    assert results == {"c3": "added", "b1": "recorded"}
    saved = load_yaml(tmp_path / "data/decisions.yaml")["decisions"]
    assert {(d["video"], d["result"]) for d in saved} == {("c3", "added"), ("b1", "recorded")}

    # 次の実行で同じ決定をもう一度読んでも、二重に追加しない。Worker が止まっていても却下は手元の記録で守られる
    n = len(titles(yt)["その他アリス"])
    run(yt=yt, now=day(1, 13), remote=FakeRemote(fail=True), **kw(tmp_path))
    assert len(titles(yt)["その他アリス"]) == n
    assert "【cover】夜の曲 / ボブ" not in (tmp_path / "reports/suggestions.md").read_text(encoding="utf-8")


def test_decisions_retry_ack_and_redecide(tmp_path):
    yt = setup(tmp_path)
    run(yt=yt, now=day(1, 10), remote=None, **kw(tmp_path))
    yt.pl["PLmisc"]["items"].append("c3")
    run(yt=yt, now=day(1, 11), remote=None, **kw(tmp_path))
    # 決定を読めたが、結果を返せなかった
    remote = FakeRemote([D_("PLbob", "b1", "reject")])
    real_ack = remote.ack
    remote.ack = lambda r: (_ for _ in ()).throw(__import__("ytpl.decisions").decisions.RemoteError("x"))
    text = run(yt=yt, now=day(1, 12), remote=remote, **kw(tmp_path))
    assert "反映結果を Worker に送れませんでした" in text
    remote.ack = real_ack
    run(yt=yt, now=day(1, 13), remote=remote, **kw(tmp_path))              # 次の実行で送り直す
    assert remote.acks and remote.acks[-1][0]["result"] == "recorded"
    # 却下を取り消した（Worker で決め直した）ら、また提案する
    remote.decisions = [D_("PLbob", "b1", "none", at="2026-01-02T00:00:00Z")]
    run(yt=yt, now=day(1, 14), remote=remote, **kw(tmp_path))
    assert "【cover】夜の曲 / ボブ" in (tmp_path / "reports/suggestions.md").read_text(encoding="utf-8")
    # 今の提案にない曲（Worker 側の記録が改ざんされた場合など）は入れない
    remote.decisions = [D_("PLaliceOther", "o2", "add", at="2026-01-02T12:00:00Z")]
    text = run(yt=yt, now=day(1, 14), remote=remote, **kw(tmp_path))
    assert "o2" not in titles(yt)["その他アリス"] and "今の提案にない曲です" in text
    assert remote.acks[-1][0]["result"] == "invalid"
    # 管理対象でない再生リストへの「追加」は行わない
    remote.decisions = [D_("PLunsorted", "b1", "add", at="2026-01-03T00:00:00Z")]
    text = run(yt=yt, now=day(1, 15), remote=remote, **kw(tmp_path))
    assert "b1" not in titles(yt)["未仕分け"]
    assert remote.acks[-1][0]["result"] == "invalid"


def test_handles_refreshed_daily_and_past_handles_kept(tmp_path):
    yt = setup(tmp_path)
    run(yt=yt, now=day(1, 10), remote=None, **kw(tmp_path))
    st = load_yaml(tmp_path / "data/state.yaml")
    assert st["handles"][A]["handle"] == "@alice_official"
    yt.handles = {**yt.handles, A: "@alice_new"}                          # ハンドルを変えた
    text = run(yt=yt, now=day(1, 11), remote=None, **kw(tmp_path))
    assert "@alice_official → @alice_new" in text
    st = load_yaml(tmp_path / "data/state.yaml")
    assert st["handles"][A]["handle"] == "@alice_new"
    assert [p["handle"] for p in st["handles"][A]["past"]] == ["@alice_official"]
    from ytpl.sync import handles_of
    assert handles_of(st)[A] == ["@alice_new", "@alice_official"]
    toks = rules.ref_tokens([A], handles_of(st))
    assert "@alice_official" in toks and "@alice_new" in toks             # 古いハンドルの言及も照合に使う
    # 同じ日のうちは取り直さない。見つからなかったチャンネルは前の記録を残す
    used = yt.used
    run(yt=yt, now=day(1, 11), remote=None, **kw(tmp_path))
    yt.missing_channels = {A}
    run(yt=yt, now=day(1, 12), remote=None, **kw(tmp_path))
    assert load_yaml(tmp_path / "data/state.yaml")["handles"][A]["handle"] == "@alice_new"
    assert used > 0


def test_legacy_rejected_playlist_still_honored(tmp_path):
    """（旧方式）config.yaml に「提案を却下」の id を書いていれば、そこに入れた曲は提案しない。"""
    cfg = CONFIG.replace('  misc: {id: "PLmisc", name: "その他の曲"}',
                         '  misc: {id: "PLmisc", name: "その他の曲"}\n  rejected: {id: "PLrej", name: "提案を却下"}')
    yt = setup(tmp_path, cfg)
    yt.pl["PLrej"] = {"title": "提案を却下", "items": ["c3"]}
    run(yt=yt, now=day(1, 10), remote=None, **kw(tmp_path))
    yt.pl["PLmisc"]["items"].append("c3")
    run(yt=yt, now=day(1, 11), remote=None, **kw(tmp_path))
    sug = (tmp_path / "reports/suggestions.md").read_text(encoding="utf-8")
    assert "朝の曲 / キャロル" not in sug and "【cover】夜の曲 / ボブ" in sug
    assert titles(yt)["提案を却下"] == ["c3"]


def test_rejected_playlist_not_created_automatically(tmp_path):
    yt = setup(tmp_path)
    run(yt=yt, now=day(1, 10), remote=None, **kw(tmp_path))
    assert "提案を却下" not in titles(yt)


def test_probe_channel_reports_sources(tmp_path):
    from ytpl.probe import probe_channel
    yt = setup(tmp_path)
    yt.v["r1"] = {**V("r1", "砂の歌", A, "01-02"), "release_only": True}
    yt.v["u1"] = {**V("u1", "風の歌", A, "01-02"), "privacy": "unlisted"}
    yt.releases = {A: {"OLAK5uy_rel1": ["r1"]}}
    yt.albums = {A: {"PLshelf": ["u1"]}}
    yt.channel_sections = lambda cid: [{"type": "multiplePlaylists", "title": "棚", "playlists": list(yt.albums.get(cid, {}))}]
    yt.search_channel_playlists = lambda cid: []
    before = {k: len(v["items"]) for k, v in yt.pl.items()}
    md = "\n".join(probe_channel(yt, A, ["r1", "zz"], [], search=False, max_pages=5))
    assert "OLAK5uy_rel1" in md and "4. 公開再生リスト（リリース）" in md and "3. 棚" in md
    assert "アップロード一覧にない本人の動画（2 件）" in md
    assert "- r1:" in md and "OLAK5uy_rel1" in md.split("- r1:")[1].split("\n")[1]
    assert "- zz: 動画情報を取得できません" in md
    assert {k: len(v["items"]) for k, v in yt.pl.items()} == before      # 何も書き込まない


def test_misc_decision_adds_to_misc_playlist(tmp_path):
    """「その他の曲に追加」にした曲は「その他の曲」に入れ、その提案先にはもう提案しない。"""
    yt = setup(tmp_path)
    run(yt=yt, now=day(1, 10), remote=None, **kw(tmp_path))
    run(yt=yt, now=day(1, 11), remote=None, **kw(tmp_path))
    assert "【cover】夜の曲 / ボブ" in (tmp_path / "reports/suggestions.md").read_text(encoding="utf-8")
    import json
    sj = json.loads((tmp_path / "data/mix/suggestions.json").read_text(encoding="utf-8"))
    assert sj["misc"] == {"id": "PLmisc", "name": "その他の曲"}
    remote = FakeRemote([D_("PLbob", "b1", "misc")])
    text = run(yt=yt, now=day(1, 12), remote=remote, **kw(tmp_path))
    assert "b1" in titles(yt)["その他の曲"] and "b1" not in titles(yt)["ボブチャンネル"]
    assert "[提案から追加] その他の曲 ← 【cover】夜の曲 / ボブ" in text
    assert remote.acks[-1][0]["result"] == "added"
    assert "【cover】夜の曲 / ボブ" not in (tmp_path / "reports/suggestions.md").read_text(encoding="utf-8")


def test_members_only_list_marks_videos(tmp_path):
    """メンバー限定の一覧（UUMO…）にある動画は、membership: true でなければ入れない。"""
    yt = setup(tmp_path)
    yt.v["m1"] = V("m1", "海の歌", A, "01-03")       # タイトルに表記はないが、メンバー限定の一覧にある
    yt.members_lists = {A: ["m1"]}
    run(yt=yt, now=day(1, 10), remote=None, **kw(tmp_path))
    assert "m1" not in titles(yt)["アリスチャンネル"]
    from ytpl.store import Catalog
    cat = Catalog(tmp_path / "data/catalog")
    assert cat.channels[A]["videos"]["m1"]["members"] is True
    assert cat.channels[B].get("members_missing") == "2026-01-10"        # 一覧がないチャンネルは7日ごとに試す


def test_unlisted_channels_are_added_to_ignore_channels(tmp_path, capsys):
    """管理対象の再生リストの動画の投稿チャンネルは、監視対象か ignore_channels のどちらかに書かれている状態にする。
    どちらにもないチャンネルは ignore_channels に「# 要確認」付きで追加する（消しても、どちらにもなければまた追加）。"""
    yt = setup(tmp_path)
    run(yt=yt, now=day(1, 10), remote=None, **kw(tmp_path))
    X, Y = C("newcomer"), C("ignored")
    yt.v["n1"] = V("n1", "新しい曲 / 新人X", X, "01-05", ch_title="新人X")
    yt.v["n2"] = V("n2", "別の曲 / 新人X", X, "01-05", ch_title="新人X")
    yt.v["y1"] = V("y1", "曲 / 無視Y", Y, "01-05", ch_title="無視Y")
    yt.pl["PLmisc"]["items"] += ["n1", "y1"]
    yt.pl["PLbob"]["items"].append("n2")
    cfg_path = tmp_path / "config.yaml"
    base = cfg_path.read_text(encoding="utf-8") + f'ignore_channels:\n  - {{id: "{Y}", name: "無視Y"}}\n'
    cfg_path.write_text(base, encoding="utf-8")
    text = run(yt=yt, now=day(1, 11), remote=None, **kw(tmp_path))
    assert "ignore_channels に追加したチャンネル" in text and f"新人X（{X}）" in text
    out = cfg_path.read_text(encoding="utf-8")
    assert out.count(X) == 1 and out.count(Y) == 1                    # 同じチャンネルは1回だけ。書いてあるものは足さない
    line = next(ln for ln in out.splitlines() if X in ln)
    assert line.strip().startswith(f'- {{id: "{X}", name: "新人X"}}') and "# 要確認（2026-01-11 自動追加：" in line
    assert rules.validate(Config(cfg_path)) == []
    # Worker の「要確認」画面用の一覧
    import json
    review = json.loads((tmp_path / "data/mix/review.json").read_text(encoding="utf-8"))["items"]
    assert {"kind": "channel", "section": "ignore_channels", "id": X, "name": "新人X",
            "note": "2026-01-11 自動追加：「その他の曲」の「新しい曲 / 新人X」"} in review
    assert not any(it["id"] == Y for it in review)                    # 「# 要確認」のないものは出さない
    from ytpl.store import Catalog
    run(yt=yt, now=day(1, 12), remote=None, **kw(tmp_path))
    assert X not in Catalog(tmp_path / "data/catalog").channels           # 無視するチャンネルは監視しない
    # config.yaml から消しただけなら、また追加する（ytpl.check でも警告）
    cfg_path.write_text(base, encoding="utf-8")
    from ytpl.check import check
    capsys.readouterr()
    check(cfg_path)
    assert "監視対象でも ignore_channels でもないチャンネル: 新人X" in capsys.readouterr().out
    run(yt=yt, now=day(1, 13), remote=None, **kw(tmp_path))
    assert X in cfg_path.read_text(encoding="utf-8")
    # 監視したいなら3分類に移す → 追加しない。翌日から監視し、監視外として記録していた曲もそのチャンネルの分になる
    moved = base.replace("occasional:", f'occasional:\n  - name: "新人X"\n    channels: [{{id: "{X}", name: "新人X"}}]')
    cfg_path.write_text(moved, encoding="utf-8")
    text = run(yt=yt, now=day(1, 14), remote=None, **kw(tmp_path))
    assert "ignore_channels に追加" not in text
    assert "n1" in Catalog(tmp_path / "data/catalog").channels[X]["videos"]
    capsys.readouterr()
    check(cfg_path)
    assert "監視対象でも ignore_channels でもない" not in capsys.readouterr().out


def test_unlisted_channels_already_in_playlists_are_added_on_first_run(tmp_path):
    """導入した日に、すでに入っている動画のチャンネルも対象になる。ignore_channels がなければ作る。"""
    yt = setup(tmp_path)
    yt.v["n1"] = V("n1", "新しい曲 / 新人X", C("newcomer"), "01-05", ch_title="新人X")
    yt.pl["PLmisc"]["items"].append("n1")
    text = run(yt=yt, now=day(1, 10), remote=None, **kw(tmp_path))
    assert "ignore_channels に追加したチャンネル" in text and "新人X" in text
    out = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "ignore_channels:" in out and rules.validate(Config(tmp_path / "config.yaml")) == []


def test_hand_add_when_quota_is_tight(tmp_path):
    """入りきらない日は「未仕分け」「その他の曲」の分を一時リストで渡し、空いたクォータをほかに使う。
    次のバックアップで入っていれば追加済みにし、監視外のチャンネルの自動追加の対象にもしない。"""
    import json
    yt = setup(tmp_path, CONFIG.replace("promotion:", "quota:\n  write_reserve: 0\npromotion:"))
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    yt.v["a6"] = V("a6", "『花の曲』アリス MV", A, "03-01")
    yt.v["d6"] = V("d6", "別の曲 / デイブ", D, "03-02")
    yt.v["d7"] = V("d7", "三つ目の曲 / デイブ", D, "03-02")
    yt.used, yt.budget = 0, 0
    yt.budget = 40 + 50 * 1                    # 書き込みは1件分だけ
    text = run(yt=yt, now=day(3, 10), **kw(tmp_path))
    t = titles(yt)
    assert "a6" in t["アリスチャンネル"]                 # 空いたクォータはほかの再生リストに使う
    assert "d6" not in t["未仕分け"] and "d7" not in t["未仕分け"]
    assert "手で追加してほしい曲" in text and "watch_videos?video_ids=" in text
    hand = json.loads((tmp_path / "data/mix/suggestions.json").read_text(encoding="utf-8"))["hand"]
    assert [(g["name"], sorted(it["id"] for it in g["items"])) for g in hand] == [("未仕分け", ["d6", "d7"])]
    from ytpl.store import Ledger
    led = Ledger(tmp_path / "data/playlists")
    assert led.entry("PLunsorted", "d6")["status"] == "pending" and led.entry("PLunsorted", "d6")["hand_since"]
    # 手で入れてもらった → 次の同期で追加済み。API では入れない
    yt.pl["PLunsorted"]["items"] += ["d6", "d7"]
    yt.used, yt.budget = 0, 10000
    text = run(yt=yt, now=day(3, 11), **kw(tmp_path))
    led = Ledger(tmp_path / "data/playlists")
    assert led.entry("PLunsorted", "d6")["status"] == "present" and led.entry("PLunsorted", "d6")["hand_done"]
    assert titles(yt)["未仕分け"].count("d6") == 1
    hand = json.loads((tmp_path / "data/mix/suggestions.json").read_text(encoding="utf-8"))["hand"]
    assert hand == []


def test_hand_add_always(tmp_path):
    yt = setup(tmp_path, CONFIG.replace("promotion:", 'hand_add:\n  mode: "always"\npromotion:'))
    run(yt=yt, now=day(1, 10), **kw(tmp_path))
    yt.v["d6"] = V("d6", "別の曲 / デイブ", D, "03-02")
    text = run(yt=yt, now=day(3, 10), **kw(tmp_path))
    assert "d6" not in titles(yt)["未仕分け"] and "手で追加してほしい曲" in text
