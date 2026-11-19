# yt-music-playlist

YouTubeの音楽再生リストを独自ルールで毎日自動仕分けし、各再生リストの中身をYAMLでgitに残すツールです。GitHub Actionsで毎朝 04:17 JST に動きます。

## 毎日やること

| 処理 | 内容 |
|---|---|
| バックアップ | 自分の全再生リストの中身を `data/playlists/<再生リスト名>.yaml` に記録 |
| 新着チェック | 設定したチャンネルの新しい動画を取得し、曲かどうかを判定 |
| 自動仕分け | 「○○チャンネル」「その他○○」「曲名」「○○△△」に該当する曲を追加 |
| 未仕分け | お気に入りチャンネルの新着と、たまに聴くチャンネルの分類できない新着を「未仕分け」へ |
| 未仕分けの整理 | 別の再生リストに仕分けた曲を「未仕分け」から外す |
| 昇格 | 「その他の曲」のうち本家/カバーが5曲以上ある曲を「お気に入りの曲」に追加し、曲名の再生リストを作成（`config.yaml` も自動更新） |
| レポート | `reports/latest.md` と Actions の実行サマリーに結果を出力 |

自分で再生リストから消した曲は、自動で戻しません（台帳で `missing` として記録だけ残します）。

## セットアップ

### 1. リポジトリ
このリポジトリはツール本体の公開用です。実際の運用は、**このリポジトリをもとに作った非公開（private）リポジトリ**で行います（設定・再生リストのバックアップには視聴傾向が含まれるため）。

1. GitHubの [Import repository](https://github.com/new/import) で、このリポジトリのURLを指定し、Privacy を **Private** にして作成
2. 以降の手順はすべて、その非公開リポジトリで行います

このリポジトリ自体には `config.yaml` も Secrets もないため、定期実行は何もせずに終了します。

本体を更新したときは、非公開リポジトリ側で取り込みます。
```bash
git remote add upstream https://github.com/slerk3357/yt-music-playlist.git
git pull upstream main
```

### 2. Google Cloud（初回のみ）
1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. 「APIとサービス」→「ライブラリ」で **YouTube Data API v3** を有効化
3. 「OAuth同意画面」を作成（User Type: 外部）。テストユーザーに自分のGoogleアカウントを追加
4. **公開ステータスを「本番環境」に変更**（「テスト」のままだとトークンが7日で失効します。審査は不要で、承認時に「未確認のアプリ」と表示されるだけです）
5. 「認証情報」→「OAuthクライアントID」→ 種類「デスクトップアプリ」で作成し、JSONをダウンロード

### 3. トークン取得（手元のPCで）
```bash
pip install -r requirements.txt google-auth-oauthlib
python scripts/get_token.py client_secret_xxx.json
```
ブラウザで、**再生リストを持っているYouTubeアカウント（ブランドアカウントの場合はそのチャンネル）** を選んで許可します。表示された `YT_CLIENT_ID` `YT_CLIENT_SECRET` `YT_REFRESH_TOKEN` を、リポジトリの Settings → Secrets and variables → Actions に登録します。

### 4. 設定ファイル
今の再生リストから下書きを作れます。
```bash
export YT_CLIENT_ID=... YT_CLIENT_SECRET=... YT_REFRESH_TOKEN=...
python -m ytpl.draft_config > config.draft.yaml
```
`config.draft.yaml` と `config.example.yaml` を見比べながら、`config.yaml` を作ってコミットします。特に以下を確認してください。
- `aliases`：他チャンネルの動画タイトルに出てくる表記（英語名・略称など）
- `songs` の `aliases` と `official_channel_ids`
- `combos` の `partner_aliases`
- 「# 要確認」の行

### 5. 試運転 → 本番
1. Actions →「playlist-sync」→「Run workflow」（dry_run にチェック）を実行し、サマリーで追加予定を確認
2. 問題なければ dry_run を外して実行。初回は既存曲の取り込みが多いため、1日約170件ずつ数日かけて追加されます
3. 以降は毎日自動で動きます

## ふだんの使い方
- **「未仕分け」を見て手動で振り分けるだけ**です。振り分けた曲は翌朝「未仕分け」から自動で外れます。
  - お気に入りチャンネルの曲は、その「○○チャンネル」に入れたときに外れます。いらない曲は「未仕分け」から直接消してください。
- チャンネルや曲を増やすときは `config.yaml` を編集してコミットします。新しく追加したチャンネルは、過去の動画もまとめて取り込みます（ただし「未仕分け」には入れません）。
- 昇格を取り消すときは、`songs` から消して `promotion.ignore` に曲名を追加します。
- 誤判定は各ルールの `exclude_video_ids` / `include_video_ids`、全体なら `ignore_video_ids` で直せます。

## バックアップと復元
`data/playlists/*.yaml` の各曲には `status` が付きます。

| status | 意味 |
|---|---|
| present | 再生リストに入っている |
| unavailable | 入っているが動画が削除・非公開（タイトル等は消える前の記録を保持） |
| missing | 以前は入っていたが、今はない |
| pending | 入るはずだが、クォータの都合で未追加（次回追加） |
| removed | 仕分け済みのため「未仕分け」から外した |
| failed | 追加に失敗（エラー理由つき） |

再生リストが壊れたら、手元で：
```bash
python -m ytpl.restore --playlist "アリスチャンネル" --dry-run      # 消えた曲を確認
python -m ytpl.restore --playlist "アリスチャンネル"                # 消えた曲を戻す
python -m ytpl.restore --playlist "アリスチャンネル" --to-new "アリスチャンネル（復元）"  # 丸ごと作り直す
```
過去のある時点の状態は `git log -p data/playlists/アリスチャンネル.yaml` でたどれます。

## クォータについて
1日1万ユニットです。読み取りは50件ごとに1、再生リストへの追加・削除は1件50、検索は1回100ユニットかかります。毎日の差分更新なら数百ユニットで済みます。足りない分は `pending` として翌日に回ります。

## 制限
- ショートの判定はAPIにないため、61秒未満または `#shorts` を含むものを除外しています（3分までのショートは取りこぼす場合あり）。
- 曲名の一致は「タイトルに別名が含まれるか」で判定します。短い曲名や一般的な単語は誤判定しやすいので、`exclude_keywords` で調整してください。
- 「その他○○」は監視しているチャンネルの動画から探します。それ以外のチャンネルも探すには `search: true` を使います。

## 開発
```bash
pip install -r requirements.txt pytest
pytest -q                     # 偽APIを使ったテスト
python -m ytpl.sync --dry-run # 手元で試す（環境変数が必要）
```
