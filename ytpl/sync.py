"""毎日の同期処理本体。  python -m ytpl.sync [--dry-run]

このプログラムは再生リストへの「追加」だけを行い、動画の削除は一切しない。
config.yaml は読むだけで書き換えない。自動で決めたこと（昇格した曲・作成した
再生リストのID）は data/ 以下のファイルに記録する。
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


def run(config_path="config.yaml", data_dir="data", report_path="reports/latest.md",
        dry_run=False, yt: YouTube | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now(JST)
    today = now.date().isoformat()
    data = Path(data_dir)
    promoted_path, created_path, state_path = (data / "promoted_songs.yaml", data / "created_playlists.yaml",
                                               data / "state.yaml")
    promoted = _plain(load_yaml(promoted_path, []) or [])
    created = _plain(load_yaml(created_path, {}) or {})
    state = _plain(load_yaml(state_path, {}) or {})
    cfg = Config(config_path, extra_songs=promoted)
    errors = rules.validate(cfg)
    if errors:
        raise SystemExit("config.yaml の設定エラー:\n" + "\n".join(f"- {e}" for e in errors))
    yt = yt or YouTube.from_env(cfg.budget)
    catalog = Catalog(data / "catalog")
    ledger = Ledger(data / "playlists")
    rep = Report()
    roles = rules.channel_roles(cfg)
    fixed = rules.fixed_playlists(cfg)
    quota_hit = False
    new_ids: list[str] = []
    alive: dict[str, str] = {}   # 再生リストID -> 実際のタイトル

    def resolve(pl: rules.PL) -> str | None:
        """再生リストIDを決める（名前では探さない）。"""
        if pl.id:
            return pl.id if pl.id in alive else None
        rec = created.get(pl.key)
        return rec["id"] if rec and rec.get("id") in alive else None

    try:
        # 1. 自分の全再生リストをスナップショット（＝バックアップ）
        for pl in yt.my_playlists():
            alive[pl["id"]] = pl["title"]
            ledger.apply_snapshot(pl["id"], pl["title"], yt.playlist_items(pl["id"]), today)
        for t in ledger.mark_deleted_playlists(set(alive), today):
            rep.add("警告", f"再生リスト「{t}」がYouTube上から消えています（台帳は保持）")
        for doc in ledger.docs.values():
            for e in doc["items"].values():
                if e.get("missing_since") == today:
                    rep.add("再生リストから消えた曲", f"{doc['title']}: {e.get('title')} ({e['video_id']})")
                if e.get("unavailable_since") == today:
                    rep.add("削除・非公開になった曲", f"{doc['title']}: {e.get('title')} ({e['video_id']})")

        # 2. 監視チャンネルの新着取得
        for cid in roles:
            new_ids += update_channel(yt, catalog, cid, today, rep)

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

        # 3. 特にお気に入りの関係曲を検索（任意・100ユニット/回）
        searches = dict(state.get("search_after") or {})
        for e in cfg.list("top_favorites"):
            if not e.get("search"):
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

    idx = rules.Index(cfg, catalog.all())

    # 5. 「その他の曲」→「お気に入りの曲」への昇格（data/promoted_songs.yaml に記録）
    misc_pid = resolve(fixed["misc"])
    if not quota_hit and misc_pid:
        titles = [e.get("title") for e in ledger.present(misc_pid)]
        for c in rules.promotion_candidates(cfg, titles, idx, roles):
            entry = {"name": c["name"], "aliases": c["aliases"], "playlist": {"name": c["name"]},
                     "promoted_at": today, "promoted_versions": c["versions"]}
            promoted.append(entry)
            cfg.extra_songs.append(entry)
            rep.add("お気に入りの曲に昇格", f"{c['name']}（{c['versions']} 曲）")

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
    unsorted_add = set()
    for vid in dict.fromkeys(new_ids):
        if any(ledger.status(p, vid) == "present" for p in managed):
            continue  # すでに仕分け済み
        if rules.needs_unsorted(vid, roles, idx, auto_n.get(vid, 0), cand_n.get(vid, 0)):
            unsorted_add.add(vid)
    plan_targets = [(fixed["unsorted"], unsorted_add)] + [(tg.pl, tg.auto) for tg in targets]

    def pub(vid):
        return ((catalog.get(vid) or {}).get("published_at")) or "9999"

    plan: list[tuple[rules.PL, str]] = []
    for pl, vids in plan_targets:
        pid = pids.get(pl.key)
        if pl.id and not pid:
            continue
        for vid in sorted(vids, key=pub):
            if (ledger.status(pid, vid) if pid else None) in (None, "pending"):
                plan.append((pl, vid))

    # 7. 実行（追加のみ）
    if not quota_hit:
        try:
            for pl, vid in plan:
                pid = pids.get(pl.key)
                if not pid:
                    if not pl.creatable or pl.id:
                        continue
                    if dry_run:
                        pid = f"DRYRUN:{pl.key}"
                    else:
                        pid = yt.create_playlist(pl.name, cfg.section("playlist_privacy"))
                        created[pl.key] = {"id": pid, "name": pl.name, "created_at": today}
                        for s in promoted:
                            if pl.key == f"song:{s['name']}":
                                s["playlist"] = {"id": pid, "name": pl.name}
                    alive[pid] = pl.name
                    pids[pl.key] = pid
                    ledger.doc(pid, pl.name)
                    rep.add("新しく作った再生リスト（config.yaml に id を書いてください）", f"{pl.name}: {pid}")
                if dry_run:
                    rep.add("追加（予定）", f"{pl.name} ← {_label(catalog.get(vid), vid)}")
                    continue
                e = ledger.entry(pid, vid)
                v = catalog.get(vid) or {}
                e.setdefault("title", v.get("title"))
                e.setdefault("channel_id", v.get("channel_id"))
                e.setdefault("channel_title", v.get("channel_title"))
                e.setdefault("first_seen", today)
                e["status"] = "pending"
            if not dry_run:
                for pl, vid in plan:
                    pid = pids.get(pl.key)
                    if not pid:
                        continue
                    e = ledger.entry(pid, vid)
                    try:
                        e["item_id"] = yt.add_item(pid, vid)
                        e["status"], e["added_by_bot"] = "present", today
                        rep.add("追加", f"{pl.name} ← {_label(catalog.get(vid), vid)}")
                    except ApiError as err:
                        e["status"], e["error"] = "failed", err.reason or str(err.status)
                        rep.add("追加に失敗", f"{pl.name} ← {vid}: {e['error']}")
        except QuotaExceeded:
            quota_hit = True
            left = sum(1 for pl, v in plan if ledger.status(pids.get(pl.key) or "", v) == "pending")
            rep.add("警告", f"クォータ上限のため {left} 件を次回に持ち越します（台帳では pending）")

    # 8. すでに再生リスト内にある重複（自動では消さず、レポートのみ）
    if cfg.section("dedupe")["enabled"]:
        ents = rules.entity_map(cfg)
        for tg in targets:
            pid = pids.get(tg.pl.key)
            if not pid:
                continue
            present = [e["video_id"] for e in ledger.present(pid)]
            for g in rules.duplicate_groups(present, idx, lambda c: ents.get(c) or c):
                rep.add("重複の可能性（手動で確認）", f"{tg.pl.name}: " + " / ".join(_label(catalog.get(v), v) for v in g))

    # 9. 保存とレポート（config.yaml は書き換えない）
    state["last_run"] = now.isoformat(timespec="seconds")
    header = (f"# 同期レポート {now:%Y-%m-%d %H:%M} JST{'（ドライラン）' if dry_run else ''}\n\n"
              f"- APIクォータ使用量（推定）: {yt.used} / {cfg.budget}\n"
              f"- 監視チャンネル数: {len(roles)} / 曲として扱う動画数: {len(idx.r)}\n"
              f"- 新着: {len(set(new_ids))} 件 / 未仕分けへ: {len(unsorted_add)} 件")
    text = rep.render(header)
    if not dry_run:
        catalog.save()
        ledger.save()
        dump_yaml(promoted_path, promoted)
        dump_yaml(created_path, created)
        dump_yaml(state_path, state)
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(description="YouTube再生リスト自動仕分け")
    ap.add_argument("--dry-run", action="store_true", help="書き込みせず予定だけ表示")
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args(argv)
    print(run(config_path=a.config, dry_run=a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
