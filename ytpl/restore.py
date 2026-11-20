"""台帳（data/playlists/*.yaml）から再生リストを復元する。

    # 消えた曲（status: missing）だけを元の再生リストに戻す
    python -m ytpl.restore --playlist "アリスチャンネル"

    # 台帳にある曲を全部、新しい再生リストに作り直す（再生リストごと消えた場合）
    python -m ytpl.restore --playlist "アリスチャンネル" --to-new "アリスチャンネル（復元）"

    --dry-run で確認だけできます。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .api import ApiError, QuotaExceeded, YouTube
from .store import Config, Ledger


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--playlist", required=True, help="台帳の再生リスト名")
    ap.add_argument("--to-new", help="この名前で新しい再生リストを作って復元")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--data", default="data")
    a = ap.parse_args(argv)

    cfg = Config(a.config)
    ledger = Ledger(Path(a.data) / "playlists")
    doc = next((d for d in ledger.docs.values() if d["title"] == a.playlist), None)
    if not doc:
        raise SystemExit(f"台帳に「{a.playlist}」がありません")

    wanted = ("present", "missing") if a.to_new else ("missing",)
    items = [e for e in doc["items"].values() if e.get("status") in wanted]
    print(f"{len(items)} 曲を復元します")
    for e in items:
        print(f"  - {e.get('title')} ({e['video_id']})")
    if a.dry_run or not items:
        return

    yt = YouTube.from_env(cfg.budget)
    today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    if a.to_new:
        pid = yt.create_playlist(a.to_new, cfg.section("playlist_privacy"))
        target = ledger.doc(pid, a.to_new)
    else:
        pid, target = doc["playlist_id"], doc
    done = 0
    try:
        for e in items:
            t = target["items"].setdefault(e["video_id"], {k: v for k, v in e.items()
                                                           if k in ("video_id", "title", "channel_id", "channel_title")})
            try:
                t["item_id"] = yt.add_item(pid, e["video_id"])
                t["status"], t["restored_at"] = "present", today
                t.pop("missing_since", None)
                done += 1
            except ApiError as err:
                t["status"], t["error"] = "unavailable", err.reason or str(err.status)
                print(f"  ! {e.get('title')}: {t['error']}")
    except QuotaExceeded:
        print("クォータ上限に達したため中断しました。明日もう一度実行してください。")
    ledger.save()
    print(f"{done} 曲を復元しました")


if __name__ == "__main__":
    main()
