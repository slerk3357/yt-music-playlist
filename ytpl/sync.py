"""毎日の同期処理本体。  python -m ytpl.sync [--dry-run]"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import rules
from .api import ApiError, QuotaExceeded, YouTube, uploads_playlist_id
from .store import EXTERNAL, Catalog, Config, Ledger, dump_yaml, load_yaml

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
    cfg = Config(config_path)
    data = Path(data_dir)
    yt = yt or YouTube.from_env(cfg.budget)
    catalog = Catalog(data / "catalog")
    ledger = Ledger(data / "playlists")
    state_path = data / "state.yaml"
    state = dict(load_yaml(state_path, {}) or {})
    rep = Report()
    roles = rules.channel_roles(cfg)
    t_unsorted, t_misc = cfg.title("unsorted"), cfg.title("misc")
    quota_hit = False
    new_ids: list[str] = []
    title_to_id: dict[str, str] = {}

    try:
        # 1. 自分の全再生リストをスナップショット（＝バックアップ）
        playlists = yt.my_playlists()
        for pl in playlists:
            if pl["title"] in title_to_id:
                rep.add("警告", f"同名の再生リストが複数あります: {pl['title']}（先に見つかった方を使用）")
            else:
                title_to_id[pl["title"]] = pl["id"]
            ledger.apply_snapshot(pl["id"], pl["title"], yt.playlist_items(pl["id"]), today)
        alive = {p["id"] for p in playlists}
        for title, info in rules.rule_titles(cfg).items():
            pin = info.get("playlist_id")
            if pin and pin in alive:
                title_to_id[title] = pin
            elif pin:
                rep.add("警告", f"「{title}」の playlist_id {pin} が自分の再生リストに見つかりません（名前で照合します）")
        for t in ledger.mark_deleted_playlists({p["id"] for p in playlists}, today):
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

        # 2b. @ハンドルの取得（その他○○・コラボ相手の判定用、7日ごとに更新）
        hcache = dict(state.get("handles") or {})
        stale = sorted(c for c in rules.handle_channel_ids(cfg)
                       if c not in hcache or hcache[c].get("checked", "") < (now - timedelta(days=7)).date().isoformat())
        if stale:
            found = yt.channel_handles(stale)
            for c in stale:
                hcache[c] = {"handle": found.get(c), "checked": today}
        state["handles"] = hcache

        # 3. 特にお気に入りの「その他○○」を検索（任意・100ユニット/回）
        searches = dict(state.get("search_after") or {})
        for t in cfg.list("top_favorites"):
            if not t.get("search"):
                continue
            after = searches.get(t["name"]) or (now - timedelta(days=3)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            ids = [i for i in yt.search_video_ids(t.get("search_query") or t["name"], after) if i not in catalog]
            refs = rules.channel_refs(t.get("channel_ids"), handles_of(state))
            aliases = t.get("aliases") or []
            for v in yt.videos(ids):
                text = f"{v['title']}\n{v['description']}"
                if rules.matches_channel_ref(text, refs) or (aliases and rules.matches_alias(text, aliases)):
                    catalog.add(EXTERNAL, v)
                    new_ids.append(v["id"])
            searches[t["name"]] = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["search_after"] = searches

        # 4. 「未仕分け」・「その他の曲」にある監視外チャンネルの動画も情報を取得
        extra = []
        for t in (t_unsorted, t_misc):
            pid = title_to_id.get(t)
            if pid:
                extra += [e["video_id"] for e in ledger.present(pid) if e["video_id"] not in catalog]
        for v in yt.videos(sorted(set(extra))):
            catalog.add(EXTERNAL, v)
    except QuotaExceeded as e:
        quota_hit = True
        rep.add("警告", f"読み取り中にクォータ上限に達しました: {e}")

    videos = list(catalog.all())

    # 5. 「その他の曲」→「お気に入りの曲」への昇格
    if not quota_hit and title_to_id.get(t_misc):
        for c in rules.promotion_candidates(cfg, ledger.present(title_to_id[t_misc]), videos, roles):
            cfg.append_song({"title": c["title"], "aliases": c["aliases"],
                             "promoted_at": today, "promoted_versions": c["versions"]})
            rep.add("お気に入りの曲に昇格", f"{c['title']}（{c['versions']} 曲）")

    # 6. 振り分け先を計算
    targets = rules.build_targets(cfg, videos, roles, handles_of(state))
    unsorted_add = []
    for vid in dict.fromkeys(new_ids):
        v = catalog.get(vid)
        hits = {t for t, s in targets.items() if vid in s}
        if v and rules.needs_unsorted(cfg, v, roles, hits):
            unsorted_add.append(vid)
    plan_targets = {t_unsorted: set(unsorted_add), **targets}

    def pub(vid):
        v = catalog.get(vid)
        return (v or {}).get("published_at") or "9999"

    plan: list[tuple[str, str]] = []
    for title, vids in plan_targets.items():
        for vid in sorted(vids, key=pub):
            st = ledger.status(title_to_id[title], vid) if title in title_to_id else None
            if st in (None, "pending"):
                plan.append((title, vid))

    # 7. 実行
    if not quota_hit:
        try:
            for title, vid in plan:
                if title not in title_to_id:
                    if dry_run:
                        title_to_id[title] = f"DRYRUN:{title}"
                    else:
                        title_to_id[title] = yt.create_playlist(title, cfg.section("playlist_privacy"))
                    ledger.doc(title_to_id[title], title)
                    rep.add("新しく作った再生リスト", title)
                pid = title_to_id[title]
                if dry_run:
                    rep.add("追加（予定）", f"{title} ← {_label(catalog.get(vid), vid)}")
                    continue
                e = ledger.entry(pid, vid)
                v = catalog.get(vid) or {}
                e.setdefault("title", v.get("title"))
                e.setdefault("channel_id", v.get("channel_id"))
                e.setdefault("channel_title", v.get("channel_title"))
                e.setdefault("first_seen", today)
                e["status"] = "pending"
            if not dry_run:
                for title, vid in plan:
                    pid = title_to_id[title]
                    e = ledger.entry(pid, vid)
                    try:
                        e["item_id"] = yt.add_item(pid, vid)
                        e["status"], e["added_by_bot"] = "present", today
                        rep.add("追加", f"{title} ← {_label(catalog.get(vid), vid)}")
                    except ApiError as err:
                        e["status"], e["error"] = "failed", err.reason or str(err.status)
                        rep.add("追加に失敗", f"{title} ← {vid}: {e['error']}")
        except QuotaExceeded:
            quota_hit = True
            left = sum(1 for t, v in plan if ledger.status(title_to_id.get(t, ""), v) == "pending")
            rep.add("警告", f"クォータ上限のため {left} 件を次回に持ち越します（台帳では pending）")

        # 8. 仕分け済みになった曲を「未仕分け」から外す
        pid_u = title_to_id.get(t_unsorted)
        if pid_u and cfg.section("unsorted")["auto_remove_when_sorted"] and not quota_hit:
            managed = rules.managed_titles(cfg, targets) - {t_unsorted}
            managed_pids = [title_to_id[t] for t in managed if t in title_to_id]
            try:
                for e in list(ledger.present(pid_u)):
                    vid = e["video_id"]
                    if not any(ledger.status(p, vid) == "present" for p in managed_pids):
                        continue
                    ch = (catalog.get(vid) or {}).get("channel_id") or e.get("channel_id")
                    own = rules.favorite_own_title(cfg, ch)
                    if own and ledger.status(title_to_id.get(own, ""), vid) != "present":
                        continue  # お気に入りチャンネルの曲は○○チャンネルへの仕分けを待つ
                    if dry_run:
                        rep.add("未仕分けから除外（予定）", e.get("title") or vid)
                        continue
                    yt.delete_item(e["item_id"])
                    e["status"], e["removed_at"] = "removed", today
                    e.pop("item_id", None)
                    rep.add("未仕分けから除外", e.get("title") or vid)
            except QuotaExceeded:
                rep.add("警告", "クォータ上限のため未仕分けの整理を中断しました")

    # 8b. すでに再生リスト内にある重複（自動では消さず、レポートのみ）
    if cfg.section("dedupe")["enabled"]:
        ents = rules.entity_map(cfg)
        by_id = {v["id"]: v for v in videos}
        for title in targets:
            pid = title_to_id.get(title)
            if not pid:
                continue
            present = [e["video_id"] for e in ledger.present(pid)]
            for g in rules.duplicate_groups(present, by_id, lambda c: ents.get(c) or c):
                rep.add("重複の可能性（手動で確認）", f"{title}: " + " / ".join(_label(catalog.get(v), v) for v in g))

    # 9. 保存とレポート
    state["last_run"] = now.isoformat(timespec="seconds")
    header = (f"# 同期レポート {now:%Y-%m-%d %H:%M} JST{'（ドライラン）' if dry_run else ''}\n\n"
              f"- APIクォータ使用量（推定）: {yt.used} / {cfg.budget}\n"
              f"- 監視チャンネル数: {len(roles)} / カタログ動画数: {len(videos)}\n"
              f"- 新着: {len(set(new_ids))} 件")
    text = rep.render(header)
    if not dry_run:
        catalog.save()
        ledger.save()
        cfg.save()
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
