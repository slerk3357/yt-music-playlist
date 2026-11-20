"""分類ルール：どの動画がどの再生リストに入るべきか。"""
from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict, defaultdict

from .store import Config



# --------------------------------------------------------------------------
# 文字列ユーティリティ
# --------------------------------------------------------------------------
def nfkc(s: str | None) -> str:
    return unicodedata.normalize("NFKC", s or "").lower()


def nz(s: str | None) -> str:
    """比較用正規化：全角半角・大小文字・空白記号を無視。"""
    return re.sub(r"[\W_]+", "", nfkc(s))


def has_keyword(text: str | None, keywords) -> bool:
    t = nfkc(text)
    return any(nfkc(k) and nfkc(k) in t for k in keywords or [])


def matches_alias(text: str | None, aliases) -> bool:
    t = nz(text)
    return any(len(nz(a)) >= 2 and nz(a) in t for a in aliases or [])


def channel_refs(channel_ids, handles: dict[str, str] | None) -> list[str]:
    """チャンネルを指す文字列（@ハンドルとチャンネルID）。"""
    refs = []
    for cid in channel_ids or []:
        refs.append(cid.lower())
        h = (handles or {}).get(cid)
        if h:
            refs.append(nfkc(h if h.startswith("@") else "@" + h))
    return refs


def matches_channel_ref(text: str | None, refs) -> bool:
    """概要欄などに @ハンドル や channel/UC… が書かれているか。"""
    t = nfkc(text)
    for r in refs or []:
        start = 0
        while (i := t.find(r, start)) >= 0:
            end = i + len(r)
            nxt = t[end:end + 1]
            if not (r.startswith("@") and nxt and (nxt.isalnum() or nxt in "._-")):
                if not (r.startswith("@") and i > 0 and (t[i - 1].isalnum() or t[i - 1] in "._-")):
                    return True
            start = end
    return False


# --------------------------------------------------------------------------
# 曲の判定と、同一曲・同一バージョンの判定
# --------------------------------------------------------------------------
# インスト・オフボーカル・カラオケ音源は常に除外
BUILTIN_EXCLUDE_RE = re.compile(
    r"instrumental|\binst\b|off[\s_-]?vocal|オフボーカル|オフボ\b|karaoke|カラオケ|backing track", re.I)


def is_music(v: dict, mcfg: dict) -> bool:
    title = v.get("title") or ""
    if BUILTIN_EXCLUDE_RE.search(unicodedata.normalize("NFKC", title)):
        return False
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
    r"instrumental|\binst\b|off\s?vocal|オフボーカル|karaoke|カラオケ|\blive\b|ライブ|piano|ピアノ|"
    r"orchestra|オーケストラ|self\s?cover|セルフカバー|弾き語り|arrange|アレンジ|\benglish\b|英語|"
    r"\bjapanese\b|日本語|tv\s?size|extended|sped\s?up|slowed|nightcore|\bdemo\b|re-?recorded|再録", re.I)
NOT_VERSION_RE = re.compile(r"official|music video|\bmv\b|\bpv\b|lyric|リリック|歌ってみた|cover\b|covered", re.I)


def version_tag(title: str) -> str:
    """リミックス・別バージョンを区別するためのタグ（通常版は空文字）。"""
    t = unicodedata.normalize("NFKC", title or "")
    base = nz(extract_song_title(title))
    segs = [a or b or c for a, b, c in re.findall(r"【([^】]*)】|\[([^\]]*)\]|\(([^)]*)\)", t)]
    rest = re.sub(r"【[^】]*】|\[[^\]]*\]|\([^)]*\)", " ", t)
    segs += re.split(r"\s*[/|]\s*|\s+-\s+|\s*[~〜]\s*", rest)
    tags = set()
    for s in segs:
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


def _preference(v: dict):
    title = nfkc(v.get("title"))
    has_mv = bool(re.search(r"\bmv\b|\bpv\b|music video|ミュージックビデオ", title))
    return (is_art_track(v), not has_mv, v.get("published_at") or "9999", v.get("id"))


