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


def test_off_vocal_detection():
    for t in ["曲 (Instrumental)", "曲 (Off Vocal)", "曲 off vocal ver.", "曲 (Inst.)", "曲 カラオケ", "曲 オフボーカル"]:
        assert rules.is_off_vocal(t), t
    assert not rules.is_off_vocal("Instagram告知の曲")
    # オフボーカル表記はバージョンに含めない（通常版と同じ曲）
    assert rules.version_tag("曲 (Off Vocal)") == rules.version_tag("曲") == ""
    assert rules.version_tag("曲 (Remix) [Off Vocal]") == rules.version_tag("曲 (Remix Off Vocal)") == "remix"


def test_off_vocal_only_when_no_vocal(tmp_path):
    base = f"""
top_favorites:
  - name: "A"
    channels: [{{id: "{C('a')}", name: "A"}}, {{id: "{C('at')}", name: "A - Topic"}}]
    playlist: {{name: "Aチャンネル"}}
"""
    topic = dict(ch=C("at"), ch_title="A - Topic", desc="Provided to YouTube by Label")
    vs = [V("mv", "A『星の歌』MV", day="02"),
          V("ov", "星の歌 (Off Vocal)", day="03"),              # 通常版(mv)がある → 入れない
          V("rmx_ovt", "星の歌 (Remix) [Off Vocal]", day="01", **topic),
          V("rmx_ov", "星の歌 (Remix Off Vocal)", day="05"),   # Remixの通常版はない → 本チャンネル版を優先
          V("only_ovt", "風の歌 (Instrumental)", **topic)]     # 通常版がない → 入れる
    t, _ = targets(cfg_of(tmp_path, base), vs, {})
    assert t["Aチャンネル"].auto == {"mv"}                      # 既定では含めない
    t, _ = targets(cfg_of(tmp_path, base.replace('playlist: {name: "Aチャンネル"}',
                                                 'playlist: {name: "Aチャンネル"}\n    include_off_vocal: true')), vs, {})
    assert t["Aチャンネル"].auto == {"mv", "rmx_ov", "only_ovt"}


def test_off_vocal_vocal_found_elsewhere(tmp_path):
    """通常版が特別再生リストの条件に合わなくても、存在すればオフボーカルは入れない。"""
    cfg = cfg_of(tmp_path, f"""
top_favorites:
  - name: "A"
    channels: [{{id: "{C('a')}", name: "A"}}]
    specials:
      - playlist: {{name: "Aのインスト"}}
        keywords: ["off vocal", "instrumental"]
        include_off_vocal: true
""")
    vs = [V("mv", "星の歌 MV"), V("ov", "星の歌 (Off Vocal)"), V("ov2", "風の歌 (Instrumental)")]
    t, _ = targets(cfg, vs, {})
    assert t["Aのインスト"].auto == {"ov2"}


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
    other:
      playlist: {{id: "PLsingerOther", name: "その他歌い手A"}}
    specials:
      - playlist: {{name: "作曲者B×歌い手A"}}
        published_by: [{{id: "{C('composer')}", name: "作曲者B"}}]
favorites:
  - name: "作曲者B"
    channels: [{{id: "{C('composer')}", name: "作曲者B"}}]
    playlist: {{name: "作曲者Bチャンネル"}}
    other:
      playlist: {{id: "PLcomposerOther", name: "その他作曲者B"}}
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
    # published_by あり：作曲者Bの動画だけが対象。@ハンドル(b1)は自動、歌い手Aの aliases だけの一致(b4)は未仕分け
    assert t["作曲者B×歌い手A"].auto == {"b1"}
    assert t["作曲者B×歌い手A"].cand == {"b4"}
    assert t["その他歌い手A"].auto == set()                # 「その他○○」は常に判断
    assert t["その他歌い手A"].cand == {"x1", "x2"}         # 特別再生リスト分(b1, b4)は除外
    assert t["作曲者Bセルフカバー"].auto == {"b3"}
    assert t["作曲者Bチャンネル"].cand == {"b1", "b2", "b3", "b4"} and not t["作曲者Bチャンネル"].auto
    assert t["その他作曲者B"].cand == {"x1"}               # お気に入りの「その他」は判断
    assert t["歌い手D×歌い手E"].auto == {"d1"}             # @ハンドルで確認できたものは自動
    assert t["歌い手D×歌い手E"].cand == {"d2"}             # mention_aliases（名前）だけの一致は未仕分け
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
    other_auto: true
    other:
      playlist: {{name: "その他B"}}
