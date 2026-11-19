"""分類ルール：どの動画がどの再生リストに入るべきか。"""
from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict

from .store import Config

ROLE_ORDER = ["top", "favorite", "occasional", "official"]


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


# --------------------------------------------------------------------------
def channel_roles(cfg: Config) -> dict[str, dict]:
    roles: dict[str, dict] = {}

    def put(cid, role, name):
        cur = roles.get(cid)
        if cur is None or ROLE_ORDER.index(role) < ROLE_ORDER.index(cur["role"]):
            roles[cid] = {"role": role, "name": name}

    for key, role in (("top_favorites", "top"), ("favorites", "favorite"), ("occasional", "occasional")):
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


def _apply_overrides(rule: dict, vids: set[str]) -> set[str]:
    vids |= set(rule.get("include_video_ids") or [])
    vids -= set(rule.get("exclude_video_ids") or [])
    return vids


def build_targets(cfg: Config, videos: list[dict], roles: dict[str, dict]) -> "OrderedDict[str, set[str]]":
    """再生リストタイトル -> 入るべき動画ID集合。"""
    mcfg = cfg.section("music")
    use_desc = cfg.section("matching")["use_description"]
    ignore = set(cfg.list("ignore_video_ids"))
    music = [v for v in videos if v["id"] not in ignore and is_music(v, mcfg)]
    category_channels = {c for c, r in roles.items() if r["role"] in ("top", "favorite", "occasional")}
    targets: OrderedDict[str, set[str]] = OrderedDict()

    def text(v):
        return (v.get("title") or "") + ("\n" + (v.get("description") or "") if use_desc else "")

    # 特にお気に入りのチャンネル：本人名義は全曲、他チャンネルの関係曲は「その他○○」
    for t in cfg.list("top_favorites"):
        own_ids = set(t.get("channel_ids") or [])
        aliases = list(t.get("aliases") or []) or [t["name"]]
        own = {v["id"] for v in music if v.get("channel_id") in own_ids}
        other = {v["id"] for v in music
                 if v.get("channel_id") not in own_ids and matches_alias(text(v), aliases)
                 and not has_keyword(v.get("title"), t.get("exclude_keywords"))}
        targets[t.get("playlist") or cfg.title("own", t["name"])] = _apply_overrides(t, own)
        targets[t.get("other_playlist") or cfg.title("other", t["name"])] = _apply_overrides(
            {"include_video_ids": t.get("other_include_video_ids"),
             "exclude_video_ids": t.get("other_exclude_video_ids")}, other)

    # お気に入りの曲：公式＋指定チャンネルのカバー
    for s in cfg.list("songs"):
        aliases = list(s.get("aliases") or []) or [s["title"]]
        allowed = category_channels | set(s.get("official_channel_ids") or [])
        hit = {v["id"] for v in music
               if v.get("channel_id") in allowed and matches_alias(v.get("title"), aliases)
               and not has_keyword(v.get("title"), s.get("exclude_keywords"))}
        targets[s.get("playlist") or s["title"]] = _apply_overrides(s, hit)

    # たまに聴くチャンネル × コラボ相手
    for c in cfg.list("combos"):
        chans = set(c.get("channel_ids") or [])
        partners = c.get("partner_aliases") or []
        hit = {v["id"] for v in music
               if v.get("channel_id") in chans and (not partners or matches_alias(text(v), partners))
               and not has_keyword(v.get("title"), c.get("exclude_keywords"))}
        targets[c["playlist"]] = _apply_overrides(c, hit)

    for k in list(targets):
        targets[k] -= ignore
    return targets


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
    """「その他の曲」のうち、本家/カバーが min_versions 曲以上ある曲。"""
    p = cfg.section("promotion")
    if not p["enabled"]:
        return []
    mcfg = cfg.section("music")
    pool_channels = {c for c, r in roles.items() if r["role"] in ("top", "favorite", "occasional")}
    pool = [nz(v.get("title")) for v in videos
            if v.get("channel_id") in pool_channels and is_music(v, mcfg)]
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
        count = sum(1 for t in pool if key in t)
        if count >= int(p["min_versions"]):
            out.append({"title": name, "aliases": [name], "versions": count})
    return out
