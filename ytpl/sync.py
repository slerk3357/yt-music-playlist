"""毎日の同期処理本体。  python -m ytpl.sync [--dry-run]

このプログラムは再生リストへの「追加」だけを行い、動画の削除は一切しない。
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


def handles_of(state: dict) -> dict[str, str]:
    return {c: h["handle"] for c, h in (state.get("handles") or {}).items() if h.get("handle")}


def update_channel(yt: YouTube, catalog: Catalog, cid: str, today: str, rep: Report) -> list[str]:
    """チャンネルのアップロード一覧を差分取得。新着ID（初回取込時は空）を返す。"""
    ch = catalog.channel(cid)
    first = not ch.get("initialized")
    known = set(ch["videos"])
    new_ids: list[str] = []
    try:
        for page in yt.playlist_pages(uploads_playlist_id(cid)):
            fresh = [x["video_id"] for x in page if x["video_id"] not in known]
            new_ids += fresh
            if not first and not fresh:
                break
    except ApiError as e:
        rep.add("警告", f"チャンネル {cid} の動画一覧を取得できません: {e.reason or e.status}")
        return []
    for v in yt.videos(new_ids):
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
        dry_run=False, create_only=False, yt: YouTube | None = None, now: datetime | None = None) -> str:
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
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(text + "\n")
        return text

    # 0. 再生リストの作成を最優先（人の仕分け作業をすぐ始められるように）
    try:
        playlists = yt.my_playlists()
        alive.update({p["id"]: p["title"] for p in playlists})
        create_missing_playlists(cfg, yt, playlists, alive, ledger, rep, dry_run)
    except QuotaExceeded as e:
        rep.add("警告", f"再生リストの作成中にクォータ上限に達しました: {e}")
        return finish("再生リストの作成", 0)
    if create_only:
        return finish("再生リストの作成", 0)

    fixed = rules.fixed_playlists(cfg)
    ignored = rules.ignored_playlist_ids(cfg)
    managed_ids = {p.id for p in rules.declared_playlists(cfg) if p.id}

    def optional_ok(cost=1) -> bool:
        """任意の読み取りは、書き込み用の予約枠（quota.write_reserve）を残して打ち切る。"""
        return yt.remaining - cost >= reserve

    try:
        # 1. 再生リストの中身をスナップショット（＝バックアップ）。管理対象を先に、管理外は予約枠の範囲で
        skipped = []
        for p in sorted((p for p in playlists if p["id"] not in ignored), key=lambda p: p["id"] not in managed_ids):
            if p["id"] not in managed_ids and not optional_ok():
                skipped.append(p["title"])
                continue
            ledger.apply_snapshot(p["id"], p["title"], yt.playlist_items(p["id"]), today)
        if skipped:
            rep.add("今回バックアップしなかった再生リスト（予約枠のため）", "、".join(skipped))
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
            new_ids += update_channel(yt, catalog, cid, today, rep)
        deferred = []
        for cid in fresh:
            if not optional_ok(50):
                deferred.append(roles[cid]["name"] or cid)
                continue
            update_channel(yt, catalog, cid, today, rep)
        if deferred:
            rep.add("次回以降に取り込むチャンネル（予約枠のため）", "、".join(dict.fromkeys(deferred)))

        # 2b. @ハンドルの取得（7日ごとに更新）
        hcache = dict(state.get("handles") or {})
        week_ago = (now - timedelta(days=7)).date().isoformat()
        stale = sorted(c for c in rules.handle_channel_ids(cfg)
                       if c not in hcache or hcache[c].get("checked", "") < week_ago)
        if stale:
            found = yt.channel_handles(stale)
            for c in stale:
                hcache[c] = {"handle": found.get(c), "checked": today}
        state["handles"] = hcache

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
        for pl in fixed.values():
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
        titles = [e.get("title") for e in ledger.present(misc_pid)]
        for c in rules.promotion_candidates(cfg, titles, idx, roles):
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

    managed = [p for k, p in pids.items() if p and k != "unsorted"]
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
    plan_items.sort(key=lambda x: x[1], reverse=True)       # 新しい順
    plan_items.sort(key=lambda x: x[0])                      # 優先順位（安定ソートなので新しい順が保たれる）
    plan = [(pl, vid, r[0]) for r, _, pl, vid in plan_items]

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
                    e.setdefault("title", v.get("title"))
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
                    rep.add("追加（予定）", f"[{LABEL[r]}] {pl.name} ← {_label(catalog.get(vid), vid)}")
                    continue
                e = ledger.entry(pid, vid)
                e.setdefault("title", (catalog.get(vid) or {}).get("title"))
                e.setdefault("first_seen", today)
                try:
                    e["item_id"] = yt.add_item(pid, vid)
                    e["status"], e["added_by_bot"] = "present", today
                    e.pop("error", None)
                    rep.add("追加", f"[{LABEL[r]}] {pl.name} ← {_label(catalog.get(vid), vid)}")
                except ApiError as err:
                    e["status"], e["error"] = "failed", err.reason or str(err.status)
                    rep.add("追加に失敗", f"{pl.name} ← {vid}: {e['error']}")
        except QuotaExceeded:
            quota_hit = True
            left = sum(1 for pl, v, _ in plan if ledger.status(pids.get(pl.key) or "", v) in ("pending", None))
            rep.add("警告", f"クォータ上限のため {left} 件を次回に持ち越します（台帳では pending）")

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