# --------------------------------------------------------------------------
# チャンネル設定
#   top_favorites / favorites / occasional の各項目は同じ書式:
#     name, channel_ids, aliases, playlist_id, other, other_playlist_id, specials[...]
# --------------------------------------------------------------------------
TIERS = (("top_favorites", "top"), ("favorites", "favorite"), ("occasional", "occasional"))
ROLE_ORDER = ["top", "favorite", "occasional", "official", "source"]


def tier_entries(cfg: Config):
    for key, tier in TIERS:
        for e in cfg.list(key):
            yield key, tier, e


def own_title(cfg: Config, tier: str, e: dict) -> str | None:
    if tier == "occasional" and not e.get("own"):
        return None
    return e.get("playlist") or cfg.title("own", e["name"])


def other_title(cfg: Config, tier: str, e: dict) -> str | None:
    if not e.get("other", tier == "top"):
        return None
    return e.get("other_playlist") or cfg.title("other", e["name"])


def channel_roles(cfg: Config) -> dict[str, dict]:
    roles: dict[str, dict] = {}

    def put(cid, role, name):
        cur = roles.get(cid)
        if cur is None or ROLE_ORDER.index(role) < ROLE_ORDER.index(cur["role"]):
            roles[cid] = {"role": role, "name": name}

    for _, tier, e in tier_entries(cfg):
        for cid in e.get("channel_ids") or []:
            put(cid, tier, e["name"])
    for s in cfg.list("songs"):
        for cid in s.get("official_channel_ids") or []:
            put(cid, "official", s["title"])
    for _, _, e in tier_entries(cfg):
        for sp in e.get("specials") or []:
            for cid in sp.get("published_by") or []:
                put(cid, "source", sp["playlist"])
    return roles


def entity_map(cfg: Config) -> dict[str, str]:
    """チャンネルID -> 同一アーティストのまとまり（1項目の channel_ids は同一人物：本人・サブ・TOPIC）。"""
    ent = {}
    for key, _, e in tier_entries(cfg):
        for cid in e.get("channel_ids") or []:
            ent.setdefault(cid, f"{key}:{e['name']}")
    return ent


def dedupe(vids: set[str], by_id: dict[str, dict], entity_of) -> set[str]:
    """同じアーティストの同じ曲・同じバージョンは1本だけ残す（MV優先、TOPIC音源は後回し）。"""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    keep = set()
    for vid in vids:
        v = by_id.get(vid)
        if not v or not song_key(v)[0]:
            keep.add(vid)
            continue
        groups[(entity_of(v.get("channel_id")), *song_key(v))].append(v)
    for vs in groups.values():
        keep.add(min(vs, key=_preference)["id"])
    return keep


def duplicate_groups(vids, by_id, entity_of) -> list[list[str]]:
    groups = defaultdict(list)
    for vid in vids:
        v = by_id.get(vid)
        if v and song_key(v)[0]:
            groups[(entity_of(v.get("channel_id")), *song_key(v))].append(vid)
    return [g for g in groups.values() if len(g) > 1]


def handle_channel_ids(cfg: Config) -> set[str]:
    """@ハンドルを調べる必要のあるチャンネル。"""
    ids = set()
    for _, _, e in tier_entries(cfg):
        ids |= set(e.get("channel_ids") or [])
        for sp in e.get("specials") or []:
            ids |= set(sp.get("mentions") or [])
    return ids


def rule_titles(cfg: Config) -> dict[str, dict]:
    """再生リストタイトル -> {"playlist_id": 固定ID or None}"""
    out = {}
    for _, tier, e in tier_entries(cfg):
        if (t := own_title(cfg, tier, e)):
            out[t] = {"playlist_id": e.get("playlist_id")}
        if (t := other_title(cfg, tier, e)):
            out[t] = {"playlist_id": e.get("other_playlist_id")}
        for sp in e.get("specials") or []:
            out[sp["playlist"]] = {"playlist_id": sp.get("playlist_id")}
    for s in cfg.list("songs"):
        out[s.get("playlist") or s["title"]] = {"playlist_id": s.get("playlist_id")}
    ids = cfg.raw.get("playlist_ids") or {}
    for kind in ("unsorted", "misc"):
        out[cfg.title(kind)] = {"playlist_id": ids.get(kind)}
    return out


