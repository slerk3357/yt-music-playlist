"""動画タイトルから「曲名らしい部分」を取り出す。

タイトルは「曲名 / アーティスト」「アーティスト - 曲名」「【歌ってみた】曲名 / 歌い手」
「曲名 (Official Video)」「曲名 feat. ゲスト」などさまざまなので、次の手順で分解する。

  1. 「」『』で囲まれた部分は、曲名の可能性が高いものとして先頭に置く
  2. 【】[]() などの括弧の中身は取り除く（Official Video、Remix などの注記）
  3. 区切り（/ | - ~ :）で分割する
  4. 各部分から feat. / ft. / prod. / remix by などの後ろ（参加者の表記）を取り除く
  5. MV・歌ってみた・cover などの決まり文句を取り除く
  6. 既知のアーティスト名（チャンネル名・設定に書いた名前）と同じ部分を取り除く

残った部分を「曲名の候補」とする。曲名の照合・重複判定・昇格はこの候補を使い、
タイトル全体の部分一致は使わない（アーティスト名や別の曲名に一致してしまうため）。
"""
from __future__ import annotations

import re
import unicodedata

QUOTE_RE = re.compile(r"[「『“]([^」』”]+)[」』”]")
BRACKET_RE = re.compile(r"【[^】]*】|\[[^\]]*\]|\([^)]*\)|〔[^〕]*〕|《[^》]*》|<[^>]*>|#\S+")
SPLIT_RE = re.compile(r"\s*[/|]\s*|\s+[-–—―−]\s+|\s*[~〜]\s*|\s+:\s+|\s*[｜]\s*")
CREDIT_RE = re.compile(
    r"\s*(?:\bfeat\b\.?|\bft\b\.?|\bfeaturing\b|\bprod\b\.?|\bproduced by\b|"
    r"\b(?:covered|cover|remix(?:ed)?|arranged|mixed|sung)\s+by\b|×).*$", re.I)
NOISE_RE = re.compile(
    r"(?i)\b(?:official\s+(?:music\s+)?(?:video|audio|mv|lyric\s+video|visualizer)|official|"
    r"music\s+video|lyric\s+video|lyrics?|visualizer|full\s+ver(?:sion)?\.?|short\s+ver(?:sion)?\.?|"
    r"mv|pv|audio|video|cover(?:ed)?|original\s+song|self\s*cover|remix)\b|"
    r"歌ってみた|うたってみた|弾いてみた|叩いてみた|演奏してみた|踊ってみた|カバー|オリジナル曲|オリジナル|"
    r"セルフカバー|リミックス|公式|ミュージックビデオ")
ARTIST_SUFFIX = ("topic", "official", "オフィシャル", "公式", "officialchannel", "channel", "チャンネル",
                 "officialyoutubechannel", "youtubechannel", "ch")


def nfkc(s: str | None) -> str:
    return unicodedata.normalize("NFKC", s or "").lower()


def nz(s: str | None) -> str:
    return re.sub(r"[\W_]+", "", nfkc(s))


def artist_key(name: str | None) -> str:
    """アーティスト名・チャンネル名の比較用キー（「- Topic」「Official」などを除く）。"""
    k = nz(name)
    changed = True
    while changed:
        changed = False
        for suf in ARTIST_SUFFIX:
            if k.endswith(suf) and len(k) > len(suf) + 1:
                k = k[: -len(suf)]
                changed = True
    return k


def segments(title: str | None, artists: set[str] = frozenset()) -> list[tuple[str, str]]:
    """曲名の候補を (比較用キー, 表示用の文字列) のリストで返す。先頭ほど曲名らしい。

    artists は artist_key() 済みの既知のアーティスト名。
    """
    t = unicodedata.normalize("NFKC", title or "")
    quoted = [m.group(1) for m in QUOTE_RE.finditer(t)]
    rest = QUOTE_RE.sub(" / ", t)
    rest = BRACKET_RE.sub(" / ", rest)
    parts = [(q, False) for q in quoted] + [(p, True) for p in SPLIT_RE.split(rest)]

    def build(noise: bool) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen = set()
        for p, plain in parts:
            p = CREDIT_RE.sub("", p)
            if noise and plain:   # 「」『』の中は曲名そのものなので、決まり文句の除去はしない
                p = NOISE_RE.sub(" ", p)
            p = re.sub(r"\s+", " ", p).strip(" -_~・,、。!！?？\"'「」『』“”")
            key = nz(p)
            if not key or key in seen:
                continue
            if artist_key(p) in artists or key in artists:
                continue  # アーティスト名・チャンネル名の部分
            seen.add(key)
            out.append((key, p))
        return out

    # 決まり文句を除いたら何も残らない場合（曲名そのものが「オリジナル」など）は、除かずにやり直す
    return build(True) or build(False)


def word_match(key_display: str, seg_display: str) -> bool:
    """英数字の曲名は、単語の途中には一致させない（KING と KingDom など）。"""
    k = nfkc(key_display).strip()
    s = nfkc(seg_display)
    if not re.search(r"[a-z0-9]", k):
        return nz(k) in nz(s)
    pattern = r"(?<![a-z0-9])" + r"[\W_]*".join(re.escape(c) for c in nz(k)) + r"(?![a-z0-9])"
    return re.search(pattern, s) is not None
