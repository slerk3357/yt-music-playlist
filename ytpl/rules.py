"""分類ルール：どの動画がどの再生リストに入るべきか。"""
from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict, defaultdict

from .store import Config

ROLE_ORDER = ["top", "favorite", "occasional", "official"]
SECTIONS = (("top_favorites", "top"), ("favorites", "favorite"), ("occasional", "occasional"))


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
def is_music(v: dict, mcfg: dict) -> bool:
    title = v.get("title") or ""
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
# チャンネルの役割と「同じアーティスト」のまとまり
# --------------------------------------------------------------------------
def channel_roles(cfg: Config) -> dict[str, dict]:
    roles: dict[str, dict] = {}

    def put(cid, role, name):
        cur = roles.get(cid)
        if cur is None or ROLE_ORDER.index(role) < ROLE_ORDER.index(cur["role"]):
            roles[cid] = {"role": role, "name": name}

    for key, role in SECTIONS:
        for ent in cfg.list(key):
            for cid in ent.get("channel_ids") or []:
                put(cid, role, ent["name"])
    for c in cfg.list("combos"):
        for cid in c.get("channel_ids") or []:
            put(cid, "occasional", c.get("playlist"))
    for s in cfg.list("songs"):
        for cid in s.get("official_channel_ids") or []:
            put(cid, "official", s["title"])
    return roles


def entity_map(cfg: Config) -> dict[str, str]:
    """チャンネルID -> 同一アーティストのまとまり（1つの設定項目に並べたチャンネルは同一人物）。"""
    ent = {}
    for key, _ in SECTIONS:
        for e in cfg.list(key):
            for cid in e.get("channel_ids") or []:
                ent.setdefault(cid, f"{key}:{e['name']}")
    return ent


def dedupe(vids: set[str], by_id: dict[str, dict], entity_of) -> set[str]:
    """同じアーティストの同じ曲・同じバージョンは1本だけ残す（MV優先、TOPIC音源は後回し）。"""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    keep = set()
    for vid in vids:
        v = by_id.get(vid)
        if not v:
            keep.add(vid)
            continue
        base, tag = song_key(v)
        if not base:
            keep.add(vid)
            continue
        groups[(entity_of(v.get("channel_id")), base, tag)].append(v)
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


# --------------------------------------------------------------------------
# 振り分け先の計算
# --------------------------------------------------------------------------
def _apply_overrides(rule: dict, vids: set[str], prefix: str = "") -> set[str]:
    vids |= set(rule.get(f"{prefix}include_video_ids") or [])
    vids -= set(rule.get(f"{prefix}exclude_video_ids") or [])
    return vids


def rule_titles(cfg: Config) -> dict[str, dict]:
    """再生リストタイトル -> {"playlist_id": 固定ID or None}"""
    out = {}
    for t in cfg.list("top_favorites"):
        out[t.get("playlist") or cfg.title("own", t["name"])] = {"playlist_id": t.get("playlist_id")}
        out[t.get("other_playlist") or cfg.title("other", t["name"])] = {"playlist_id": t.get("other_playlist_id")}
    for f in cfg.list("favorites"):
        out[f.get("playlist") or cfg.title("own", f["name"])] = {"playlist_id": f.get("playlist_id")}
    for s in cfg.list("songs"):
        out[s.get("playlist") or s["title"]] = {"playlist_id": s.get("playlist_id")}
    for c in cfg.list("combos"):
        out[c["playlist"]] = {"playlist_id": c.get("playlist_id")}
    ids = cfg.raw.get("playlist_ids") or {}
    for kind in ("unsorted", "misc"):
        out[cfg.title(kind)] = {"playlist_id": ids.get(kind)}
    return out


