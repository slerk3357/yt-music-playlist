from ytpl import rules
from ytpl.store import Config


def V(id, title, ch="UCa", desc="", ch_title="A", day="01"):
    return {"id": id, "title": title, "channel_id": ch, "channel_title": ch_title,
            "description": desc, "published_at": f"2026-01-{day}T00:00:00Z", "category": "10",
            "duration": 240, "live": "none"}


def test_version_tag():
    assert rules.version_tag("アリス『星の歌』Official Music Video") == ""
    assert rules.version_tag("星の歌") == ""
    assert rules.version_tag("【歌ってみた】星の歌 / ボブ") == ""
    assert rules.version_tag("星の歌 (Remix)") == "remix"
    assert rules.version_tag("星の歌 (Acoustic Ver.)") == rules.version_tag("星の歌 - Acoustic Version")
    assert rules.version_tag("星の歌 (X Remix)") != rules.version_tag("星の歌 (Y Remix)")


def test_dedupe_prefers_mv_and_keeps_versions():
    vs = [
        V("art", "星の歌", ch="UCa_topic", ch_title="A - Topic", desc="Provided to YouTube by Label", day="01"),
        V("mv", "アリス『星の歌』Official Music Video", day="02"),
        V("remix", "星の歌 (Remix)", ch="UCa_topic", ch_title="A - Topic", desc="Provided to YouTube by Label"),
        V("ac", "星の歌 (Acoustic Ver.)", day="03"),
        V("cover", "【歌ってみた】星の歌 / ボブ", ch="UCbob"),
    ]
    ent = {"UCa": "A", "UCa_topic": "A"}
    keep = rules.dedupe({v["id"] for v in vs}, {v["id"]: v for v in vs}, lambda c: ent.get(c) or c)
    assert keep == {"mv", "remix", "ac", "cover"}


def test_channel_ref_matching():
    refs = rules.channel_refs(["UCalice"], {"UCalice": "@alice_official"})
    assert rules.matches_channel_ref("Vocal: @Alice_Official", refs)
    assert rules.matches_channel_ref("https://www.youtube.com/channel/UCalice", refs)
    assert not rules.matches_channel_ref("Vocal: @alice_official2", refs)
    assert not rules.matches_channel_ref("Vocal: Alice", refs)


def cfg_of(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    return Config(p)


def test_special_partner_by_channel(tmp_path):
    cfg = cfg_of(tmp_path, """
occasional:
  - name: キャロル
    channel_ids: [UCcarol, UCcarol_topic]
    specials:
      - playlist: キャロルデイブ
        mentions: [UCdave]
""")
    vs = [V("c1", "夜の曲 MV", ch="UCcarol", desc="with @dave"),
          V("c2", "夜の曲", ch="UCcarol_topic", ch_title="キャロル - Topic",
            desc="Provided to YouTube by X\n\nfeat. @dave"),
          V("c3", "朝の曲 MV", ch="UCcarol", desc="Dave と一緒に"),
          V("c4", "昼の曲", ch="UCcarol", desc="@dave_fan")]
    t = rules.build_targets(cfg, vs, rules.channel_roles(cfg), {"UCdave": "@dave"})
    assert t.auto["キャロルデイブ"] == {"c1"}  # c2はc1と重複、c3は名前のみ、c4は別ハンドル
    assert "キャロルチャンネル" not in t.titles()  # たまに聴くチャンネルは既定で○○チャンネルなし


NAKURU = """
top_favorites:
  - name: 藍月なくる
    channel_ids: [UCnaku, UCnaku_topic]
    specials:
      - playlist: Imyなくちゃ
        published_by: [UCimy]
      - playlist: なくるセルフカバー
        published_by: [UCnaku, UCnaku_topic]
        keywords: [self cover, セルフカバー]
favorites:
  - name: Imy
    channel_ids: [UCimy]
    other: true
"""


def test_specials_excluded_from_other(tmp_path):
    cfg = cfg_of(tmp_path, NAKURU)
    h = {"UCnaku": "@nakuru", "UCimy": "@imy"}
    vs = [V("n1", "なくるの曲 MV", ch="UCnaku"),
          V("n2", "なくるの曲 (Instrumental)", ch="UCnaku"),
          V("n3", "昔の曲 / 藍月なくる (Self Cover)", ch="UCnaku"),
          V("i1", "Imyの曲 MV", ch="UCimy", desc="Vocal: @nakuru"),
          V("i2", "Imyの別の曲 MV", ch="UCimy", desc="Vocal: someone"),
          V("x1", "他の人の曲", ch="UCx", desc="Vocal @nakuru / Music @imy")]
    t = rules.build_targets(cfg, vs, rules.channel_roles(cfg), h)
    assert t.auto["藍月なくるチャンネル"] == {"n1", "n3"}     # インストは除外
    assert t.auto["なくるセルフカバー"] == {"n3"}
    assert t.auto["Imyなくちゃ"] == {"i1"}
    assert t.auto["その他藍月なくる"] == {"x1"}              # 特別再生リスト分(i1)は除外
    # お気に入りのチャンネルの○○チャンネル・その他○○は「未仕分け」経由（manual）
    assert t.manual["Imyチャンネル"] == {"i1", "i2"}
    assert t.manual["その他Imy"] == {"x1"}
    roles = rules.channel_roles(cfg)
    x1 = next(v for v in vs if v["id"] == "x1")
    assert rules.needs_unsorted(cfg, x1, roles, *t.hits("x1"), set())   # その他Imy の候補
    n1 = vs[0]
    assert not rules.needs_unsorted(cfg, n1, roles, *t.hits("n1"), set())


def test_legacy_combos_rejected(tmp_path):
    cfg = cfg_of(tmp_path, "combos:\n  - playlist: x\n")
    assert rules.validate(cfg)


def test_promotion_counts_dedupe(tmp_path):
    cfg = cfg_of(tmp_path, """
promotion: {min_versions: 3}
occasional:
  - {name: A, channel_ids: [UCa, UCa_topic]}
  - {name: B, channel_ids: [UCb]}
""")
    vs = [V("1", "夜の曲 MV", ch="UCa"),
          V("2", "夜の曲", ch="UCa_topic", desc="Provided to YouTube by X"),
          V("3", "【cover】夜の曲 / B", ch="UCb")]
    roles = rules.channel_roles(cfg)
    assert rules.promotion_candidates(cfg, [{"title": "夜の曲 / B"}], vs, roles) == []
    vs.append(V("4", "夜の曲 (Remix)", ch="UCa"))
    assert len(rules.promotion_candidates(cfg, [{"title": "夜の曲 / B"}], vs, roles)) == 1
