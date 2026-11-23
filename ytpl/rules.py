"""分類ルール：どの動画がどの再生リストに入るべきか。

動画数が多くても軽く動くように、動画ごとの正規化文字列・曲キー・概要欄の
@ハンドル/チャンネルIDは Index で1回だけ計算し、チャンネル別・参照先別の
逆引き表から集合演算で振り分け先を求める。
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

from .store import Config

# ==========================================================================
# 文字列ユーティリティ
# ==========================================================================


def nfkc(s: str | None) -> str:
    return unicodedata.normalize("NFKC", s or "").lower()


def nz(s: str | None) -> str:
    """比較用正規化：全角半角・大小文字・空白記号を無視。"""
    return re.sub(r"[\W_]+", "", nfkc(s))


@lru_cache(maxsize=None)
def _kw_re(keywords: tuple) -> re.Pattern | None:
    parts = sorted({re.escape(nfkc(k)) for k in keywords if nfkc(k)}, key=len, reverse=True)
    return re.compile("|".join(parts)) if parts else None


def has_keyword(text_nfkc: str, keywords) -> bool:
    """text_nfkc は nfkc() 済みの文字列。キーワード群は1本の正規表現にまとめてキャッシュ。"""
    r = _kw_re(tuple(keywords or ()))
    return bool(r and r.search(text_nfkc))


def alias_keys(aliases) -> list[str]:
    return [k for k in (nz(a) for a in aliases or []) if len(k) >= 2]


# ==========================================================================
# 曲の判定と、同一曲・同一バージョンの判定
# ==========================================================================
# インスト・オフボーカル・カラオケ音源（既定ではどの再生リストにも入れない。include_off_vocal で含める）
OFF_VOCAL_RE = re.compile(
    r"instrumental|\binst\b\.?|off[\s_-]?vocal|オフボーカル|オフボ(?![ァ-ヶー])|karaoke|カラオケ|backing track", re.I)


def is_off_vocal(title: str | None) -> bool:
    return bool(OFF_VOCAL_RE.search(unicodedata.normalize("NFKC", title or "")))


def is_music(v: dict, mcfg: dict) -> bool:
    title = nfkc(v.get("title"))
    if has_keyword(title, mcfg["exclude_keywords"]):
        return False
    if v.get("live") in ("live", "upcoming"):
        return False
    d = v.get("duration")
    if d is not None and (d < mcfg["min_seconds"] or d > mcfg["max_seconds"]):
        return False
    return str(v.get("category")) in {str(c) for c in mcfg["categories"]} \
        or has_keyword(title, mcfg["include_keywords"])


def extract_song_title(title: str) -> str:
    """動画タイトルから曲名らしき部分を取り出す（ヒューリスティック）。"""
    t = unicodedata.normalize("NFKC", title or "")
    m = re.search(r"[「『]([^」』]+)[」』]", t)
    if m:
        return m.group(1).strip()
    t = re.sub(r"【[^】]*】|\[[^\]]*\]|#\S+", " ", t)
    t = re.split(r"\s*[/|]\s*|\s+-\s+|\s+feat\.?\s|\s+ft\.?\s|\s+covered by\s", t, flags=re.I)[0]
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"(?i)(official music video|music video|歌ってみた|full ver\.?|cover|カバー|\bmv\b)", " ", t)
    return re.sub(r"\s+", " ", t).strip(" -_~・")


VERSION_RE = re.compile(
    r"remix|re-mix|リミックス|\bver\b|\bversion\b|バージョン|\bedit\b|acoustic|アコースティック|"
    r"\blive\b|ライブ|piano|ピアノ|orchestra|オーケストラ|self\s?cover|セルフカバー|弾き語り|"
    r"arrange|アレンジ|\benglish\b|英語|\bjapanese\b|日本語|tv\s?size|extended|sped\s?up|slowed|"
    r"nightcore|\bdemo\b|re-?recorded|再録", re.I)
NOT_VERSION_RE = re.compile(r"official|music video|\bmv\b|\bpv\b|lyric|リリック|歌ってみた|cover\b|covered", re.I)


def version_tag(title: str) -> str:
    """リミックス・別バージョンを区別するためのタグ（通常版は空文字）。"""
    t = unicodedata.normalize("NFKC", title or "")
    base = nz(extract_song_title(title))
    segs = [a or b or c for a, b, c in re.findall(r"【([^】]*)】|\[([^\]]*)\]|\(([^)]*)\)", t)]
    rest = re.sub(r"【[^】]*】|\[[^\]]*\]|\([^)]*\)", " ", t)
    segs += re.split(r"\s*[/|]\s*|\s+-\s+|\s*[~〜]\s*", rest)
    tags = set()   # オフボーカル表記はバージョンに含めない（通常版と同じ曲として扱う）
    for s in segs:
        s = OFF_VOCAL_RE.sub(" ", s)
        if VERSION_RE.search(s) and not (NOT_VERSION_RE.search(s) and not re.search(r"remix|ver\b|version", s, re.I)):
            tag = nz(s).replace(base, "") if base else nz(s)
            tag = re.sub(r"(official|musicvideo|mv)", "", tag)
            tag = re.sub(r"version|バージョン", "ver", tag)
            if tag:
                tags.add(tag)
    return "+".join(sorted(tags))


def is_art_track(v: dict) -> bool:
    """YouTube が自動生成した音源（TOPICチャンネル等）。"""
    return nfkc(v.get("description")).lstrip().startswith("provided to youtube by") \
        or (v.get("channel_title") or "").endswith(" - Topic")


def song_key(v: dict) -> tuple[str, str]:
    return nz(extract_song_title(v.get("title") or "")), version_tag(v.get("title") or "")


MV_RE = re.compile(r"\bmv\b|\bpv\b|music video|ミュージックビデオ")


# ==========================================================================
# 設定の読み出し（チャンネル・再生リストは {id, name} の組）
# ==========================================================================
TIERS = (("top_favorites", "top"), ("favorites", "favorite"), ("occasional", "occasional"))
ROLE_ORDER = ["top", "favorite", "occasional", "official", "source"]
DEFAULT_NAMES = {"unsorted": "未仕分け", "misc": "その他の曲"}


def ids(refs) -> list[str]:
    """{id, name} オブジェクトのリストからIDを取り出す（3分類の channels 用。名前による指定は不可）。"""
    return [r["id"] for r in refs or [] if isinstance(r, dict) and r.get("id")]


def entry_channels(cfg: Config) -> dict[str, list[str]]:
    """3分類の項目の name -> その項目の channels のID。"""
    out: dict[str, list[str]] = {}
    for _, _, _, e in tier_entries(cfg):
        if isinstance(e.get("name"), str):
            out.setdefault(e["name"], ids(e.get("channels")))
    return out


def chan_ids(cfg: Config, refs) -> list[str]:
    """チャンネル指定のリストをIDに展開する。

    要素は {id, name} オブジェクト、または3分類の項目の name（文字列）。name の場合は、
    その項目の channels（本人・サブ・TOPIC）すべてを指す。
    """
    by_name = entry_channels(cfg)
    out: list[str] = []
    for r in refs or []:
        if isinstance(r, dict) and r.get("id"):
            out.append(r["id"])
        elif isinstance(r, str):
            out += by_name.get(r, [])
    return list(dict.fromkeys(out))


def tier_entries(cfg: Config):
    """(セクション名, 分類, 位置, 項目)"""
    for key, tier in TIERS:
        for i, e in enumerate(cfg.list(key)):
            yield key, tier, i, e


@dataclass
class PL:
    """再生リストの指定。id がなければ name で新規作成し、作ったIDを config.yaml の path の位置に書き込む。"""
    key: str
    name: str
    id: str | None
    auto: bool
    path: tuple
    creatable: bool = True


def _pl(ref: dict | None, key: str, auto: bool, path: tuple, creatable=True) -> PL | None:
    if not ref:
        return None
    return PL(key=key, name=ref.get("name") or key, id=ref.get("id"), auto=auto, path=path, creatable=creatable)


def fixed_playlists(cfg: Config) -> dict[str, PL]:
    """「未仕分け」と「その他の曲」。"""
    ps = cfg.raw.get("playlists") or {}
    return {k: _pl({"name": DEFAULT_NAMES[k], **(ps.get(k) or {})}, k, True, ("playlists", k)) for k in DEFAULT_NAMES}


def channel_roles(cfg: Config) -> dict[str, dict]:
    roles: dict[str, dict] = {}

    def put(cid, role, name):
        cur = roles.get(cid)
        if cur is None or ROLE_ORDER.index(role) < ROLE_ORDER.index(cur["role"]):
            roles[cid] = {"role": role, "name": name}

    for _, tier, _, e in tier_entries(cfg):
        for cid in ids(e.get("channels")):
            put(cid, tier, e["name"])
    for s in cfg.songs():
        for cid in chan_ids(cfg, s.get("official_channels")):
            put(cid, "official", s["name"])
    for _, _, _, e in tier_entries(cfg):
        for sp in e.get("specials") or []:
            for cid in chan_ids(cfg, sp.get("published_by")):
                put(cid, "source", (sp.get("playlist") or {}).get("name"))
    return roles


def entity_map(cfg: Config) -> dict[str, str]:
    """チャンネルID -> 同一アーティストのまとまり（1項目の channels は同一人物：本人・サブ・TOPIC）。"""
    ent = {}
    for key, _, _, e in tier_entries(cfg):
        for cid in ids(e.get("channels")):
            ent.setdefault(cid, f"{key}:{e['name']}")
    return ent


def handle_channel_ids(cfg: Config) -> set[str]:
    """@ハンドルを調べる必要のあるチャンネル。"""
    out = set()
    for _, _, _, e in tier_entries(cfg):
        out |= set(ids(e.get("channels")))
        for sp in e.get("specials") or []:
            out |= set(chan_ids(cfg, sp.get("mentions")))
    return out


def ref_tokens(channel_ids, handles: dict[str, str] | None) -> set[str]:
    """チャンネルを指す文字列（チャンネルIDと@ハンドル、小文字化）。"""
    toks = set()
    for cid in channel_ids or []:
        toks.add(cid.lower())
        h = (handles or {}).get(cid)
        if h:
            toks.add(nfkc(h if h.startswith("@") else "@" + h))
    return toks


# ==========================================================================
# 設定の検証
# ==========================================================================
UC_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
PL_RE = re.compile(r"^(PL|FL|OL|LL|UU|RD)[0-9A-Za-z_-]+$")

LEGACY_ENTRY = {
    "channel_ids": "channels", "playlist_id": "playlist", "own": "playlist",
    "other_playlist": "other.playlist", "other_playlist_id": "other.playlist", "other_auto": "（廃止：「その他○○」は常に未仕分け経由）",
    "other_include_video_ids": "other.include_video_ids", "other_exclude_video_ids": "other.exclude_video_ids",
    "other_exclude_keywords": "other.exclude_keywords",
}
LEGACY_SPECIAL = {"partner_channel_ids": "mentions", "partner_aliases": "mention_aliases",
                  "playlist_id": "playlist", "channel_ids": "published_by"}
LEGACY_SONG = {"title": "name", "official_channel_ids": "official_channels", "playlist_id": "playlist"}
LEGACY_TOP = {"combos": "各チャンネル項目の specials", "titles": "再生リストを {id, name} で直接書く",
              "unsorted": "playlists.unsorted（「未仕分け」からは削除しません）"}


def validate(cfg: Config) -> list[str]:
    errors = [f"文字列はダブルクオートで囲んでください: {q}" for q in cfg.quote_errors]

    def legacy(obj, table, where):
        for old, new in table.items():
            if isinstance(obj, dict) and old in obj:
                errors.append(f"{where}{old} は廃止されました → {new}")

    names = [e.get("name") for _, _, _, e in tier_entries(cfg)]
    dup = sorted({n for n in names if isinstance(n, str) and names.count(n) > 1})
    for n in dup:
        errors.append(f"3分類の name「{n}」が重複しています（name は一意にしてください）")
    known_names = set(entry_channels(cfg))

    def check_channels(refs, where, required=False, allow_names=True):
        if required and not refs:
            errors.append(f"{where}: チャンネルがありません")
        if refs is not None and not isinstance(refs, list):
            errors.append(f"{where}: リストで書いてください")
            return
        for i, r in enumerate(refs or []):
            if isinstance(r, str):
                if not allow_names:
                    errors.append(f"{where}[{i}]: ここでは名前による指定はできません。{{id, name}} で書いてください: {r}")
                elif r not in known_names:
                    errors.append(f"{where}[{i}]: 「{r}」という name の項目が3分類にありません")
                continue
            if not isinstance(r, dict) or not r.get("id") or not r.get("name"):
                errors.append(f"{where}[{i}]: チャンネルは id と name の両方が必要です")
            elif not UC_RE.match(r["id"]):
                errors.append(f"{where}[{i}]: チャンネルIDの形式が違います（UC で始まる24文字）: {r['id']}")

    def check_pl(ref, where, need_id=False):
        if ref is None:
            return
        if not isinstance(ref, dict) or not ref.get("name"):
            errors.append(f"{where}: 再生リストは name が必要です（id は任意）")
            return
        if need_id and not ref.get("id"):
            errors.append(f"{where}: id が必要です（id があるときだけ「その他○○」を扱います）")
        if ref.get("id") and not PL_RE.match(ref["id"]):
            errors.append(f"{where}: 再生リストIDの形式が違います: {ref['id']}")

    legacy(cfg.raw, LEGACY_TOP, "")
    for kind in DEFAULT_NAMES:
        check_pl((cfg.raw.get("playlists") or {}).get(kind), f"playlists.{kind}")
    for key, _, i, e in tier_entries(cfg):
        w = f"{key}[{i}]「{e.get('name')}」"
        legacy(e, LEGACY_ENTRY, f"{w}.")
        if not e.get("name"):
            errors.append(f"{w}: name がありません")
        check_channels(e.get("channels"), f"{w}.channels", required=True, allow_names=False)
        check_pl(e.get("playlist"), f"{w}.playlist")
        other = e.get("other")
        if other is not None:
            if not isinstance(other, dict) or "playlist" not in other:
                errors.append(f"{w}.other: playlist がありません")
            else:
                check_pl(other.get("playlist"), f"{w}.other.playlist", need_id=True)
        for j, sp in enumerate(e.get("specials") or []):
            ws = f"{w}.specials[{j}]"
            legacy(sp, LEGACY_SPECIAL, f"{ws}.")
            if not sp.get("playlist"):
                errors.append(f"{ws}: playlist がありません")
            check_pl(sp.get("playlist"), f"{ws}.playlist")
            check_channels(sp.get("published_by"), f"{ws}.published_by")
            check_channels(sp.get("mentions"), f"{ws}.mentions")
    for i, s in enumerate(cfg.list("songs")):
        w = f"songs[{i}]「{s.get('name')}」"
        legacy(s, LEGACY_SONG, f"{w}.")
        if not s.get("name"):
            errors.append(f"{w}: name がありません")
        check_pl(s.get("playlist"), f"{w}.playlist")
        check_channels(s.get("official_channels"), f"{w}.official_channels")
    return errors


# ==========================================================================
# 動画索引
# ==========================================================================
HANDLE_RE = re.compile(r"@[\w.\-]+")
UCID_RE = re.compile(r"uc[0-9a-z_-]{22}")


@dataclass
class Rec:
    v: dict
    ch: str | None
    t: str        # nfkc(タイトル)
    tz: str       # nz(タイトル)
    xz: str       # nz(タイトル＋概要欄)
    key: tuple    # (曲名, バージョン)
    art: bool     # TOPIC等の自動生成音源
    mv: bool      # タイトルにMV表記
    off: bool     # インスト・オフボーカル


class Index:
    def __init__(self, cfg: Config, videos):
        mcfg = cfg.section("music")
        use_desc = cfg.section("matching")["use_description"]
        ignore = set(cfg.list("ignore_video_ids"))
        self.r: dict[str, Rec] = {}
        self.by_ch: dict[str, set[str]] = defaultdict(set)
        self.by_ref: dict[str, set[str]] = defaultdict(set)
        self._grams: dict[str, set[str]] | None = None
        for v in videos:
            vid = v["id"]
            if vid in ignore or not is_music(v, mcfg):
                continue
            t = nfkc(v.get("title"))
            x = t + ("\n" + nfkc(v.get("description")) if use_desc else "")
            rec = Rec(v, v.get("channel_id"), t, nz(t), nz(x), song_key(v), is_art_track(v), bool(MV_RE.search(t)),
                      is_off_vocal(v.get("title")))
            self.r[vid] = rec
            self.by_ch[rec.ch].add(vid)
            for tok in set(HANDLE_RE.findall(x)) | set(UCID_RE.findall(x)):
                self.by_ref[tok.rstrip(".-")].add(vid)

    def __contains__(self, vid):
        return vid in self.r

    def channels(self, cids) -> set[str]:
        out = set()
        for c in cids or []:
            out |= self.by_ch.get(c, set())
        return out

    def mentioning(self, tokens) -> set[str]:
        out = set()
        for tok in tokens or []:
            out |= self.by_ref.get(tok, set())
        return out

    def _title_grams(self) -> dict[str, set[str]]:
        """タイトル（正規化済み）の2文字索引。曲名の部分一致を全件走査せずに引くため。"""
        if self._grams is None:
            self._grams = defaultdict(set)
            for vid, rec in self.r.items():
                for g in {rec.tz[i:i + 2] for i in range(len(rec.tz) - 1)}:
                    self._grams[g].add(vid)
        return self._grams

    def alias_hits(self, aliases, among=None, field="xz") -> set[str]:
        """名前の部分一致。field="tz"（タイトル）は2文字索引で候補を絞ってから確認する。"""
        keys = alias_keys(aliases)
        if not keys:
            return set()
        if field == "tz":
            grams = self._title_grams()
            out = set()
            for k in keys:
                posts = sorted((grams.get(k[i:i + 2], set()) for i in range(len(k) - 1)), key=len)
                cand = set(posts[0]).intersection(*posts[1:]) if posts else set()
                if among is not None:
                    cand &= among
                out |= {vid for vid in cand if k in self.r[vid].tz}
            return out
        pool = self.r.keys() if among is None else among
        return {vid for vid in pool if any(k in self.r[vid].xz for k in keys)}

    def title_has(self, keywords, among) -> set[str]:
        r = _kw_re(tuple(keywords or ()))
        if not r:
            return set()
        return {vid for vid in among if r.search(self.r[vid].t)}


def dedupe(vids, idx: Index, entity_of) -> set[str]:
    """同じアーティストの同じ曲・同じバージョンは1本だけ残す。

    優先順：MV → 通常の動画 → TOPIC音源 → 本チャンネルのオフボーカル → TOPICのオフボーカル（同順位は古い順）。
    オフボーカルは通常版と同じグループなので、通常版があればオフボーカルは残らない。
    """
    best: dict[tuple, tuple] = {}
    keep = set()
    for vid in vids:
        rec = idx.r.get(vid)
        if rec is None or not rec.key[0]:
            keep.add(vid)
            continue
        g = (entity_of(rec.ch), *rec.key)
        pref = (rec.off, rec.art, not rec.mv, rec.v.get("published_at") or "9999", vid)
        if g not in best or pref < best[g]:
            best[g] = pref
    return keep | {p[-1] for p in best.values()}


def duplicate_groups(vids, idx: Index, entity_of) -> list[list[str]]:
    groups = defaultdict(list)
    for vid in vids:
        rec = idx.r.get(vid)
        if rec and rec.key[0]:
            groups[(entity_of(rec.ch), *rec.key)].append(vid)
    return [g for g in groups.values() if len(g) > 1]


# ==========================================================================
# 振り分け先の計算
# ==========================================================================
@dataclass
class Target:
    pl: PL
    auto: set = field(default_factory=set)     # 自動で追加する動画
    cand: set = field(default_factory=set)     # 「未仕分け」に仮保存して人が判断する候補


def build_targets(cfg: Config, idx: Index, roles: dict[str, dict],
                  handles: dict[str, str] | None = None) -> list[Target]:
    do_dedupe = cfg.section("dedupe")["enabled"]
    ents = entity_map(cfg)
    tier_channels = {c for c, r in roles.items() if r["role"] in ("top", "favorite", "occasional")}
    off = {vid for vid, r in idx.r.items() if r.off}
    out: list[Target] = []

    def ent_of(extra=None):
        return lambda cid: (extra or {}).get(cid) or ents.get(cid) or cid

    # 通常版（オフボーカルでない版）がある「人×曲×バージョン」。オフボーカルはここにないときだけ入れる
    has_vocal = {(ents.get(r.ch) or r.ch, r.key) for r in idx.r.values() if not r.off and r.key[0]}

    def off_allowed(entity_of, extra_vocal=frozenset()):
        return {vid for vid in off
                if idx.r[vid].key[0]
                and (entity_of(idx.r[vid].ch), idx.r[vid].key) not in has_vocal
                and (entity_of(idx.r[vid].ch), idx.r[vid].key) not in extra_vocal}

    def emit(pl: PL, verified: set, unverified: set, rule: dict, entity_of=None, extra_vocal=frozenset()):
        """verified は pl.auto に従って自動/候補へ。unverified（名前一致のみ）は常に候補へ。"""
        inc = set(rule.get("include_video_ids") or [])
        exc = set(rule.get("exclude_video_ids") or [])
        drop = off - off_allowed(entity_of or ent_of(), extra_vocal) if rule.get("include_off_vocal") else off
        verified, unverified = verified - drop, unverified - drop
        auto = (verified if pl.auto else set()) - exc
        cand = ((set() if pl.auto else verified) | unverified) - exc - auto
        if do_dedupe:
            kept = dedupe(auto | cand, idx, entity_of or ent_of())
            auto, cand = auto & kept, cand & kept
        out.append(Target(pl, auto | inc, cand - inc))

    for key, tier, i, e in tier_entries(cfg):
        own_ids = ids(e.get("channels"))
        own = idx.channels(own_ids)
        verified = idx.mentioning(ref_tokens(own_ids, handles)) - own
        by_name = (idx.alias_hits(e.get("aliases")) - own - verified) if e.get("aliases") else set()

        # 特別再生リスト：対象範囲のうち、書かれた条件をすべて満たすもの
        in_specials = set()
        for j, sp in enumerate(e.get("specials") or []):
            pub = chan_ids(cfg, sp.get("published_by"))
            m_ids = chan_ids(cfg, sp.get("mentions"))
            m_alias = sp.get("mention_aliases")

            def split(pool, refs_ids, aliases, base_verified=frozenset()):
                """(チャンネルで確認できた動画, 名前だけで一致した動画) に分ける。"""
                ver = (pool & set(base_verified)) | (pool & idx.mentioning(ref_tokens(refs_ids, handles)))
                return ver, idx.alias_hits(aliases, among=pool) - ver

            if not sp.get("require_related", True):
                # 関係の確認なし：published_by（なければ全動画）を対象に、書いた条件だけで判定
                h = idx.channels(pub) if pub else set(idx.r)
                pools = split(h, m_ids, m_alias) if (m_ids or m_alias) else (h, set())
            elif pub:
                # published_by あり：指定チャンネルの動画だけが対象。
                # 関係は mentions（既定：この人の channels）と mention_aliases（既定：この人の aliases）で確認する。
                # この人のチャンネルの動画は確認不要
                h = idx.channels(pub)
                alias = m_alias if m_alias else e.get("aliases")
                if m_ids:
                    pools = split(h, m_ids, alias)
                else:
                    pools = split(h, own_ids, alias, base_verified=own)
            else:
                # published_by なし：この人の曲（本人名義＋関係曲）が対象。名前だけの関係曲は確認待ち
                pool_v, pool_n = own | verified, by_name
                if m_ids or m_alias:
                    v1, n1 = split(pool_v, m_ids, m_alias)
                    v2, n2 = split(pool_n, m_ids, m_alias)
                    pools = (v1, n1 | v2 | n2)
                else:
                    pools = (pool_v, pool_n)
            hits = []
            for h in pools:
                if sp.get("keywords"):
                    h = idx.title_has(sp["keywords"], h)
                if sp.get("exclude_keywords"):
                    h = h - idx.title_has(sp["exclude_keywords"], h)
                hits.append(h)
            in_specials |= hits[0] | hits[1]
            pl = _pl(sp["playlist"], f"special:{key}:{i}:{j}", sp.get("auto", True), (key, i, "specials", j, "playlist"))
            emit(pl, hits[0], hits[1], sp)

        if e.get("playlist"):
            pl = _pl(e["playlist"], f"own:{key}:{i}", e.get("auto", tier == "top"), (key, i, "playlist"))
            emit(pl, own, set(), e)

        # 「その他○○」：常に判断（自動では追加しない）。other.playlist.id があるときだけ扱う
        other = e.get("other") or {}
        op = other.get("playlist") or {}
        if op.get("id"):
            rest = (verified | by_name) - in_specials
            if other.get("exclude_keywords"):
                rest -= idx.title_has(other["exclude_keywords"], rest)
            pl = _pl(op, f"other:{key}:{i}", False, (key, i, "other", "playlist"), creatable=False)
            emit(pl, rest, set(), other)

    # お気に入りの曲：公式＋登録チャンネルのカバー（自動）
    for i, s in enumerate(cfg.songs()):
        official = set(chan_ids(cfg, s.get("official_channels")))
        pool = idx.channels(tier_channels | official)
        hit = idx.alias_hits(list(s.get("aliases") or []) or [s["name"]], among=pool, field="tz")
        if s.get("exclude_keywords"):
            hit -= idx.title_has(s["exclude_keywords"], hit)
        ref = {"name": s["name"], **(s.get("playlist") or {})}
        off_ent = {c: "official:" + s["name"] for c in official if c not in ents}
        extra = frozenset((off_ent[c], idx.r[v].key) for c in off_ent for v in idx.by_ch.get(c, ())
                          if not idx.r[v].off)
        emit(_pl(ref, f"song:{i}", True, ("songs", i, "playlist")), hit, set(), s,
             entity_of=ent_of(off_ent), extra_vocal=extra)
    return out


def duplicate_losers(cfg: Config, idx: Index) -> set[str]:
    """同じ人の同じ曲で、より優先度の高い版（MV等）がすでにある動画。"""
    ents = entity_map(cfg)
    return set(idx.r) - dedupe(set(idx.r), idx, lambda c: ents.get(c) or c)


def needs_unsorted(vid: str, roles: dict, idx: Index, auto_hits: int, cand_hits: int) -> bool:
    """新着動画を「未仕分け」に仮保存すべきか（判断待ちの候補、またはどこにも自動で入らない曲）。"""
    rec = idx.r.get(vid)
    if rec is None:
        return False
    if cand_hits:
        return True
    if rec.off:
        return False  # オフボーカルは、含める設定の再生リストの候補になったときだけ
    return roles.get(rec.ch, {}).get("role") in ("favorite", "occasional") and not auto_hits


def promotion_candidates(cfg: Config, misc_titles: list[str], idx: Index, roles: dict) -> list[dict]:
    """「その他の曲」のうち、本家/カバーが min_versions 曲以上ある曲（同じ人の同じ音源は1曲と数える）。"""
    p = cfg.section("promotion")
    if not p["enabled"]:
        return []
    ents = entity_map(cfg)
    tier_ch = {c for c, r in roles.items() if r["role"] in ("top", "favorite", "occasional")}
    pool = [r for r in (idx.r[v] for v in idx.channels(tier_ch)) if not r.off]
    known = set()
    for s in cfg.songs():
        known |= set(alias_keys(list(s.get("aliases") or []) + [s["name"]]))
    ignored = set(alias_keys(p["ignore"]))
    out, seen = [], set()
    for title in misc_titles:
        name = extract_song_title(title or "")
        key = nz(name)
        if len(key) < 2 or key in seen or key in ignored:
            continue
        seen.add(key)
        if any(k in key or key in k for k in known):
            continue
        count = len({(ents.get(r.ch, r.ch), r.key) for r in pool if key in r.tz})
        if count >= int(p["min_versions"]):
            out.append({"name": name, "aliases": [name], "versions": count})
    return out