def validate(cfg: Config) -> list[str]:
    errors = []
    if cfg.raw.get("combos"):
        errors.append("combos は廃止されました。各チャンネル項目の specials に移してください（config.example.yaml 参照）")
    for key, _, e in tier_entries(cfg):
        if not e.get("channel_ids"):
            errors.append(f"{key}「{e.get('name')}」に channel_ids がありません")
        for sp in e.get("specials") or []:
            if not sp.get("playlist"):
                errors.append(f"{key}「{e.get('name')}」の specials に playlist がありません")
    return errors


# --------------------------------------------------------------------------
# 振り分け先の計算
# --------------------------------------------------------------------------
class Targets:
    """auto: 自動で追加する再生リスト / manual: 候補を「未仕分け」に出して人が判断する再生リスト"""

    def __init__(self):
        self.auto: OrderedDict[str, set[str]] = OrderedDict()
        self.manual: OrderedDict[str, set[str]] = OrderedDict()

    def put(self, title, vids, auto: bool):
        (self.auto if auto else self.manual)[title] = vids

    def titles(self) -> set[str]:
        return set(self.auto) | set(self.manual)

    def hits(self, vid) -> tuple[set[str], set[str]]:
        return ({t for t, s in self.auto.items() if vid in s},
                {t for t, s in self.manual.items() if vid in s})


def _overrides(rule: dict, vids: set[str], prefix: str = "") -> set[str]:
    vids = vids | set(rule.get(f"{prefix}include_video_ids") or [])
    return vids - set(rule.get(f"{prefix}exclude_video_ids") or [])


def music_videos(cfg: Config, videos: list[dict]) -> list[dict]:
    mcfg = cfg.section("music")
    ignore = set(cfg.list("ignore_video_ids"))
    return [v for v in videos if v["id"] not in ignore and is_music(v, mcfg)]


def build_targets(cfg: Config, videos: list[dict], roles: dict[str, dict],
                  handles: dict[str, str] | None = None) -> Targets:
    use_desc = cfg.section("matching")["use_description"]
    do_dedupe = cfg.section("dedupe")["enabled"]
    ignore = set(cfg.list("ignore_video_ids"))
    music = music_videos(cfg, videos)
    by_id = {v["id"]: v for v in music}
    ents = entity_map(cfg)
    tier_channels = {c for c, r in roles.items() if r["role"] in ("top", "favorite", "occasional")}
    out = Targets()

    def ent_of(extra=None):
        return lambda cid: (extra or {}).get(cid) or ents.get(cid) or cid

    def finish(rule, vids, prefix="", entity_of=None):
        if do_dedupe:
            vids = dedupe(vids, by_id, entity_of or ent_of())
        return _overrides(rule, vids, prefix) - ignore

    def text(v):
        return (v.get("title") or "") + ("\n" + (v.get("description") or "") if use_desc else "")

    for _, tier, e in tier_entries(cfg):
        own_ids = set(e.get("channel_ids") or [])
        refs = channel_refs(own_ids, handles)
        aliases = e.get("aliases") or []

        def related(v):
            return matches_channel_ref(text(v), refs) or (aliases and matches_alias(text(v), aliases))

        own = {v["id"] for v in music if v.get("channel_id") in own_ids}
        others = {v["id"] for v in music if v.get("channel_id") not in own_ids and related(v)}

        # 特別再生リスト：この人の曲（本人名義＋関係曲）のうち条件を満たすもの
        in_specials = set()
        for sp in e.get("specials") or []:
            pool = own | others if sp.get("require_related", True) else set(by_id)
            pub = set(sp.get("published_by") or [])
            mrefs = channel_refs(sp.get("mentions"), handles)
            malias = sp.get("mention_aliases") or []
            hit = set()
            for vid in pool:
                v = by_id[vid]
                if pub and v.get("channel_id") not in pub:
                    continue
                if (mrefs or malias) and not (matches_channel_ref(text(v), mrefs)
                                              or (malias and matches_alias(text(v), malias))):
                    continue
                if sp.get("keywords") and not has_keyword(v.get("title"), sp["keywords"]):
                    continue
                if has_keyword(v.get("title"), sp.get("exclude_keywords")):
                    continue
                hit.add(vid)
            hit = _overrides(sp, hit)
            in_specials |= hit
            out.put(sp["playlist"], finish(sp, hit), sp.get("auto", True))

        if (t := own_title(cfg, tier, e)):
            out.put(t, finish(e, own), e.get("auto", tier == "top"))
        if (t := other_title(cfg, tier, e)):
            rest = {v for v in others - in_specials
                    if not has_keyword(by_id[v].get("title"), e.get("other_exclude_keywords"))}
            out.put(t, finish(e, rest, "other_"), e.get("other_auto", tier == "top"))

    # お気に入りの曲：公式＋登録チャンネルのカバー
    for s in cfg.list("songs"):
        aliases = list(s.get("aliases") or []) or [s["title"]]
        official = set(s.get("official_channel_ids") or [])
        allowed = tier_channels | official
        hit = {v["id"] for v in music
               if v.get("channel_id") in allowed and matches_alias(v.get("title"), aliases)
               and not has_keyword(v.get("title"), s.get("exclude_keywords"))}
        official_ent = {cid: "official:" + s["title"] for cid in official if cid not in ents}
        out.put(s.get("playlist") or s["title"], finish(s, hit, entity_of=ent_of(official_ent)), True)
    return out


