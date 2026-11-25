"""毎日の同期処理本体。  python -m ytpl.sync [--dry-run]

このプログラムは再生リストへの「追加」だけを行い、動画の削除は一切しない。
追加の提案は、Worker の画面で「追加」を選んだ曲だけを次の実行で再生リストに入れる（data/decisions.py）。
config.yaml に書き戻すのは「新しく作った再生リストのID」と「昇格した曲」だけで、
ruamel.yaml の round-trip によりコメント・クオート・並び順は保たれる。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import rules
from .api import ApiError, QuotaExceeded, YouTube, uploads_playlist_id
from .decisions import Decisions, Remote, RemoteError
from .store import EXTERNAL, Catalog, Config, Ledger, _plain, dump_yaml, load_yaml

JST = timezone(timedelta(hours=9))


class Report:
    def __init__(self):
        self.sections: dict[str, list[str]] = {}

    def add(self, section: str, line: str) -> None:
        self.sections.setdefault(section, []).append(line)

    def render(self, header: str) -> str:
        out = [header, ""]
        for sec, lines in self.sections.items():
            out.append(f"## {sec}（{len(lines)}）")
            out += [f"- {x}" for x in lines[:300]]
            if len(lines) > 300:
                out.append(f"- …ほか {len(lines) - 300} 件")
            out.append("")
        return "\n".join(out)


def _label(v: dict | None, vid: str) -> str:
    if not v:
        return vid
    return f"{v.get('title')} / {v.get('channel_title') or '?'} (https://youtu.be/{vid})"


def write_suggestions(md_path: Path, json_path: Path, suggestions: dict, now: datetime) -> None:
    """追加の提案を、読む用（Markdown）と Worker 用（JSON）に書き出す。"""
    import json

    groups = sorted(suggestions.values(), key=lambda g: g["name"])
    body = [f"# 追加の提案 {now:%Y-%m-%d %H:%M} JST", "",
            "すでに別の再生リストに入っている曲や、お気に入りの曲の確認候補のうち、"
            "まだ入れていない（消してもいない）ものです。自動では追加しません。",
            "ミックスAPI（Worker）の「追加の提案」の画面で、曲ごとに「追加」か「却下」を選んでください。"
            "追加にした曲は次回の同期で再生リストに入り、却下にした曲はその提案先にはもう提案しません"
            "（どちらも選ばなかった曲は、次回も提案されます）。", ""]
    for g in groups:
        body.append(f"## 提案先: {g['name']}（{len(g['items'])}）")
        for it in g["items"]:
            note = f"理由: {it['reason']}" + (f" / 今入っている: {'、'.join(it['in'])}" if it["in"] else "")
            body.append(f"- {it['title']} / {it['channel']} (https://youtu.be/{it['id']}) — {note}")
        body.append("")
    if not groups:
        body.append("提案はありません。")
    md_path.write_text("\n".join(body) + "\n", encoding="utf-8")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    out = {"version": 1, "generated_at": now.isoformat(timespec="seconds"), "groups": groups}
    lines = ["{", f'  "version": 1,', f'  "generated_at": {json.dumps(out["generated_at"])},', '  "groups": [']
    gs = [json.dumps(g, ensure_ascii=False, separators=(",", ":")) for g in groups]
    lines += [f"    {g}{',' if n < len(gs) - 1 else ''}" for n, g in enumerate(gs)]
    lines += ["  ]", "}"]
    json_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def handles_of(state: dict) -> dict[str, list[str]]:
    """チャンネルID -> 今と過去の @ハンドル（概要欄では古いハンドルのまま書かれていることがあるため、両方で照合する）。"""
    out = {}
    for c, h in (state.get("handles") or {}).items():
        hs = ([h["handle"]] if h.get("handle") else []) + [p["handle"] for p in h.get("past") or [] if p.get("handle")]
        if hs:
            out[c] = list(dict.fromkeys(hs))
    return out


def refresh_handles(yt: YouTube, cfg: Config, state: dict, today: str, rep: Report, roles: dict) -> None:
    """@ハンドルを毎日取り直す（50チャンネルで1ユニット）。変わったら、前のハンドルを past に残す。

    state["handles"][チャンネルID] = {"handle": 今のハンドル, "checked": 日付,
                                       "past": [{"handle": 前のハンドル, "until": 変更に気づいた日}]}
    見つからなかったチャンネル（削除・一時的な失敗）は、前の記録をそのまま残す。
    """
    hcache = {c: dict(v) for c, v in (state.get("handles") or {}).items()}
    need = sorted(c for c in rules.handle_channel_ids(cfg) if (hcache.get(c) or {}).get("checked") != today)
    if need:
        info = yt.channels_info(need)
        for c in need:
            cur = hcache.setdefault(c, {})
            if c in info:
                new, old = info[c].get("handle"), cur.get("handle")
                if old and new != old:
                    past = [p for p in cur.get("past") or [] if p.get("handle") != old]
                    cur["past"] = past + [{"handle": old, "until": today}]
                    name = (roles.get(c) or {}).get("name") or info[c].get("title") or c
                    rep.add("@ハンドルの変更（前のハンドルも引き続き照合に使います）", f"{name}: {old} → {new or '（なし）'}")
                if new:
                    cur["past"] = [p for p in cur.get("past") or [] if p.get("handle") != new]
                    if not cur["past"]:
                        cur.pop("past")
                cur["handle"] = new
            cur["checked"] = today
    state["handles"] = hcache


def _collect(pages, known: set, incremental: bool) -> list[str]:
    """ページ（新しい順）から未知の動画IDを集める。差分取得では、未知のIDがないページで止める。"""
    out: list[str] = []
    for page in pages:
        fresh = [x["video_id"] for x in page if x["video_id"] not in known and x["video_id"] not in out]
        out += fresh
        if incremental and not fresh:
            break
    return out


def update_channel(yt: YouTube, catalog: Catalog, cid: str, today: str, rep: Report,
                   allow_search: bool = False) -> list[str]:
    """チャンネルの動画を差分取得。新着ID（初回取込時は空）を返す。

    通常はアップロード一覧（UU…）を読む。TOPIC チャンネルなど、アップロード一覧が見つからない場合は
    次の順に代わりの方法を試し、うまくいった方法をカタログに記録して次回から使う。
      1. チャンネル情報に書かれたアップロード一覧のID
      2. チャンネルページの棚（アルバム・シングルなど）に並ぶ再生リスト（その動画のうち、このチャンネルのもの）
      3. 検索（100ユニット。allow_search のときだけ。新しい順に最大50件）
    """
    ch = catalog.channel(cid)
    first = not ch.get("initialized")
    known = set(ch["videos"])
    source = ch.get("source") or "uploads"
    new_ids: list[str] = []
    owner_check = False

    def try_uploads(pid):
        return _collect(yt.playlist_pages(pid), known, not first)

    try:
        if source == "uploads":
            try:
                new_ids = try_uploads(uploads_playlist_id(cid))
            except ApiError as e:
                if e.status != 404:
                    raise
                source = "related"
        if source == "related":
            pid = ch.get("uploads_id") or yt.uploads_id(cid)
            try:
                if not pid or pid == uploads_playlist_id(cid):
                    raise ApiError(404, "playlistNotFound")
                new_ids = try_uploads(pid)
                ch["uploads_id"] = pid
            except ApiError as e:
                if e.status != 404:
                    raise
                source = "sections"
        if source == "sections":
            pls = yt.section_playlists(cid)
            if pls:
                owner_check = True
                for pid in pls:
                    try:
                        new_ids += [v for v in _collect(yt.playlist_pages(pid), known | set(new_ids), False)]
                    except ApiError:
                        continue
            else:
                source = "search"
        if source == "search":
            if not allow_search:
                rep.add("次回以降に取り込むチャンネル（予約枠のため）", f"{cid}（検索で取得するチャンネル）")
                ch["source"] = "search"
                catalog.dirty.add(cid)
                return []
            after = None if first else (ch.get("last_checked") and f"{ch['last_checked']}T00:00:00Z")
            new_ids = [v for v in yt.search_channel_video_ids(cid, after) if v not in known]
            owner_check = True
    except ApiError as e:
        rep.add("警告", f"チャンネル {cid} の動画一覧を取得できません: {e.reason or e.status}")
        return []

    if source != (ch.get("source") or "uploads"):
        rep.add("動画一覧の取得方法を変更", f"{cid}: {source}（アップロード一覧が見つからないため）")
    ch["source"] = source
    for v in yt.videos(new_ids):
        if owner_check and v.get("channel_id") != cid:
            continue  # アルバム等に入っている、ほかのチャンネルの動画は除く
        catalog.add(cid, v)
    ch["initialized"] = ch.get("initialized") or today
    ch["last_checked"] = today
    catalog.dirty.add(cid)
    return [] if first else [v for v in new_ids if v in catalog]


def create_missing_playlists(cfg: Config, yt: YouTube, playlists: list[dict], alive: dict, ledger: Ledger,
                             rep: Report, dry_run: bool) -> None:
    """config.yaml にある、id のない再生リストをすぐに作る（読み取りや昇格より先）。

    作ったIDは config.yaml に書き込む。前回作ったのにIDを書き戻せなかった場合（push失敗など）に
    二重に作らないよう、このツールが作った同名の再生リストで config.yaml から参照されていないものがあれば、それを使う。
    """
    from .api import CREATED_MARK

    declared = rules.declared_playlists(cfg)
    referenced = {p.id for p in declared if p.id}
    reusable: dict[str, str] = {}
    for p in playlists:
        if p.get("description") == CREATED_MARK and p["id"] not in referenced:
            reusable.setdefault(p["title"], p["id"])
    for pl in declared:
        if pl.id or not pl.creatable:
            continue
        if pl.name in reusable:
            pid = reusable.pop(pl.name)
            rep.add("作成済みの再生リストを使用（config.yaml に id を追記）", f"{pl.name}: {pid}")
        elif dry_run:
            rep.add("新しく作る再生リスト（予定）", pl.name)
            continue
        else:
            pid = yt.create_playlist(pl.name, cfg.section("playlist_privacy"))
            rep.add("新しく作った再生リスト（config.yaml に id を追記）", f"{pl.name}: {pid}")
        cfg.set_playlist_id(pl.path, pid, pl.name)
        alive[pid] = pl.name
        ledger.doc(pid, pl.name)


def run(config_path="config.yaml", data_dir="data", report_path="reports/latest.md",
        dry_run=False, create_only=False, yt: YouTube | None = None, now: datetime | None = None,
        remote: "Remote | None | str" = "env") -> str:
    now = now or datetime.now(JST)
    today = now.date().isoformat()
    data = Path(data_dir)
    state_path = data / "state.yaml"
    state = _plain(load_yaml(state_path, {}) or {})
    cfg = Config(config_path)
    errors = rules.validate(cfg)
    if errors:
        raise SystemExit("config.yaml の設定エラー:\n" + "\n".join(f"- {e}" for e in errors))
    yt = yt or YouTube.from_env(cfg.budget)
    reserve = int(cfg.section("quota")["write_reserve"])
    catalog = Catalog(data / "catalog")
    ledger = Ledger(data / "playlists")
    rep = Report()
    roles = rules.channel_roles(cfg)
    quota_hit = False
    new_ids: list[str] = []
    alive: dict[str, str] = {}   # 再生リストID -> 実際のタイトル
    playlists: list[dict] = []
    unsorted_add: set[str] = set()
    suggestions: dict[str, dict] = {}   # 提案先の key -> {"name", "id", "items": [...]}

    def resolve(pl: rules.PL) -> str | None:
        """再生リストIDを決める（名前では探さない）。"""
        return pl.id if pl.id and pl.id in alive else None

    def finish(mode: str, n_videos: int) -> str:
        state["last_run"] = now.isoformat(timespec="seconds")
        header = (f"# 同期レポート {now:%Y-%m-%d %H:%M} JST（{mode}{'・ドライラン' if dry_run else ''}）\n\n"
                  f"- APIクォータ使用量（推定）: {yt.used} / {cfg.budget}\n"
                  f"- 監視チャンネル数: {len(roles)} / 曲として扱う動画数: {n_videos}\n"
                  f"- 新着: {len(set(new_ids))} 件 / 未仕分けへ: {len(unsorted_add)} 件")
        text = rep.render(header)
        if not dry_run:
            catalog.save()
            ledger.save()
            cfg.save()
            if not create_only:
                from . import mix_index
                mix_index.write(data / "mix" / "index.json", mix_index.build(cfg, ledger, now.isoformat(timespec="seconds")))
            dump_yaml(state_path, state)
            rp = Path(report_path)
            if create_only:   # 作成だけの実行は、毎日のレポートを上書きしない
                rp = rp.with_name(rp.stem + "-create" + rp.suffix)
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(text, encoding="utf-8")
            if not create_only:
                write_suggestions(rp.with_name("suggestions.md"), data / "mix" / "suggestions.json",
                                  suggestions, now)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(text + "\n")
        return text

    # 0. 再生リストの作成を最優先（人の仕分け作業をすぐ始められるように）
    try:
        playlists = yt.my_playlists()
        alive.update({p["id"]: p["title"] for p in playlists})
        create_missing_playlists(cfg, yt, playlists, alive, ledger, rep, dry_run)
        # config.yaml のどこにも載っていない再生リストは、管理外として ignore_playlists に記録する
        from .pull_config import add_unknown_to_ignore
        for p in add_unknown_to_ignore(cfg, playlists):
            rep.add("管理外として ignore_playlists に追加（要確認）", f"{p['title']}: {p['id']}")
    except QuotaExceeded as e:
        rep.add("警告", f"再生リストの作成中にクォータ上限に達しました: {e}")
        return finish("再生リストの作成", 0)
    if create_only:
        return finish("再生リストの作成", 0)

    # 追加の提案に対する決定（Worker の画面で選んだもの）を読む。クォータは使わない
    decisions = Decisions(data / "decisions.yaml")
    remote = Remote.from_env() if remote == "env" else remote
    remote_ok = False
    if remote:
        try:
            decisions.merge(remote.fetch())
            remote_ok = True
        except RemoteError as e:
            rep.add("警告", f"追加の提案の決定を Worker から読めませんでした（手元の記録だけで続けます）: {e}")

    fixed = rules.fixed_playlists(cfg)
    ignored = rules.ignored_playlist_ids(cfg)
    managed_ids = {p.id for p in rules.declared_playlists(cfg) if p.id}

    def optional_ok(cost=1) -> bool:
        """任意の読み取りは、書き込み用の予約枠（quota.write_reserve）を残して打ち切る。"""
        return yt.remaining - cost >= reserve

    try:
        # 1. 管理対象の再生リストの中身をスナップショット（＝バックアップ）。管理外（ignore_playlists）はしない
        for p in playlists:
            if p["id"] in managed_ids and p["id"] not in ignored:
                ledger.apply_snapshot(p["id"], p["title"], yt.playlist_items(p["id"]), today)
        for t in ledger.mark_deleted_playlists(set(alive), today):
            rep.add("警告", f"再生リスト「{t}」がYouTube上から消えています（台帳は保持）")
        for doc in ledger.docs.values():
            for e in doc["items"].values():
                if e.get("missing_since") == today:
                    rep.add("再生リストから消えた曲", f"{doc['title']}: {e.get('title')} ({e['video_id']})")
                if e.get("unavailable_since") == today:
                    rep.add("削除・非公開になった曲", f"{doc['title']}: {e.get('title')} ({e['video_id']})")

        # 2. 監視チャンネルの新着取得。取り込み済みのチャンネルを先に、新しく追加したチャンネルの全件取得は予約枠の範囲で
        known = [c for c in roles if (catalog.channels.get(c) or {}).get("initialized")]
        fresh = [c for c in roles if c not in known]
        for cid in known:
            new_ids += update_channel(yt, catalog, cid, today, rep, allow_search=optional_ok(101))
        deferred = []
        for cid in fresh:
            if not optional_ok(50):
                deferred.append(roles[cid]["name"] or cid)
                continue
            update_channel(yt, catalog, cid, today, rep, allow_search=optional_ok(150))
        if deferred:
            rep.add("次回以降に取り込むチャンネル（予約枠のため）", "、".join(dict.fromkeys(deferred)))

        # 2b. @ハンドルの取得（毎日。過去のハンドルも残す）
        refresh_handles(yt, cfg, state, today, rep, roles)

        # 3. 特にお気に入りの関係曲を検索（任意・100ユニット/回。予約枠を残せないときは行わない）
        searches = dict(state.get("search_after") or {})
        for e in cfg.list("top_favorites"):
            if not e.get("search"):
                continue
            if not optional_ok(101):
                rep.add("今回行わなかった検索（予約枠のため）", e["name"])
                continue
            after = searches.get(e["name"]) or (now - timedelta(days=3)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            found_ids = [i for i in yt.search_video_ids(e.get("search_query") or e["name"], after) if i not in catalog]
            toks = rules.ref_tokens(rules.ids(e.get("channels")), handles_of(state))
            keys = rules.alias_keys(e.get("aliases"))
            for v in yt.videos(found_ids):
                text = rules.nfkc(f"{v['title']}\n{v['description']}")
                if any(t in text for t in toks) or any(k in rules.nz(text) for k in keys):
                    catalog.add(EXTERNAL, v)
                    new_ids.append(v["id"])
            searches[e["name"]] = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["search_after"] = searches

        # 4. 「未仕分け」「その他の曲」にある監視外チャンネルの動画も情報を取得
        extra = []
        for k, pl in fixed.items():
            if k == "rejected":
                continue
            if (pid := resolve(pl)):
                extra += [e["video_id"] for e in ledger.present(pid) if e["video_id"] not in catalog]
        for v in yt.videos(sorted(set(extra))):
            catalog.add(EXTERNAL, v)
    except QuotaExceeded as e:
        quota_hit = True
        rep.add("警告", f"読み取り中にクォータ上限に達しました: {e}")

    # 最近の新曲（優先して追加する）。30日で期限切れ
    recent = {k: v for k, v in (state.get("recent_new") or {}).items()
              if v >= (now - timedelta(days=30)).date().isoformat()}
    for vid in new_ids:
        recent.setdefault(vid, today)
    state["recent_new"] = recent

    idx = rules.Index(cfg, catalog.all())

    # 5. 「その他の曲」→「お気に入りの曲」への昇格（config.yaml の songs に追加）
    n_songs = len(cfg.songs())
    misc_pid = resolve(fixed["misc"])
    if not quota_hit and misc_pid:
        for c in rules.promotion_candidates(cfg, ledger.present(misc_pid), idx, roles):
            cfg.append_song({"name": c["name"], "playlist": {"name": c["name"]}, "aliases": c["aliases"],
                             "promoted_at": today, "promoted_versions": c["versions"]})
            rep.add("お気に入りの曲に昇格", f"{c['name']}（{c['versions']} 曲）")
    promoted_keys = {f"song:{i}" for i in range(n_songs, len(cfg.songs()))}

    # 6. 振り分け先を計算
    targets = rules.build_targets(cfg, idx, roles, handles_of(state))
    pids = {}
    for tg in targets:
        pid = resolve(tg.pl)
        if tg.pl.id and not pid:
            rep.add("警告", f"「{tg.pl.name}」の再生リストID {tg.pl.id} が見つかりません（スキップ）")
        if pid and alive.get(pid) != tg.pl.name:
            rep.add("名前の不一致（IDで照合しています）", f"設定「{tg.pl.name}」/ YouTube「{alive[pid]}」")
        pids[tg.pl.key] = pid
    for k, pl in fixed.items():
        pids[k] = resolve(pl)
        if pl.id and not pids[k]:
            rep.add("警告", f"「{pl.name}」の再生リストID {pl.id} が見つかりません")

    managed = [p for k, p in pids.items() if p and k not in ("unsorted", "rejected")]
    auto_n, cand_n = {}, {}
    for tg in targets:
        for v in tg.auto:
            auto_n[v] = auto_n.get(v, 0) + 1
        for v in tg.cand:
            cand_n[v] = cand_n.get(v, 0) + 1
    for vid in dict.fromkeys(new_ids):
        if any(ledger.status(p, vid) == "present" for p in managed):
            continue  # すでに仕分け済み
        if rules.needs_unsorted(vid, roles, idx, auto_n.get(vid, 0), cand_n.get(vid, 0)):
            unsorted_add.add(vid)

    # 7. 追加の優先順位
    #   1 新曲を「未仕分け」へ / 2 手動指定（include_video_ids） / 3 新曲を○○チャンネルへ / 4 新曲を特別再生リストへ
    #   5 新曲を「お気に入りの曲」へ / 6 取り込み待ち（3〜5と同じ順） / 7 昇格した曲。同じ順位の中は新しい順
    SUB = {"own": 0, "other": 0, "special": 1, "song": 2}
    LABEL = {1: "新曲→未仕分け", 2: "手動指定", 3: "新曲→○○チャンネル", 4: "新曲→特別再生リスト",
             5: "新曲→お気に入りの曲", 6: "取り込み待ち", 7: "昇格した曲"}

    def rank(pl: rules.PL, vid: str, inc: set) -> tuple[int, int]:
        if pl.key == "unsorted":
            return 1, 0
        if pl.key in promoted_keys:
            return 7, 0
        if vid in inc:
            return 2, 0
        sub = SUB.get(pl.key.split(":")[0], 3)
        return (3 + sub if vid in recent else 6), sub

    def pub(vid):
        return ((catalog.get(vid) or {}).get("published_at")) or ""

    plan_items = []
    for pl, vids, inc in [(fixed["unsorted"], unsorted_add, set())] + [(tg.pl, tg.auto, tg.inc) for tg in targets]:
        pid = pids.get(pl.key)
        if pl.id and not pid:
            continue
        for vid in vids:
            if (ledger.status(pid, vid) if pid else None) in (None, "pending"):
                plan_items.append((rank(pl, vid, inc), pub(vid), pl, vid))
    # 2' Worker の画面で「追加」にした曲（手動指定と同じ順位）
    by_pid = {pids[tg.pl.key]: tg.pl for tg in targets if pids.get(tg.pl.key) and pids[tg.pl.key] not in ignored}
    planned = {(pl.key, vid) for _, _, pl, vid in plan_items}
    decided: dict[tuple[str, str], dict] = {}
    for rec in decisions.pending_adds():
        pid, vid = rec["target"], rec["video"]
        pl = by_pid.get(pid)
        if not pl:
            if not dry_run:
                decisions.mark(rec, "invalid", now.isoformat(timespec="seconds"), "提案先が管理対象の再生リストではありません")
            rep.add("提案から追加できなかった曲", f"{rec.get('target_name') or pid} ← {rec.get('title') or vid}: 提案先が管理対象ではありません")
            continue
        if ledger.status(pid, vid) == "present":
            if not dry_run:
                decisions.mark(rec, "already", now.isoformat(timespec="seconds"))
            continue
        decided[(pid, vid)] = rec
        if (pl.key, vid) in planned:
            plan_items = [x for x in plan_items if not (x[2].key == pl.key and x[3] == vid)]
        plan_items.append(((2, 0), pub(vid), pl, vid))
    plan_items.sort(key=lambda x: x[1], reverse=True)       # 新しい順
    plan_items.sort(key=lambda x: x[0])                      # 優先順位（安定ソートなので新しい順が保たれる）
    plan = [(pl, vid, r[0]) for r, _, pl, vid in plan_items]

    def label(pl, vid, r):
        return "提案から追加" if (pids.get(pl.key), vid) in decided else LABEL[r]

    def title_of(vid, pid):
        return (catalog.get(vid) or {}).get("title") or (decided.get((pid, vid)) or {}).get("title")

    # 8. 実行（追加のみ）
    if not quota_hit:
        try:
            for pl, vid, _ in plan:
                if dry_run:
                    continue
                pid = pids.get(pl.key)
                if pid:
                    e = ledger.entry(pid, vid)
                    v = catalog.get(vid) or {}
                    e.setdefault("title", v.get("title") or title_of(vid, pid))
                    e.setdefault("channel_id", v.get("channel_id"))
                    e.setdefault("channel_title", v.get("channel_title"))
                    e.setdefault("first_seen", today)
                    e["status"] = "pending"
            for pl, vid, r in plan:
                pid = pids.get(pl.key)
                if not pid:
                    # ここで作るのは昇格した曲の再生リストだけ（ほかは 0. で作成済み）
                    if not pl.creatable or pl.id:
                        continue
                    if dry_run:
                        pid = f"DRYRUN:{pl.key}"
                    else:
                        pid = yt.create_playlist(pl.name, cfg.section("playlist_privacy"))
                        cfg.set_playlist_id(pl.path, pid, pl.name)
                        pl.id = pid
                    alive[pid] = pl.name
                    pids[pl.key] = pid
                    ledger.doc(pid, pl.name)
                    rep.add("新しく作った再生リスト（config.yaml に id を追記）", f"{pl.name}: {pid}")
                if dry_run:
                    rep.add("追加（予定）", f"[{label(pl, vid, r)}] {pl.name} ← {_label(catalog.get(vid), vid)}")
                    continue
                e = ledger.entry(pid, vid)
                e.setdefault("title", title_of(vid, pid))
                e.setdefault("first_seen", today)
                try:
                    e["item_id"] = yt.add_item(pid, vid)
                    e["status"], e["added_by_bot"] = "present", today
                    e.pop("error", None)
                    rep.add("追加", f"[{label(pl, vid, r)}] {pl.name} ← {_label(catalog.get(vid), vid)}")
                except ApiError as err:
                    e["status"], e["error"] = "failed", err.reason or str(err.status)
                    rep.add("追加に失敗", f"{pl.name} ← {vid}: {e['error']}")
        except QuotaExceeded:
            quota_hit = True
            left = sum(1 for pl, v, _ in plan if ledger.status(pids.get(pl.key) or "", v) in ("pending", None))
            rep.add("警告", f"クォータ上限のため {left} 件を次回に持ち越します（台帳では pending）")

    # 8a. 決定の反映結果を記録する（クォータ切れで入れられなかった「追加」は、次回また試す）
    if not dry_run:
        at = now.isoformat(timespec="seconds")
        for (pid, vid), rec in decided.items():
            st = ledger.status(pid, vid)
            if st == "present":
                decisions.mark(rec, "added", at)
            elif st == "failed":
                decisions.mark(rec, "failed", at, (ledger.entry(pid, vid) or {}).get("error"))
                rep.add("提案から追加できなかった曲", f"{rec.get('target_name') or pid} ← {rec.get('title') or vid}")
        for rec in decisions.pending_rejects():
            decisions.mark(rec, "recorded", at)

    # 8b. ほかの再生リストへの追加の提案（自動では追加しない）
    #   すでにどれかの管理対象の再生リストに入っている曲が、別の再生リストの確認候補に当たっているもの。
    #   お気に入りの曲は、どこにも入っていない過去の確認候補も出す。
    #   出さないもの：提案先に入っている・入っていた（自分で消した）曲、画面で却下・追加にした曲、
    #   （旧方式）「提案を却下」の再生リストに入れた曲
    rejected = {e["video_id"] for e in ledger.present(pids["rejected"])} if pids.get("rejected") else set()
    present_in: dict[str, list[str]] = {}
    for k, pid in pids.items():
        if pid and k not in ("unsorted", "rejected"):
            for e in ledger.present(pid):
                present_in.setdefault(e["video_id"], []).append(alive.get(pid) or pid)
    suggestions = {}
    fresh = set(new_ids)
    for tg in targets:
        pid = pids.get(tg.pl.key)
        for vid in sorted(tg.cand - fresh, key=pub, reverse=True):
            if (pid and ledger.status(pid, vid) is not None) or vid in rejected:
                continue
            if decisions.blocks_suggestion(pid, vid):
                continue
            where = [n for n in present_in.get(vid, []) if n != tg.pl.name]
            if not where and not tg.pl.key.startswith("song:"):
                continue
            v = catalog.get(vid) or {}
            g = suggestions.setdefault(tg.pl.key, {"name": tg.pl.name, "id": pid, "items": []})
            g["items"].append({"id": vid, "title": v.get("title"), "channel": v.get("channel_title"),
                               "published_at": v.get("published_at"), "reason": tg.why.get(vid, "確認候補"),
                               "in": where})
    for g in suggestions.values():
        for it in g["items"]:
            note = f"理由: {it['reason']}" + (f" / 今入っている: {'、'.join(it['in'])}" if it["in"] else "")
            rep.add("ほかの再生リストへの追加の提案（ミックスAPIの「追加の提案」の画面で追加・却下を選べます）",
                    f"{g['name']} ← {it['title']} / {it['channel']} (https://youtu.be/{it['id']}) — {note}")

    # 9. すでに再生リスト内にある重複（自動では消さず、レポートのみ）
    if cfg.section("dedupe")["enabled"]:
        ents = rules.entity_map(cfg)
        for tg in targets:
            pid = pids.get(tg.pl.key)
            if not pid:
                continue
            present = [e["video_id"] for e in ledger.present(pid)]
            for g in rules.duplicate_groups(present, idx, lambda c: ents.get(c) or c):
                rep.add("重複の可能性（手動で確認）", f"{tg.pl.name}: " + " / ".join(_label(catalog.get(v), v) for v in g))

    # 10. 決定の記録を保存し、反映結果を Worker に返す
    if not dry_run:
        decisions.save()
        payload = decisions.ack_payload()
        if remote and remote_ok and payload:
            try:
                remote.ack(payload)
            except RemoteError as e:
                rep.add("警告", f"反映結果を Worker に送れませんでした（次回また送ります）: {e}")

    return finish("通常", len(idx.r))


def main(argv=None):
    ap = argparse.ArgumentParser(description="YouTube再生リスト自動仕分け")
    ap.add_argument("--dry-run", action="store_true", help="書き込みせず予定だけ表示")
    ap.add_argument("--create-only", action="store_true",
                    help="config.yaml にある id のない再生リストを作るだけ（config.yaml を変更したときに使う）")
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args(argv)
    print(run(config_path=a.config, dry_run=a.dry_run, create_only=a.create_only))


if __name__ == "__main__":
    sys.exit(main())
