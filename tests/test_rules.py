from ytpl import rules
from ytpl.store import Config


def C(name: str) -> str:
    return "UC" + name.ljust(22, "0")[:22]


def V(id, title, ch=C("a"), desc="", ch_title="A", day="01"):
    return {"id": id, "title": title, "channel_id": ch, "channel_title": ch_title,
            "description": desc, "published_at": f"2026-01-{day}T00:00:00Z", "category": "10",
            "duration": 240, "live": "none"}


def cfg_of(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    cfg = Config(p)
    assert rules.validate(cfg) == []
    return cfg


def targets(cfg, vs, handles):
    idx = rules.Index(cfg, vs)
    return {t.pl.name: t for t in rules.build_targets(cfg, idx, rules.channel_roles(cfg), handles)}, idx


def test_version_tag():
    assert rules.version_tag("A『星の歌』Official Music Video") == ""
    assert rules.version_tag("星の歌") == ""
    assert rules.version_tag("【歌ってみた】星の歌 / B") == ""
    assert rules.version_tag("星の歌 (Remix)") == "remix"
    assert rules.version_tag("星の歌 (Acoustic Ver.)") == rules.version_tag("星の歌 - Acoustic Version")
    assert rules.version_tag("星の歌 (X Remix)") != rules.version_tag("星の歌 (Y Remix)")


def test_builtin_exclusions():
    m = {"exclude_keywords": [], "include_keywords": [], "categories": ["10"], "min_seconds": 61, "max_seconds": 900}
    for t in ["曲 (Instrumental)", "曲 (Off Vocal)", "曲 off vocal ver.", "曲 (Inst.)", "曲 カラオケ", "曲 オフボーカル"]:
        assert not rules.is_music(V("x", t), m), t
    assert rules.is_music(V("x", "Instagram告知の曲"), m)


def test_dedupe_prefers_mv_and_keeps_versions(tmp_path):
    cfg = cfg_of(tmp_path, f"""
top_favorites:
  - name: "A"
    channels: [{{id: "{C('a')}", name: "A"}}, {{id: "{C('at')}", name: "A - Topic"}}]
    playlist: {{name: "Aチャンネル"}}
""")
    topic = dict(ch=C("at"), ch_title="A - Topic", desc="Provided to YouTube by Label")
    vs = [V("art", "星の歌", day="01", **topic),
          V("mv", "A『星の歌』Official Music Video", day="02"),
          V("remix", "星の歌 (Remix)", **topic),
          V("ac", "星の歌 (Acoustic Ver.)", day="03")]
    t, _ = targets(cfg, vs, {})
    assert t["Aチャンネル"].auto == {"mv", "remix", "ac"}


def test_channel_ref_index(tmp_path):
    cfg = cfg_of(tmp_path, "{}")
    vs = [V("1", "曲", desc="Vocal: @Singer_Official."), V("2", "曲", desc="Vocal: @singer_official2"),
          V("3", "曲", desc="https://www.youtube.com/channel/" + C("singer")), V("4", "曲", desc="Vocal: Singer")]
    idx = rules.Index(cfg, vs)
    toks = rules.ref_tokens([C("singer")], {C("singer"): "@singer_official"})
    assert idx.mentioning(toks) == {"1", "3"}


SPEC = f"""
top_favorites:
  - name: "歌い手A"
    channels:
      - {{id: "{C('singer')}", name: "歌い手A"}}
      - {{id: "{C('singertopic')}", name: "歌い手A - Topic"}}
    aliases: ["歌い手A"]
    playlist: {{name: "歌い手Aチャンネル"}}
    other_playlist: {{id: "PLsingerOther", name: "その他歌い手A"}}
    specials:
      - playlist: {{name: "作曲者B×歌い手A"}}
        published_by: [{{id: "{C('composer')}", name: "作曲者B"}}]
favorites:
  - name: "作曲者B"
    channels: [{{id: "{C('composer')}", name: "作曲者B"}}]
    playlist: {{name: "作曲者Bチャンネル"}}
    other_playlist: {{id: "PLcomposerOther", name: "その他作曲者B"}}
    specials:
      - playlist: {{name: "作曲者Bセルフカバー"}}
        published_by: [{{id: "{C('composer')}", name: "作曲者B"}}]
        keywords: ["self cover", "セルフカバー"]
occasional:
  - name: "歌い手D"
    channels: [{{id: "{C('d')}", name: "歌い手D"}}]
    specials:
      - playlist: {{name: "歌い手D×歌い手E"}}
        mentions: [{{id: "{C('e')}", name: "歌い手E"}}]
        mention_aliases: ["歌い手E"]
"""


def test_specials_and_other(tmp_path):
    cfg = cfg_of(tmp_path, SPEC)
    h = {C("singer"): "@singer_a", C("composer"): "@composer_b", C("e"): "@singer_e"}
    S, B, X, D = C("singer"), C("composer"), C("x"), C("d")
    vs = [V("s1", "歌い手Aの曲 MV", ch=S),
          V("b1", "作曲者Bの曲 MV", ch=B, desc="Vocal: @singer_a"),
          V("b2", "作曲者Bの別の曲 MV", ch=B, desc="Vocal: someone"),
          V("b3", "昔の曲 (Self Cover)", ch=B),
          V("b4", "作曲者Bの新曲", ch=B, desc="Vocal: 歌い手A"),
          V("x1", "他の人の曲", ch=X, desc="Vocal @singer_a / Music @composer_b"),
          V("x2", "他の人の曲2", ch=X, desc="Vocal: 歌い手A"),
          V("d1", "Dの曲", ch=D, desc="with @singer_e"),
          V("d2", "Dの曲2", ch=D, desc="with 歌い手E"),
          V("d3", "Dの曲3", ch=D, desc="solo")]
    t, idx = targets(cfg, vs, h)
    assert t["歌い手Aチャンネル"].auto == {"s1"}
    assert t["作曲者B×歌い手A"].auto == {"b1"}             # @ハンドルで確認できた
    assert t["作曲者B×歌い手A"].cand == {"b4"}             # 名前（aliases）だけ → 未仕分けで確認
    assert t["その他歌い手A"].auto == {"x1"}               # 特別再生リスト分(b1)は除外
    assert t["その他歌い手A"].cand == {"x2"}
    assert t["作曲者Bセルフカバー"].auto == {"b3"}
    assert t["作曲者Bチャンネル"].cand == {"b1", "b2", "b3", "b4"} and not t["作曲者Bチャンネル"].auto
    assert t["その他作曲者B"].cand == {"x1"}               # お気に入りの「その他」は判断
    assert t["歌い手D×歌い手E"].auto == {"d1", "d2"}       # mention_aliases は信用して自動
    roles = rules.channel_roles(cfg)
    assert rules.needs_unsorted("d3", roles, idx, 0, 0)         # たまに聴く・どこにも入らない
    assert not rules.needs_unsorted("s1", roles, idx, 1, 0)


def test_validate_errors(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(f"""
combos: [{{playlist: "x"}}]
favorites:
  - name: "B"
    channels: [{{id: "{C('b')}"}}]
    other_playlist: {{name: "その他B"}}
songs:
  - name: 曲名
""", encoding="utf-8")
    errs = "\n".join(rules.validate(Config(p)))
    assert "combos" in errs and "id と name" in errs and "other_playlist は id" in errs and "ダブルクオート" in errs


def test_promotion_counts_dedupe(tmp_path):
    cfg = cfg_of(tmp_path, f"""
promotion: {{min_versions: 3}}
occasional:
  - {{name: "A", channels: [{{id: "{C('a')}", name: "A"}}, {{id: "{C('at')}", name: "A - Topic"}}]}}
  - {{name: "B", channels: [{{id: "{C('b')}", name: "B"}}]}}
""")
    vs = [V("1", "夜の曲 MV", ch=C("a")),
          V("2", "夜の曲", ch=C("at"), desc="Provided to YouTube by X"),
          V("3", "【cover】夜の曲 / B", ch=C("b"))]
    roles = rules.channel_roles(cfg)
    assert rules.promotion_candidates(cfg, ["夜の曲 / B"], rules.Index(cfg, vs), roles) == []
    vs.append(V("4", "夜の曲 (Remix)", ch=C("a")))
    assert len(rules.promotion_candidates(cfg, ["夜の曲 / B"], rules.Index(cfg, vs), roles)) == 1


def test_index_scales():
    import time
    from ytpl.store import Config as Cfg
    cfg = Cfg("/nonexistent.yaml")
    vs = [V(str(i), f"曲{i % 5000} / 歌い手{i % 300}", ch=C(f"c{i % 300}"), desc=f"@h{i % 700}")
          for i in range(30000)]
    t0 = time.time()
    idx = rules.Index(cfg, vs)
    idx.mentioning({"@h5"})
    assert time.time() - t0 < 10