def duplicate_losers(cfg: Config, videos: list[dict]) -> set[str]:
    """同じ人の同じ曲で、より優先度の高い版（MV等）がすでにある動画。"""
    music = music_videos(cfg, videos)
    by_id = {v["id"]: v for v in music}
    ents = entity_map(cfg)
    keep = dedupe(set(by_id), by_id, lambda c: ents.get(c) or c)
    return set(by_id) - keep


def needs_unsorted(cfg: Config, v: dict, roles: dict, auto_hits: set[str], manual_hits: set[str],
                   losers: set[str]) -> bool:
    """新着動画を「未仕分け」に仮保存すべきか。"""
    if v["id"] in losers or v["id"] in set(cfg.list("ignore_video_ids")) \
            or not is_music(v, cfg.section("music")):
        return False
    if manual_hits:
        return True  # 人が判断する再生リストの候補
    role = roles.get(v.get("channel_id"), {}).get("role")
    return role in ("favorite", "occasional") and not auto_hits


def promotion_candidates(cfg: Config, misc_entries: list[dict], videos: list[dict],
                         roles: dict) -> list[dict]:
    """「その他の曲」のうち、本家/カバーが min_versions 曲以上ある曲（同じ人の同じ音源は1曲と数える）。"""
    p = cfg.section("promotion")
    if not p["enabled"]:
        return []
    ents = entity_map(cfg)
    pool_channels = {c for c, r in roles.items() if r["role"] in ("top", "favorite", "occasional")}
    pool = [(nz(v.get("title")), ents.get(v.get("channel_id"), v.get("channel_id")), song_key(v))
            for v in music_videos(cfg, videos) if v.get("channel_id") in pool_channels]
    known = set()
    for s in cfg.list("songs"):
        for a in list(s.get("aliases") or []) + [s["title"]]:
            known.add(nz(a))
    ignored = {nz(x) for x in p["ignore"] or []}
    out, seen = [], set()
    for e in misc_entries:
        name = extract_song_title(e.get("title") or "")
        key = nz(name)
        if len(key) < 2 or key in seen or key in ignored:
            continue
        seen.add(key)
        if any(k and (k in key or key in k) for k in known):
            continue
        count = len({(ent, sk) for t, ent, sk in pool if key in t})
        if count >= int(p["min_versions"]):
            out.append({"title": name, "aliases": [name], "versions": count})
    return out
