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


def test_combo_partner_by_channel(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("""
occasional:
  - name: キャロル
    channel_ids: [UCcarol, UCcarol_topic]
combos:
  - playlist: キャロルデイブ
    channel_ids: [UCcarol, UCcarol_topic]
    partner_channel_ids: [UCdave]
""", encoding="utf-8")
    cfg = Config(p)
    vs = [V("c1", "夜の曲 MV", ch="UCcarol", desc="with @dave"),
          V("c2", "夜の曲", ch="UCcarol_topic", ch_title="キャロル - Topic",
            desc="Provided to YouTube by X\n\nfeat. @dave"),
          V("c3", "朝の曲 MV", ch="UCcarol", desc="Dave と一緒に"),
          V("c4", "昼の曲", ch="UCcarol", desc="@dave_fan")]
    t = rules.build_targets(cfg, vs, rules.channel_roles(cfg), {"UCdave": "@dave"})
    assert t["キャロルデイブ"] == {"c1"}  # c2はc1と重複、c3は名前のみ、c4は別ハンドル


def test_promotion_counts_dedupe(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("""
promotion: {min_versions: 3}
occasional:
  - {name: A, channel_ids: [UCa, UCa_topic]}
  - {name: B, channel_ids: [UCb]}
""", encoding="utf-8")
    cfg = Config(p)
    vs = [V("1", "夜の曲 MV", ch="UCa"),
          V("2", "夜の曲", ch="UCa_topic", desc="Provided to YouTube by X"),
          V("3", "【cover】夜の曲 / B", ch="UCb")]
    roles = rules.channel_roles(cfg)
    assert rules.promotion_candidates(cfg, [{"title": "夜の曲 / B"}], vs, roles) == []
    vs.append(V("4", "夜の曲 (Remix)", ch="UCa"))
    assert len(rules.promotion_candidates(cfg, [{"title": "夜の曲 / B"}], vs, roles)) == 1
