"""config.yaml の整合性チェック（APIは使わない）。

    python -m ytpl.check [config.yaml] [--label 表示名]
    python -m ytpl.check --fix [config.yaml]    # 整形（ytpl/fmt.py）してからチェック

エラーがあれば終了コード 1。警告だけなら 0。コミット前フック（.githooks/pre-commit）と
GitHub Actions（check-config）から使う。整形されていないファイルは警告になる。
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

from . import rules
from .store import Config


def warnings(cfg: Config) -> list[str]:
    out = []
    # 同じチャンネルが複数の項目にある（同一人物のまとまりが崩れる）
    owner = defaultdict(list)
    for key, _, i, e in rules.tier_entries(cfg):
        for cid in rules.ids(e.get("channels")):
            owner[cid].append(f"{key}「{e.get('name')}」")
    for cid, where in owner.items():
        if len(where) > 1:
            out.append(f"チャンネル {cid} が複数の項目にあります: {', '.join(where)}")

    # 同じ再生リストIDが複数の場所で使われている
    used = defaultdict(list)

    def note(ref, where):
        if isinstance(ref, dict) and ref.get("id"):
            used[ref["id"]].append(where)

    for kind in rules.DEFAULT_NAMES:
        note((cfg.raw.get("playlists") or {}).get(kind), f"playlists.{kind}")
    for key, _, i, e in rules.tier_entries(cfg):
        w = f"{key}「{e.get('name')}」"
        note(e.get("playlist"), f"{w}.playlist")
        note((e.get("other") or {}).get("playlist"), f"{w}.other.playlist")
        for j, sp in enumerate(e.get("specials") or []):
            note(sp.get("playlist"), f"{w}.specials[{j}]")
    for i, s in enumerate(cfg.songs()):
        note(s.get("playlist"), f"songs「{s.get('name')}」")
    for pid, where in used.items():
        if len(where) > 1:
            out.append(f"再生リスト {pid} が複数の場所で使われています: {', '.join(where)}")

    # 何も作らない特別再生リスト条件など
    for key, _, i, e in rules.tier_entries(cfg):
        for j, sp in enumerate(e.get("specials") or []):
            if sp.get("require_related") is False and not any(
                    sp.get(k) for k in ("published_by", "mentions", "mention_aliases", "keywords")):
                out.append(f"{key}「{e.get('name')}」.specials[{j}]: require_related: false で条件がないため、"
                           "監視中の全動画が対象になります")
    return out


def check(path: str | Path, label: str | None = None, fix: bool = False) -> int:
    from . import fmt

    path = Path(path)
    name = label or str(path)
    if not path.exists():
        print(f"{path} がありません（チェックをスキップ）")
        return 0
    try:
        cfg = Config(path)
    except Exception as e:  # YAML の文法エラー
        print(f"✗ {name} を読み込めません:\n{e}")
        return 1
    if fix:
        if fmt.format_file(path):
            print(f"✎ {name} を整形しました（クオート・インデント・チャンネル項目の並び順）")
        else:
            print(f"✎ {name} は整形済みです")
        cfg = Config(path)
    errs = rules.validate(cfg)
    warns = warnings(cfg) if not errs else []
    if not fix and not fmt.is_formatted(path):
        warns.append("整形されていません（python -m ytpl.check --fix で、クオート・インデント・"
                     "チャンネル項目の並び順を整えられます）")
    for w in warns:
        print(f"⚠ {w}")
    for e in errs:
        print(f"✗ {e}")
    n = {k: len(cfg.list(k)) for k, _ in rules.TIERS}
    summary = (f"特にお気に入り {n['top_favorites']} / お気に入り {n['favorites']} / "
               f"たまに聴く {n['occasional']} / お気に入りの曲 {len(cfg.songs())}")
    if errs:
        print(f"\n{name}: エラー {len(errs)} 件（{summary}）")
        return 1
    print(f"✓ {name}: OK（{summary}、警告 {len(warns)} 件）")
    return 0


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="config.yaml の整合性チェック")
    ap.add_argument("path", nargs="?", default="config.yaml")
    ap.add_argument("--label", help="表示に使う名前")
    ap.add_argument("--fix", action="store_true", help="整形してからチェックする（ファイルを書き換える）")
    a = ap.parse_args(argv)
    sys.exit(check(a.path, a.label, a.fix))


if __name__ == "__main__":
    main()