songs:
  - name: 曲名
""", encoding="utf-8")
    errs = "\n".join(rules.validate(Config(p)))
    assert "combos" in errs and "id と name" in errs and "other_auto は廃止" in errs and "other.playlist: id が必要" in errs and "ダブルクオート" in errs


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
    assert rules.promotion_candidates(cfg, [{"title": "夜の曲 / B"}], rules.Index(cfg, vs), roles) == []
    vs.append(V("4", "夜の曲 (Remix)", ch=C("a")))
    assert len(rules.promotion_candidates(cfg, [{"title": "夜の曲 / B"}], rules.Index(cfg, vs), roles)) == 1


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


NAMES = f"""
top_favorites:
  - name: "歌い手A"
    channels:
      - {{id: "{C('singer')}", name: "歌い手A"}}
    aliases: ["歌い手A"]
    specials:
      - playlist: {{name: "B×A"}}
        published_by: ["作曲者B"]                 # 名前で指定（作曲者Bの channels すべて）
      - playlist: {{name: "B×A（Eも参加）"}}
        published_by: ["作曲者B"]
        mentions: ["歌い手E"]
      - playlist: {{name: "AとBの曲"}}
        published_by: ["歌い手A", "作曲者B"]      # 本人を含めれば本人の曲も対象
favorites:
  - name: "作曲者B"
    channels:
      - {{id: "{C('composer')}", name: "作曲者B"}}
      - {{id: "{C('composertopic')}", name: "作曲者B - Topic"}}
occasional:
  - name: "歌い手E"
    channels:
      - {{id: "{C('e')}", name: "歌い手E"}}
songs:
  - name: "曲X"
    official_channels: ["作曲者B"]
"""


def test_name_references_and_published_by_scope(tmp_path):
    cfg = cfg_of(tmp_path, NAMES)
    S, B, BT, E = C("singer"), C("composer"), C("composertopic"), C("e")
    h = {S: "@singer_a", B: "@composer_b", E: "@singer_e"}
    vs = [V("s1", "Aの曲 MV", ch=S),
          V("b1", "Bの曲 MV", ch=B, desc="Vocal: @singer_a"),
          V("bt1", "Bの別の曲", ch=BT, ch_title="作曲者B - Topic", desc="Provided to YouTube by X\nVocal: 歌い手A"),
          V("b2", "Bの曲2 MV", ch=B, desc="Vocal: someone"),
          V("b3", "Bの曲3 MV", ch=B, desc="Vocal: @singer_e"),
          V("x1", "曲X", ch=B)]
    t, _ = targets(cfg, vs, h)
    # 歌い手A本人の曲(s1)は対象外。作曲者BのTOPIC(bt1)も対象範囲だが、名前だけの一致なので未仕分け
    assert t["B×A"].auto == {"b1"} and t["B×A"].cand == {"bt1"}
    # mentions を書いた場合はその相手で確認（mention_aliases がないので歌い手Aの aliases も名前として使う）
    assert t["B×A（Eも参加）"].auto == {"b3"} and t["B×A（Eも参加）"].cand == {"bt1"}
    assert t["AとBの曲"].auto == {"s1", "b1"}           # 本人の曲は関係の確認なしで自動
    assert t["AとBの曲"].cand == {"bt1"}
    assert t["曲X"].auto == {"x1"}                     # official_channels も名前で指定できる
    roles = rules.channel_roles(cfg)
    assert roles[BT]["role"] == "favorite"


def test_name_reference_errors(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(NAMES.replace('published_by: ["作曲者B"]                 #', 'published_by: ["作曲者Z"]  #')
                 .replace("songs:\n", '  - name: "歌い手A"\n    channels: ["作曲者B"]\nsongs:\n'), encoding="utf-8")
    errs = "\n".join(rules.validate(Config(p)))
    assert "「作曲者Z」という name" in errs
    assert "name「歌い手A」が重複" in errs
    assert "名前による指定はできません" in errs


def test_check_script(tmp_path, capsys):
    from ytpl import check
    good = tmp_path / "good.yaml"
    from ytpl import fmt
    good.write_text(fmt.format_text(NAMES), encoding="utf-8")
    assert check.check(good) == 0
    assert "警告 0 件" in capsys.readouterr().out
    bad = tmp_path / "bad.yaml"
    bad.write_text('favorites:\n  - name: B\n    channels: []\n', encoding="utf-8")
    assert check.check(bad) == 1
    broken = tmp_path / "broken.yaml"
    broken.write_text('favorites:\n  - name: "B\n', encoding="utf-8")
    assert check.check(broken) == 1
    assert check.check(tmp_path / "none.yaml") == 0


SONGS = f"""
top_favorites:
  - name: "歌い手A"
    channels: [{{id: "{C('singer')}", name: "歌い手A"}}]
