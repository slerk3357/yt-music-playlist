"""config.yaml のフォーマッタ。

    python -m ytpl.check --fix          # 整形してからチェック

整形の内容（何度実行しても同じ結果になる）：
  1. ダブルクオートのない文字列値に、ダブルクオートを付ける
  2. インデント幅をファイル全体でそろえる（幅はファイルから推定）
  3. top_favorites / favorites / occasional の項目を name の辞書順に並べ替える

並べ替えは行単位で行う。各項目は「直前のコメント・空行＋項目本体」をひとかたまりとして
動かすので、項目に付けたコメントは項目と一緒に移動する。セクション末尾の行頭コメントは
セクションの末尾に残る。
"""
from __future__ import annotations

import io
import unicodedata

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from .store import Config

SORTED_SECTIONS = ("top_favorites", "favorites", "occasional")


def sort_key(name) -> tuple:
    s = name if isinstance(name, str) else ""
    return (not isinstance(name, str), unicodedata.normalize("NFKC", s).casefold(), s)


def _quote_strings(node) -> None:
    if isinstance(node, CommentedMap):
        for k in list(node.keys()):
            v = node[k]
            if isinstance(v, str) and not isinstance(v, DoubleQuotedScalarString):
                node[k] = DoubleQuotedScalarString(v)
            else:
                _quote_strings(v)
    elif isinstance(node, CommentedSeq):
        for i, v in enumerate(node):
            if isinstance(v, str) and not isinstance(v, DoubleQuotedScalarString):
                node[i] = DoubleQuotedScalarString(v)
            else:
                _quote_strings(v)


def _is_comment_or_blank(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#")


def _sort_section(lines: list[str], seq: CommentedSeq, end: int) -> list[str] | None:
    """lines[...:end] にある block sequence の項目を並べ替えた行の列を返す（並べ替え不要なら None）。"""
    if len(seq) < 2 or seq.fa.flow_style():
        return None
    starts = [seq.lc.item(i)[0] for i in range(len(seq))]
    first = lines[starts[0]]
    col = len(first) - len(first.lstrip())   # 「- 」の位置。これ以上深いコメントは項目のもの
    # 各項目の先頭を、直前のコメント・空行（前の項目より後ろ）まで広げる
    heads = []
    for i, s in enumerate(starts):
        lo = starts[i - 1] + 1 if i else 0
        h = s
        while h - 1 >= lo and _is_comment_or_blank(lines[h - 1]) and \
                (not lines[h - 1].strip() or len(lines[h - 1]) - len(lines[h - 1].lstrip()) >= col):
            h -= 1
        heads.append(h)
    # セクション末尾の、項目より浅いコメント・空行は末尾に残す
    tail = end
    while tail - 1 > starts[-1] and _is_comment_or_blank(lines[tail - 1]) and \
            (not lines[tail - 1].strip() or len(lines[tail - 1]) - len(lines[tail - 1].lstrip()) < col):
        tail -= 1
    chunks = []
    for i in range(len(seq)):
        stop = heads[i + 1] if i + 1 < len(seq) else tail
        chunks.append(lines[heads[i]:stop])
    order = sorted(range(len(seq)), key=lambda i: (sort_key(seq[i].get("name") if isinstance(seq[i], dict) else None), i))
    if order == list(range(len(seq))):
        return None
    out = lines[:heads[0]]
    for i in order:
        chunk = list(chunks[i])
        if chunk and not chunk[-1].endswith("\n"):
            chunk[-1] += "\n"
        out += chunk
    out += lines[tail:]
    return out


def format_text(text: str) -> str:
    """config.yaml の文字列を整形して返す。"""
    if not text.strip():
        return text
    # 1, 2. クオートとインデント（ruamel の round-trip。コメントは保たれる）
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.yaml"
        p.write_text(text, encoding="utf-8")
        cfg = Config(p)
        _quote_strings(cfg.doc)
        cfg.dirty = True
        cfg.save()
        text = p.read_text(encoding="utf-8")

    # 3. 項目の並べ替え（行単位）。1セクションずつ、毎回読み直して行番号を取り直す
    for section in SORTED_SECTIONS:
        from ruamel.yaml import YAML

        doc = YAML().load(text)
        if not isinstance(doc, CommentedMap) or not isinstance(doc.get(section), CommentedSeq):
            continue
        keys = list(doc.keys())
        idx = keys.index(section)
        lines = text.splitlines(keepends=True)
        end = doc.lc.key(keys[idx + 1])[0] if idx + 1 < len(keys) else len(lines)
        new = _sort_section(lines, doc[section], end)
        if new is not None:
            text = "".join(new)
    return text


def format_file(path) -> bool:
    """整形して書き込む。変更があれば True。"""
    from pathlib import Path

    p = Path(path)
    old = p.read_text(encoding="utf-8")
    new = format_text(old)
    if new != old:
        p.write_text(new, encoding="utf-8")
        return True
    return False


def is_formatted(path) -> bool:
    from pathlib import Path

    old = Path(path).read_text(encoding="utf-8")
    return format_text(old) == old