def build_targets(cfg: Config, videos: list[dict], roles: dict[str, dict],
                  handles: dict[str, str] | None = None) -> "OrderedDict[str, set[str]]":
    """再生リストタイトル -> 入るべき動画ID集合。"""
    mcfg = cfg.section("music")
    use_desc = cfg.section("matching")["use_description"]
    do_dedupe = cfg.section("dedupe")["enabled"]
    ignore = set(cfg.list("ignore_video_ids"))
    music = [v for v in videos if v["id"] not in ignore and is_music(v, mcfg)]
    by_id = {v["id"]: v for v in music}
    ents = entity_map(cfg)
    category_channels = {c for c, r in roles.items() if r["role"] in ("top", "favorite", "occasional")}
    targets: OrderedDict[str, set[str]] = OrderedDict()

    def ent_of(extra=None):
        return lambda cid: (extra or {}).get(cid) or ents.get(cid) or cid

    def finish(rule, vids, entity_of, prefix=""):
        if do_dedupe:
            vids = dedupe(vids, by_id, entity_of)
        return _apply_overrides(rule, vids, prefix) - ignore

    def text(v):
        return (v.get("title") or "") + ("\n" + (v.get("description") or "") if use_desc else "")

    def related(v, refs, aliases):
        return (refs and matches_channel_ref(text(v), refs)) or (aliases and matches_alias(text(v), aliases))

    # 特にお気に入りのチャンネル：本人名義は全曲、他チャンネルの関係曲は「その他○○」
    for t in cfg.list("top_favorites"):
        own_ids = set(t.get("channel_ids") or [])
        refs = channel_refs(own_ids, handles)
        aliases = t.get("aliases") or []
        own = {v["id"] for v in music if v.get("channel_id") in own_ids}
        other = {v["id"] for v in music
                 if v.get("channel_id") not in own_ids and related(v, refs, aliases)
                 and not has_keyword(v.get("title"), t.get("exclude_keywords"))}
        targets[t.get("playlist") or cfg.title("own", t["name"])] = finish(t, own, ent_of())
        targets[t.get("other_playlist") or cfg.title("other", t["name"])] = finish(t, other, ent_of(), "other_")

    # お気に入りの曲：公式＋指定チャンネルのカバー
    for s in cfg.list("songs"):
        aliases = list(s.get("aliases") or []) or [s["title"]]
        official = set(s.get("official_channel_ids") or [])
        allowed = category_channels | official
        hit = {v["id"] for v in music
               if v.get("channel_id") in allowed and matches_alias(v.get("title"), aliases)
               and not has_keyword(v.get("title"), s.get("exclude_keywords"))}
        official_ent = {cid: "official:" + s["title"] for cid in official if cid not in ents}
        targets[s.get("playlist") or s["title"]] = finish(s, hit, ent_of(official_ent))

    # たまに聴くチャンネル × コラボ相手
    for c in cfg.list("combos"):
        chans = set(c.get("channel_ids") or [])
        refs = channel_refs(c.get("partner_channel_ids"), handles)
        aliases = c.get("partner_aliases") or []
        hit = {v["id"] for v in music
               if v.get("channel_id") in chans and (not (refs or aliases) or related(v, refs, aliases))
               and not has_keyword(v.get("title"), c.get("exclude_keywords"))}
        targets[c["playlist"]] = finish(c, hit, ent_of({cid: "combo:" + c["playlist"] for cid in chans}))
    return targets


def handle_channel_ids(cfg: Config) -> set[str]:
    """@ハンドルを調べる必要のあるチャンネル。"""
    ids = set()
    for t in cfg.list("top_favorites"):
        ids |= set(t.get("channel_ids") or [])
    for c in cfg.list("combos"):
        ids |= set(c.get("partner_channel_ids") or [])
    return ids


def managed_titles(cfg: Config, targets) -> set[str]:
    s = set(targets) | {cfg.title("misc")}
    for f in cfg.list("favorites"):
        s.add(f.get("playlist") or cfg.title("own", f["name"]))
    return s


def favorite_own_title(cfg: Config, channel_id: str | None) -> str | None:
    for f in cfg.list("favorites"):
        if channel_id in (f.get("channel_ids") or []):
            return f.get("playlist") or cfg.title("own", f["name"])
    return None


def needs_unsorted(cfg: Config, v: dict, roles: dict, auto_hits: set[str]) -> bool:
    """新着動画を「未仕分け」に入れるべきか。"""
    if not is_music(v, cfg.section("music")) or v["id"] in set(cfg.list("ignore_video_ids")):
        return False
    role = roles.get(v.get("channel_id"), {}).get("role")
    if role == "favorite":
        return True  # お気に入りかどうかは人が判断
    if role == "occasional":
        return not auto_hits
    return False


def promotion_candidates(cfg: Config, misc_entries: list[dict], videos: list[dict],
                         roles: dict) -> list[dict]:
    """「その他の曲」のうち、本家/カバーが min_versions 曲以上ある曲（同じ人の同じ音源は1曲と数える）。"""
    p = cfg.section("promotion")
    if not p["enabled"]:
        return []
    mcfg = cfg.section("music")
    ents = entity_map(cfg)
    pool_channels = {c for c, r in roles.items() if r["role"] in ("top", "favorite", "occasional")}
    pool = [(nz(v.get("title")), ents.get(v.get("channel_id"), v.get("channel_id")), song_key(v))
            for v in videos if v.get("channel_id") in pool_channels and is_music(v, mcfg)]
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