occasional:
  - name: "バンドK"
    channels: [{{id: "{C('bandk')}", name: "バンドK"}}]
  - name: "番組F"
    channels: [{{id: "{C('bangumif')}", name: "番組F"}}]
  - name: "作曲者H"
    channels: [{{id: "{C('sakkyokushah')}", name: "作曲者H"}}]
  - name: "歌手N"
    channels: [{{id: "{C('kashun')}", name: "歌手N"}}]
  - name: "バンドP"
    channels: [{{id: "{C('bandp')}", name: "バンドP"}}]
songs:
  - name: "KING"
  - name: "ダーリン"
  - name: "アイドル"
  - name: "テトリス"
  - name: "プロポーズ"
    original_artists: ["歌手N"]
"""


def test_song_matching_reported_examples(tmp_path):
    """曲名の部分一致・アーティスト名への一致・同名の別曲（ご指摘の例）。"""
    cfg = cfg_of(tmp_path, SONGS)
    S, K, F, H, N, P = (C(x) for x in ("singer", "bandk", "bangumif", "sakkyokushah", "kashun", "bandp"))
    vs = [
        V("k0", "【歌ってみた】KING / 歌い手A", ch=S),
        V("k1", "KingDom", ch=S),
        V("k2", "逆夢 - バンドK", ch=K, ch_title="バンドK"),
        V("k3", "歌手C & 歌手D feat.メンバーK1, メンバーK2 from バンドK - BABY GANGSTA / 番組F / 番組F",
          ch=F, ch_title="番組F"),
        V("k4", "Side Quest King (編曲者R remix) feat. 合成音声M", ch=S),
        V("d0", "【歌ってみた】ダーリン / 歌い手A", ch=S),
        V("d1", "おねがいダーリン", ch=S), V("d2", "ダーリンゲームオーバーラブ", ch=S), V("d3", "ダーリンダンス", ch=S),
        V("i0", "アイドル / 歌い手A(cover)", ch=S),
        V("i1", "アイドルパワー", ch=S), V("i2", "咲き誇れアイドル", ch=S),
        V("t1", "『「せや！ワイらでテトリス歌ったろ！」Remix by 作曲者H / 作曲者H / Sakkyokusha H』", ch=H, ch_title="作曲者H"),
        V("p0", "歌手N - プロポーズ", ch=N, ch_title="歌手N"),
        V("p1", "バンドP「プロポーズ」MV", ch=P, ch_title="バンドP"),
        V("p2", "【歌ってみた】プロポーズ / 歌い手A", ch=S, desc="原曲: 歌手N"),
        V("p3", "【歌ってみた】プロポーズ / 歌い手A", ch=S, desc="原曲: バンドP", day="02"),
    ]
    t, idx = targets(cfg, vs, {})
    assert t["KING"].auto == {"k0"}
    assert t["KING"].cand == {"k4"}                    # 単語として含む → 確認。KingDom・バンドK には一致しない
    assert t["ダーリン"].auto == {"d0"}
    assert t["ダーリン"].cand == {"d1", "d2", "d3"}     # 部分一致は自動では入れない
    assert t["アイドル"].auto == {"i0"} and t["アイドル"].cand == {"i1", "i2"}
    assert t["テトリス"].auto == set() and t["テトリス"].cand == {"t1"}   # 曲名の中の部分一致 → 確認
    assert t["プロポーズ"].auto == {"p0", "p2"}        # 歌手N本人・歌手Nが原曲と書かれたカバー
    assert t["プロポーズ"].cand == {"p1"}              # 同名の別の曲（バンドP）は確認へ
    # p3（バンドPのカバー）は、同じ歌い手の同名の動画 p2 と重複扱いになり、どちらにも入らない
    # 重複判定：同じアーティストの別の曲を同じ曲とみなさない
    assert idx.r["k2"].key[0] == "逆夢"
    assert rules.dedupe({"p0"}, idx, lambda c: c) == {"p0"}


def test_promotion_does_not_promote_artist_names(tmp_path):
    cfg = cfg_of(tmp_path, SONGS.replace("songs:", "promotion: {min_versions: 2}\nsongs:"))
    K, F = C("bandk"), C("bangumif")
    vs = [V("a", "バンドK - 白日", ch=K, ch_title="バンドK"),
          V("b", "バンドK - 逆夢", ch=K, ch_title="バンドK"),
          V("c", "バンドK - 白日 / 番組F", ch=F, ch_title="番組F")]
    idx = rules.Index(cfg, vs)
    out = rules.promotion_candidates(cfg, [{"video_id": "b", "title": "バンドK - 逆夢"},
                                           {"video_id": "a", "title": "バンドK - 白日"}], idx, rules.channel_roles(cfg))
    assert [c["name"] for c in out] == ["白日"]          # アーティスト名（バンドK）は昇格しない
    assert idx.r["a"].key != idx.r["b"].key              # 別の曲を同じ曲とみなさない


def test_unmonitored_or_past_handle_mentions_need_review(tmp_path):
    """監視外のチャンネルの動画や、過去の @ハンドルだけで一致した動画は、自動では入れず確認に回す。"""
    A, B, X = C("a"), C("b"), C("stranger")
    cfg = cfg_of(tmp_path, f"""
