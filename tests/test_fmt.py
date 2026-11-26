from ytpl import check, fmt
from ytpl.store import Config

SRC = '''# 先頭のコメント
playlist_privacy: private
favorites:
  # Cさんのコメント
  - name: "C"   # 行末C
    channels: [{id: "UCcccccccccccccccccccccc", name: "C"}]
  # Aさんのコメント
  - name: A
    channels:
      - {id: "UCaaaaaaaaaaaaaaaaaaaaaa", name: "A"}   # 行末A
    # Aの中のコメント
    aliases: [A, "エー"]

  # bさんのコメント（空行のあと）
  - name: "b"
    channels: [{id: "UCbbbbbbbbbbbbbbbbbbbbbb", name: "b"}]
# songs の前のコメント
songs:
  - name: "曲Z"
  - name: "曲A"
occasional:
- name: "ん"
  channels: [{id: "UCnnnnnnnnnnnnnnnnnnnnnn", name: "ん"}]
- name: "あ"
  channels: [{id: "UCxxxxxxxxxxxxxxxxxxxxxx", name: "あ"}]
# 末尾のコメント
'''

EXPECTED = '''# 先頭のコメント
playlist_privacy: "private"
favorites:
  # Aさんのコメント
  - name: "A"
    channels:
      - {id: "UCaaaaaaaaaaaaaaaaaaaaaa", name: "A"}   # 行末A
    # Aの中のコメント
    aliases: ["A", "エー"]

  # bさんのコメント（空行のあと）
  - name: "b"
    channels: [{id: "UCbbbbbbbbbbbbbbbbbbbbbb", name: "b"}]
  # Cさんのコメント
  - name: "C"   # 行末C
    channels: [{id: "UCcccccccccccccccccccccc", name: "C"}]
# songs の前のコメント
songs:
  - name: "曲Z"
  - name: "曲A"
occasional:
  - name: "あ"
    channels: [{id: "UCxxxxxxxxxxxxxxxxxxxxxx", name: "あ"}]
  - name: "ん"
    channels: [{id: "UCnnnnnnnnnnnnnnnnnnnnnn", name: "ん"}]
# 末尾のコメント
'''


def test_format_sorts_entries_with_their_comments():
    out = fmt.format_text(SRC)
    assert out == EXPECTED
    assert fmt.format_text(out) == out          # 何度実行しても同じ


def test_check_fix(tmp_path, capsys):
    p = tmp_path / "config.yaml"
    p.write_text(SRC, encoding="utf-8")
    assert check.check(p) == 1                  # クオートのない文字列はエラー
    assert check.check(p, fix=True) == 0        # 整形で直る
    assert p.read_text(encoding="utf-8") == EXPECTED
    assert Config(p).quote_errors == []
    capsys.readouterr()
    p.write_text(EXPECTED.replace('  - name: "あ"', '  - name: "ぁ"'), encoding="utf-8")
    assert check.check(p) == 0
    assert "整形されていません" not in capsys.readouterr().out
    p.write_text(EXPECTED.replace('name: "A"\n', 'name: "Z"\n', 1), encoding="utf-8")
    assert check.check(p) == 0                  # 並び順の乱れは警告のみ
    assert "整形されていません" in capsys.readouterr().out