top_favorites:
  - name: "A"
    channels: [{{id: "{A}", name: "A"}}]
    specials:
      - playlist: {{name: "Aの関係曲"}}
favorites:
  - name: "B"
    channels: [{{id: "{B}", name: "B"}}]
""")
    vs = [V("own", "Aの曲", A),
          V("b_now", "曲1 / B", B, desc="with @a_now"),         # 監視しているチャンネルが今のハンドルを書いた → 自動
          V("b_old", "曲2 / B", B, desc="with @a_old"),         # 過去のハンドルだけ → 確認
          V("x_now", "曲3 / X", X, desc="with @a_now"),         # 監視外のチャンネル → 確認
          V("x_id", "曲4 / X", X, desc=f"youtube.com/channel/{A}")]
    t, _ = targets(cfg, vs, {A: ["@a_now", "@a_old"]})
    sp = t["Aの関係曲"]
    assert sp.auto == {"own", "b_now"}
    assert sp.cand == {"b_old", "x_now", "x_id"}
    assert sp.why["b_old"] == "過去の @ハンドルだけで一致"
    assert "監視していないチャンネル" in sp.why["x_now"]


def test_off_vocal_key_with_dash_marks(tmp_path):
    """「曲名 -instrumental-」も通常版と同じ曲として扱い、通常版があれば入れない。"""
    A, AT = C("a"), C("atopic")
    for t in ["曲A -instrumental-", "曲A -Instrumental ver.-", "曲A ~off vocal~", "曲A（Instrumental）"]:
        assert rules.song_key({"title": t, "channel_title": "A - Topic"})[0] == "曲a", t
    cfg = cfg_of(tmp_path, f"""
top_favorites:
  - name: "A"
    channels: [{{id: "{A}", name: "A"}}, {{id: "{AT}", name: "A - Topic"}}]
    playlist: {{name: "Aチャンネル"}}
    include_off_vocal: true
""")
    art = "Provided to YouTube by Label"
    vs = [V("n", "曲A", AT, desc=art, ch_title="A - Topic"), V("i", "曲A -instrumental-", AT, desc=art, ch_title="A - Topic"),
          V("i2", "曲B -instrumental-", AT, desc=art, ch_title="A - Topic")]
    t, _ = targets(cfg, vs, {})
    assert t["Aチャンネル"].auto == {"n", "i2"}       # 通常版のない曲Bのオフボーカルだけ入れる


def test_topic_track_skipped_when_credited_official_video_exists(tmp_path):
    """別のチャンネルが出した、この人がクレジットされた公式の動画と同じ曲のTOPIC音源は入れない。
    同じ曲名でも、この人がクレジットされていない別の人の版（ボカロ版など）やカバーでは判定しない。"""
    A, AT, P, Q = C("a"), C("atopic"), C("project"), C("other")
    cfg = cfg_of(tmp_path, f"""
top_favorites:
  - name: "歌手A"
    aliases: ["歌手A"]
    channels: [{{id: "{A}", name: "歌手A"}}, {{id: "{AT}", name: "歌手A - Topic"}}]
    playlist: {{name: "Aチャンネル"}}
favorites:
  - name: "企画P"
    channels: [{{id: "{P}", name: "企画P"}}]
  - name: "作者Q"
    channels: [{{id: "{Q}", name: "作者Q"}}]
""")
    art = "Provided to YouTube by Label"
    vs = [V("mv", "「曲X」feat.歌手A / 企画P", P, ch_title="企画P"),            # 公式の動画（タイトルでクレジット）
          V("tx", "曲X", AT, desc=art, ch_title="歌手A - Topic"),
          V("q", "曲Y / 作者Q feat.歌手B", Q, ch_title="作者Q"),               # 別の人の版
          V("ty", "曲Y", AT, desc=art, ch_title="歌手A - Topic"),
          V("cv", "曲Z / 歌手A (cover)", Q, desc="@a_now", ch_title="作者Q"),   # カバーは判定に使わない
          V("tz", "曲Z", AT, desc=art, ch_title="歌手A - Topic")]
    t, _ = targets(cfg, vs, {A: ["@a_now"]})
    assert t["Aチャンネル"].auto == {"ty", "tz"}


def test_members_only_videos(tmp_path):
    A, B = C("a"), C("b")
    cfg = cfg_of(tmp_path, f"""
top_favorites:
  - name: "A"
    channels: [{{id: "{A}", name: "A"}}]
    playlist: {{name: "Aチャンネル"}}
    membership: true
  - name: "B"
    channels: [{{id: "{B}", name: "B"}}]
    playlist: {{name: "Bチャンネル"}}
""")
    vs = [V("a1", "【メン限】曲1", A), V("a2", "メン限｜新曲「曲2」先行公開", A), V("a3", "曲3", A),
          {**V("a4", "曲4", A), "members": True},
          V("b1", "【メンバー限定】曲5", B), {**V("b2", "曲6", B), "members": True}, V("b3", "曲7", B)]
    t, idx = targets(cfg, vs, {})
    assert t["Aチャンネル"].auto == {"a1", "a3", "a4"}       # 加入しているチャンネル：先行公開以外は入れる
    assert t["Bチャンネル"].auto == {"b3"}                   # 加入していないチャンネル：メンバー限定は入れない
    assert idx.members_skipped == {"a2", "b1", "b2"}
    bad = tmp_path / "bad.yaml"
    bad.write_text(f'top_favorites:\n  - name: "A"\n    channels: [{{id: "{A}", name: "A"}}]\n    membership: "yes"\n',
                   encoding="utf-8")
    assert any("membership" in e for e in rules.validate(Config(bad)))


def test_other_exclude_channels(tmp_path):
    """別名義・所属ユニットのチャンネルの動画は「その他○○」の候補にしない。"""
    A, U, X = C("a"), C("unit"), C("x")
    cfg = cfg_of(tmp_path, f"""
top_favorites:
  - name: "A"
    channels: [{{id: "{A}", name: "A"}}]
    other:
      playlist: {{id: "PLotherAxxxxxxxxxxxxxxxxxxxxxxxxxx", name: "その他A"}}
      exclude_channels: ["ユニットU"]
favorites:
  - name: "ユニットU"
    channels: [{{id: "{U}", name: "ユニットU"}}]
  - name: "X"
    channels: [{{id: "{X}", name: "X"}}]
""")
    vs = [V("u1", "曲1 / ユニットU", U, desc="vo. @a_now", ch_title="ユニットU"),
          V("x1", "曲2 / X", X, desc="vo. @a_now", ch_title="X")]
    t, _ = targets(cfg, vs, {A: ["@a_now"]})
    assert t["その他A"].cand == {"x1"}


def test_song_partial_match_needs_evidence_when_configured(tmp_path):
    """部分一致は、原曲の手がかり（original_artists など）を書いた曲では、手がかりがあるときだけ確認候補。
    タイトルの並びは人によって違うので、区切りの位置では判定しない。"""
    A = C("a")
    cfg = cfg_of(tmp_path, f"""
favorites:
  - name: "A"
    channels: [{{id: "{A}", name: "A"}}]
songs:
  - name: "KAZE"
    original_artists: ["作者Z"]
  - name: "SORA"
""")
    vs = [V("c1", "別の曲 - KAZE Band / A (cover)", A),        # アーティスト名に曲名が含まれる（手がかりなし）
          V("c2", "A / KAZE Band - 別の曲 (cover)", A),        # 並びが違っても同じ
          V("c3", "KAZE Remix Party / 作者Z / A", A),           # 部分一致＋手がかり → 確認
          V("c4", "KAZE / 作者Z (cover) A", A),                 # 完全一致＋手がかり → 自動
          V("s1", "A / SORA Band - 別の曲 (cover)", A)]          # 手がかりを書いていない曲は、部分一致を確認候補に
    t, _ = targets(cfg, vs, {})
    assert t["KAZE"].auto == {"c4"} and t["KAZE"].cand == {"c3"}
    assert t["SORA"].cand == {"s1"}


def test_official_channel_names_as_original_artists(tmp_path):
    """official_channels のチャンネル名（「Official」などを除いたもの）も原曲のアーティスト名として使う。
    英数字の名前は単語の途中には一致させない。2文字以下の名前は自動では使わない。"""
    A, B, D, O, S = C("a"), C("b"), C("d"), C("official"), C("short")
    cfg = cfg_of(tmp_path, f"""
favorites:
  - name: "A"
    channels: [{{id: "{A}", name: "A"}}]
  - name: "B"
    channels: [{{id: "{B}", name: "B"}}]
  - name: "D"
    channels: [{{id: "{D}", name: "D"}}]
songs:
  - name: "SORA"
    official_channels: [{{id: "{O}", name: "Zeta Official"}}]
  - name: "UMI"
    original_artists: ["Ado"]
  - name: "MORI"
    official_channels: [{{id: "{S}", name: "AB"}}]
""")
    vs = [V("s1", "SORA / zeta (cover) A", A),               # 公式チャンネル名（Official を除く）→ 手がかりあり
          V("s2", "SORA (cover) B", B, ch_title="B"),         # 手がかりなし（同じ人の重複除去を避けるため別チャンネル）
          V("s3", "SORA / Zetaman (cover) D", D, ch_title="D"),   # 単語の途中には一致させない
          V("u1", "UMI - Ado (cover) A", A),
          V("u2", "UMI / B", B, desc="Adobe Premiere で編集", ch_title="B"),
          V("m1", "MORI / AB (cover) A", A)]                  # 2文字の名前は自動では使わない
    t, _ = targets(cfg, vs, {})
    assert t["SORA"].auto == {"s1"} and t["SORA"].cand == {"s2", "s3"}
    assert t["UMI"].auto == {"u1"} and t["UMI"].cand == {"u2"}
    assert t["MORI"].auto == set() and t["MORI"].cand == {"m1"}
    assert rules.official_names({"official_channels": [{"id": O, "name": "Zeta Official"}]}, {O}, rules.Index(cfg, vs), cfg) == ["zeta"]
