# yt-music-playlist

YouTubeの音楽再生リストを独自ルールで毎日自動仕分けし、各再生リストの中身をYAMLでgitに残すツールです。GitHub Actionsで毎朝 04:17 JST に動きます。`config.yaml` を main に push したときは、新しく書いた再生リストの作成だけをすぐに行います。

## 毎日やること（この順に実行）

| 処理 | 内容 |
|---|---|
| 再生リストの作成 | `config.yaml` に id なしで書いた再生リストを、最初に作成（すぐ手で仕分けを始められるように） |
| 未知の再生リストの記録 | `config.yaml` のどこにも載っていない再生リストを、`ignore_playlists` に「# 要確認」付きで追加（管理外として扱う） |
| バックアップ | 管理対象の再生リスト（`config.yaml` に載っていて `ignore_playlists` でないもの）の中身を `data/playlists/<再生リスト名>.yaml` に記録 |
| 新着チェック | 設定したチャンネルの新しい動画を取得し、曲かどうかを判定 |
| 自動仕分け | 「自動」の再生リストに該当する曲を追加 |
| 重複除去 | 同じ人の同じ曲・同じバージョンはMVを優先して1本だけ追加（Remix・別Ver.は別扱い） |
| 仮保存 | 判断が必要な新着曲を「未仕分け」に追加 |
| 昇格 | 「その他の曲」のうち本家/カバーが5曲以上ある曲を「お気に入りの曲」にし、曲名の再生リストを作成 |
| レポート | `reports/latest.md` と Actions の実行サマリーに結果を出力 |
| 追加の提案 | すでに別の再生リストに入っている曲が、ほかの再生リストの確認候補にも当たっていれば、提案先ごとに理由つきで一覧にする。ミックスAPIの「追加の提案」ページで曲を再生して確かめ、「追加」を選んだ曲は次の実行で再生リストに入る（自分で選ばない限り追加しない） |
| ミックス用の索引 | オリジナルミックスリスト用に、管理対象の再生リストの曲を `data/mix/index.json` に書き出す |

**クォータ（1日10,000）が足りないときの優先順位**
- 読み取り：管理対象の再生リストのバックアップと、取り込み済みチャンネルの新着取得は毎日必ず行います。新しく追加したチャンネルの全件取得と検索は、書き込み用の予約枠（`quota.write_reserve`）を残せる範囲で行い、残りは次回に回します。
- 追加：①新曲を「未仕分け」へ → ②手動指定（`include_video_ids`） → ③新曲を○○チャンネルへ → ④新曲を特別再生リストへ → ⑤新曲を「お気に入りの曲」へ → ⑥取り込み待ち（初回・設定変更で該当した過去の曲。③〜⑤の順） → ⑦昇格した曲。同じ順位の中は新しい順です。新曲とは、公開を検知してから30日以内の曲です。
- 間に合わなかった分は `pending` として次回に回ります。

**データ保護のための決まり**
- システムは再生リストへの**追加だけ**を行い、動画の削除は一切しません（削除するAPIも実装していません）。「未仕分け」の整理は自分で行います。
- 自分で再生リストから消した曲は、自動で戻しません（台帳には `missing` として記録が残ります）。
- システムが `config.yaml` に書き込むのは「新しく作った再生リストの id」と「昇格した曲」だけです。コメント・クオート・並び順は保たれるので、`config.yaml` を見れば、今の設定・再生リスト・対象チャンネルがすべて分かります。

## 再生リストの種類

チャンネルごと（`top_favorites` / `favorites` / `occasional` の各項目）に、次の再生リストを作れます。

| | ○○チャンネル（`playlist`） | その他○○（`other.playlist`） | 特別再生リスト（`specials`） |
|---|---|---|---|
| 中身 | そのチャンネル（＋TOPIC・サブ）が公開した曲 | 他チャンネルの関係曲（特別再生リストに入った曲は除く） | 条件を合わせた曲（例：作曲者Bが公開した、歌い手Aがボーカルの曲） |
| 作る条件 | `playlist` を書いたとき | `other.playlist` に **id** を書いたとき | `specials` を書いたとき |
| 特にお気に入り | 自動（全曲） | 判断 | 自動 |
| お気に入り | 判断 | 判断 | 自動 |
| たまに聴く | 判断 | 判断 | 自動 |

- **自動**：毎日自動で追加します。
- **判断**：新しい候補を「未仕分け」に仮保存するので、そこから手で振り分けます。
- **名前（`aliases` / `mention_aliases`）だけで見つかった曲**は、自動の再生リストが対象でも必ず「未仕分け」に入れて確認します。チャンネル（本人のチャンネル・@ハンドル・チャンネルID）で確認できた曲だけを自動で追加します。
- **お気に入りの曲**（`songs`）は、公式チャンネルと3分類のチャンネルによるカバーを自動で集めます。
- 「その他○○」は、どの分類でも自動では追加しません。
- Instrumental・Off Vocal・カラオケ音源は、既定ではどの再生リストにも入れません。再生リストごとに `include_off_vocal: true` を書くと、通常版がない曲に限ってオフボーカルを入れます。

全フィールドの仕様と、特別再生リストの条件の組み合わせ方は **[docs/config.md](docs/config.md)** にまとめています。

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

### 2. トークン発行用の Apps Script（初回のみ）
Google Cloud の OAuth 設定の代わりに、Google Apps Script で「YouTube 用のアクセストークンを返すだけのWebアプリ」を作ります。GitHub Actions は実行のたびにここからトークンを受け取ります。

1. [Apps Script](https://script.google.com/) で「新しいプロジェクト」を作成（**再生リストを持っているGoogleアカウント**で）
2. 左の「プロジェクトの設定」→「「appsscript.json」マニフェスト ファイルをエディタで表示する」にチェック
3. エディタで `appsscript.json` と `コード.gs` の中身を、このリポジトリの [`gas/appsscript.json`](gas/appsscript.json) と [`gas/Code.gs`](gas/Code.gs) で置き換えて保存
4. 関数 `setup` を選んで「実行」→ 権限を承認（「このアプリは Google で確認されていません」と出たら「詳細」→「（安全ではないページ）に移動」）
5. 実行ログを確認
   - `接続先チャンネル:` が、再生リストを持っているチャンネルであること
   - `YT_TOKEN_SECRET = ...` の値を控える
6. 「デプロイ」→「新しいデプロイ」→ 種類「ウェブアプリ」、次のユーザーとして実行「自分」、アクセスできるユーザー「全員」でデプロイし、**ウェブアプリのURL**（`https://script.google.com/macros/s/.../exec`）を控える

> **ブランドアカウントの注意：** Apps Script が操作できるのは、そのGoogleアカウント本体のチャンネルだけです。ブランドアカウントのチャンネルで再生リストを管理している場合は、末尾の「別の認証方法」を使ってください。

### 3. GitHub に登録
非公開リポジトリの Settings → Secrets and variables → Actions に、次の2つを登録します。

| 名前 | 値 |
|---|---|
| `YT_TOKEN_URL` | ウェブアプリのURL |
| `YT_TOKEN_SECRET` | 控えた秘密の文字列 |

URLと秘密の文字列が両方漏れると、あなたのYouTubeを操作できてしまいます。漏れた疑いがあるときは、Apps Script で `rotateSecret` を実行し、`YT_TOKEN_SECRET` を更新してください。

### 4. 設定ファイル
今の再生リストから下書きを作れます（手元のPCで実行）。
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export YT_TOKEN_URL=... YT_TOKEN_SECRET=...
python -m ytpl.draft_config > config.draft.yaml
```
文字列はすべてダブルクオートで囲む決まりです（下書きは囲んだ状態で出力されます）。`config.draft.yaml` と `config.example.yaml`、[docs/config.md](docs/config.md) を見比べながら、`config.yaml` を作ってコミットします。特に以下を確認してください。
- 各チャンネルの `channels`：TOPIC・サブチャンネルを候補コメントから追加（`name` も書く）
- `specials`：下書きは「中身のチャンネルが少ない再生リスト」を、主なチャンネルの特別再生リストとして仮に置いています。どの人の項目に置くか（例：「作曲者B×歌い手A」は歌い手Aの項目に置き `published_by` に作曲者Bを書く）と、条件を見直してください
- `songs` の `aliases` と `official_channels`
- 「# 要確認」の行

できあがったら、整合性を確認してからコミットします。
```bash
python -m ytpl.check --fix            # 整形（クオート・インデント・チャンネル項目を name 順に）してから確認
git config core.hooksPath .githooks   # 以後、config.yaml をコミットするたびに自動で確認
```

### 5. 試運転 → 本番
1. Actions →「playlist-sync」→「Run workflow」（dry_run にチェック）を実行し、サマリーで追加予定を確認
2. 問題なければ dry_run を外して実行。初回は既存曲の取り込みが多いため、1日約170件ずつ数日かけて追加されます
3. 以降は毎日自動で動きます

## ふだんの使い方
- **「未仕分け」を見て、手動で振り分けてから「未仕分け」から消す**だけです。システムは「未仕分け」から曲を消しません。一度消した曲が、もう一度入ることはありません。
- チャンネルや曲を増やすときは `config.yaml` を編集してコミットします（コミット前に自動で整合性を確認します）。新しく追加したチャンネルは、過去の動画もまとめて取り込みます（ただし「未仕分け」には入れません）。
- 自動で昇格した曲は、`config.yaml` の `songs` の末尾に追加されます（`promoted_at` 付き）。取り消すときは、その曲を `songs` から消して `promotion.ignore` に曲名を追加します。
- `playlist: {name: "…"}` のように id を書かずに再生リストを指定して main に push すると、数分後に作成され、id が `config.yaml` に書き込まれます（`git pull` で取り込んでください）。
- 管理したくない再生リストは `ignore_playlists` に `{id, name}` で列挙します。新しく作った再生リストは、次の実行で自動的に `ignore_playlists` に「# 要確認」付きで追加されるので、管理したいものは正しい場所に移してください。
- **追加の提案の確認:** ミックスAPIのメニュー →「追加の提案」を開くと、ほかの再生リストにも入れたほうがよさそうな曲が、**提案先ごと**に、理由（名前だけの一致、曲名の部分一致、原曲の手がかりなし、判断が必要な再生リスト など）と今入っている再生リストつきで並びます。
  - **▶（サムネイル）:** その場で動画を再生して確かめられます（埋め込みの再生が許可されていない動画は、タイトルのリンクから YouTube で開きます）。
  - **追加:** 次回の同期（毎朝）で、提案先の再生リストに入ります。反映されると「追加済み」になります。
  - **却下:** その提案先には、もう提案しません。
  - **取り消し:** 反映前の「追加」と「却下」は取り消せます（反映済みの追加は、このシステムでは再生リストから消せないので、YouTube 上で消してください）。
  - **どちらも選ばない:** 次回も提案されます。「まとめて YouTube で開く」では、まだ決めていない曲を一時リストで続けて聴けます（クォータは使いません）。
  - 決めた内容は Worker（Durable Object）に保存され、同期のたびに `data/decisions.yaml` にも記録されます（git に残るので、Worker 側のデータが消えても却下は守られます）。
- YouTube上で再生リストの名前を変えたり、新しく作ったりしたときは、手元で `python -m ytpl.pull_config` を実行します。`config.yaml` の再生リスト名が今のタイトルに更新され、未登録の再生リストは `ignore_playlists` に「# 要確認」付きで追加されます（詳しくは [docs/config.md](docs/config.md)）。
- 誤判定は各ルールの `exclude_video_ids` / `include_video_ids`、全体なら `ignore_video_ids` で直せます。

## オリジナルミックスリスト

このシステムで管理している再生リストの曲だけから、その場でミックスを作り、YouTube Music で再生します。YouTube の「一時的な再生リスト」（`watch_videos`）を使うので、**YouTube Data API のクォータは消費しません**。

| ミックス | URL | 選曲の対象 |
|---|---|---|
| メニュー | `/` | 下の各ミックスへのリンク（スマホのホーム画面に置く用） |
| 完全ランダム | `/random` | 管理しているすべての再生リストの曲 |
| ランダム再生リスト | `/playlists?k=3` | ランダムに選んだ k 個の再生リストの曲 |
| 特にお気に入りのチャンネル | `/top/<name>` | その人の ○○チャンネル＋その他○○＋特別再生リストの曲 |
| 追加の提案 | `/suggestions` | 提案先ごとの一覧。曲ごとに再生して「追加」「却下」を選ぶ。「まとめて YouTube で開く」で、まだ決めていない曲を一時リストとして開く（50曲ずつ） |

- **対象:** `config.yaml` で管理している再生リストの曲です。「未仕分け」と `ignore_playlists`、削除・非公開になった曲は含めません。
- **共通のオプション:** `n=50`（曲数。1〜50、既定は50）、`open=list`（曲一覧の画面を開く）、`app=intent`（Android でブラウザが開いてしまうとき）、`format=json`（リダイレクトせず選曲結果を表示）。
- **選び直し:** リンクを開くたびに選び直します。同じ曲は1回だけです。

**しくみ**
1. 毎日の同期が、ミックス用の索引 `data/mix/index.json` を非公開リポジトリに書き出します。
2. Cloudflare がその commit を検知して Worker（`worker/`）をビルドし直し、索引を Worker に組み込みます（`worker/build.mjs`）。Cloudflare のビルドはリポジトリを丸ごと取得するので、**GitHub のトークンは不要**です。
3. リンクを開くと、選んだ曲で `watch_videos` を呼んで一時リスト（`TLGG…`）を作り、`music.youtube.com` へ HTTP 302 でリダイレクトします。
4. Worker は Cloudflare Access で保護し、自分の Cloudflare アカウントでログインしたときだけ使えます。Worker 側でも、Access で認証されていないリクエストは拒否します。

### セットアップ（Cloudflare のダッシュボードで操作。手元に wrangler は不要）

画面の名前は変わることがあります。見つからないときは、Cloudflare のドキュメント（Workers Builds、Cloudflare Access）を参照してください。

**1. Worker を作ってリポジトリを接続（自動デプロイ）**
1. Cloudflare ダッシュボードの Workers & Pages → 作成 → リポジトリをインポート（Import a repository）を選びます。GitHub と連携し、**非公開リポジトリ**を選びます。
2. 設定は次のとおりにします。
   - プロジェクト名（Worker 名）: `yt-music-mix`（`worker/wrangler.toml` の `name` と同じにする必要があります）
   - ルートディレクトリ: `worker`
   - ビルドコマンド: 空欄、デプロイコマンド: `npx wrangler deploy`（既定のまま。デプロイの前に `build.mjs` が自動で動き、索引を組み込みます）
3. Worker の Settings → Build → Build watch paths の Include paths を、`worker/*` と `data/mix/*` の2つにします。毎日の同期で索引が変わったときと、Worker のコードを変えたときだけビルドされます（1日1回程度。無料プランのビルド時間は月3,000分です）。

**2. ログイン（Cloudflare Access）**
1. Worker の **Access** タブ → Protect this Worker behind Access を選びます。
2. 範囲は **All traffic**（workers.dev の URL もプレビュー用の URL も、すべてログインが必要になります）、ポリシーは **Cloudflare account**（このアカウントのメンバーだけ）を選び、適用します。
3. ログイン方法は Cloudflare アカウントです。2026年6月以降に作った Zero Trust の組織では、最初から使えます。それより前に作った組織では、Zero Trust → Integrations → Identity providers で「Cloudflare」を追加してください。
4. セッションの有効期間は長め（例：1か月）にすると、毎回ログインせずに済みます。
5. スマホで `https://yt-music-mix.<あなたのサブドメイン>.workers.dev/` を開き、Cloudflare アカウントでログインしてから、ホーム画面に追加します。

**3. 追加の提案の決定を、毎日の同期に渡す**

「追加の提案」の画面で選んだ「追加」「却下」は、Worker の Durable Object（SQLite 方式。無料プランで使えます）に保存されます。保存先は `worker/wrangler.toml` に書いてあるので、デプロイすると自動で作られます。毎日の同期（GitHub Actions）がそれを読めるように、次を設定します。
1. **サービストークン:** Zero Trust → Access controls → Service credentials → Service Tokens で、サービストークンを作ります（名前は例えば `yt-playlist-sync`）。期間（Service Token Duration）は **Non-expiring（無期限）** を選べます。期限付きにした場合は、切れる前に Cloudflare 側で延長してください。表示される Client ID と Client Secret を控えます（Secret は作ったときにしか表示されません）。漏れたときは、その場で失効させるか Secret を作り直します。このトークンで使えるのは同期用 API だけで、画面や「追加」「却下」の送信には使えません。
2. **Access のポリシーを追加:** Worker の Access の設定（アプリケーション）を開き、ポリシーを1つ追加します。**Action は Service Auth**、Include に **Service Token** でさっきのトークンを選びます。ふだんのログイン用のポリシーはそのまま残します。
3. **Worker の変数:** Worker の Settings → Variables and Secrets に、**変数** `SYNC_CLIENT_ID` としてサービストークンの **Client ID** を登録します（秘密の値ではありません）。Worker は、Access が通したリクエストのうち、このトークンで来たものだけを同期用 API に通します。ログインした人のブラウザからは同期用 API を使えません。
4. **GitHub の Secrets（非公開リポジトリ）:** 次の3つを登録します。

| 名前 | 値 |
|---|---|
| `MIX_WORKER_URL` | Worker の URL（例 `https://yt-music-mix.<サブドメイン>.workers.dev`） |
| `CF_ACCESS_CLIENT_ID` | サービストークンの Client ID |
| `CF_ACCESS_CLIENT_SECRET` | サービストークンの Client Secret |

設定しなくても同期は動きます（その場合、画面で選んだ決定は反映されず、レポートにも警告は出ません）。設定したのに読めないときは、同期のレポートに「追加の提案の決定を Worker から読めませんでした」と出ます。

**4. （任意）変数とシークレット**
Worker の Settings → Variables and Secrets で、必要なら次を**シークレット**として追加します。
| 名前 | 値 |
|---|---|
| `ALLOWED_EMAILS` | 使うアカウントのメールアドレス（複数ならカンマ区切り）。Access のポリシーに加えて、Worker 側でも本人か確かめます |

**注意**
- **ログイン後のアプリ起動:** ログイン画面を経由した直後は、YouTube Music アプリではなくブラウザで開くことがあります。2回目以降（ログイン済み）は、そのままアプリが開きます。ブラウザで開いてしまう場合は `?app=intent` を付けてください。
- **iPhone:** 別ドメインからのリダイレクトではアプリが開かず、Safari で Web 版の YouTube Music が開きやすくなります（画面上部のバナーからアプリに切り替えられます）。
- **一時リスト:** 時間がたつと使えなくなるので、保存用ではなく「今聴く」用です。残したいときは、YouTube Music の再生画面の「保存」から再生リストに保存できます。
- **非公式の仕組み:** `watch_videos` は YouTube の公式APIではないので、仕様が変わると動かなくなる可能性があります。一時リストを作れなかったときは、`watch_videos` の URL を直接開きます（YouTube のアプリかブラウザで再生されます）。
- **再生できない曲:** YouTube Music で再生できない動画は、リストの中でグレー表示になるか、飛ばされます。
- **索引の反映:** 毎日の同期のあと、Cloudflare のビルドが終わる（数分）と新しい曲が選ばれるようになります。メニューに索引の日時が出ます。
- **テスト:** `cd worker && npm test`（Node.js だけで動きます）

## バックアップと復元
`data/playlists/*.yaml` の各曲には `status` が付きます。

| status | 意味 |
|---|---|
| present | 再生リストに入っている |
| unavailable | 入っているが動画が削除・非公開（タイトル等は消える前の記録を保持） |
| missing | 以前は入っていたが、今はない |
| pending | 入るはずだが、クォータの都合で未追加（次回追加） |
| failed | 追加に失敗（エラー理由つき） |

再生リストが壊れたら、手元で：
```bash
python -m ytpl.restore --playlist "アリスチャンネル" --dry-run      # 消えた曲を確認
python -m ytpl.restore --playlist "アリスチャンネル"                # 消えた曲を戻す
python -m ytpl.restore --playlist "アリスチャンネル" --to-new "アリスチャンネル（復元）"  # 丸ごと作り直す
```
過去のある時点の状態は `git log -p data/playlists/アリスチャンネル.yaml` でたどれます。

## クォータについて
1日1万ユニットです。読み取りは50件ごとに1、再生リストへの追加は1件50、検索は1回100ユニットかかります。毎日の差分更新なら数百ユニットで済みます。足りない分は `pending` として翌日に回ります。

## 制限
- ショートの判定はAPIにないため、61秒未満または `#shorts` を含むものを除外しています（3分までのショートは取りこぼす場合あり）。
- 重複除去と昇格の判定は、タイトルから取り出した曲名に頼っています。タイトルの書き方が大きく違うと同じ曲と判定できず、両方入ることがあります（`exclude_video_ids` で調整）。
- 概要欄に @ハンドルやURLを書かず、名前だけでクレジットしている動画は「その他○○」・特別再生リストの自動判定から漏れます（`aliases` を書けば名前でも探しますが、その場合は「未仕分け」で確認します）。
- 曲名の照合は、タイトルから取り出した「曲名の候補」で行います（[docs/config.md](docs/config.md) の「曲名の照合」）。完全一致で原曲の手がかりがある曲だけを自動で追加し、部分一致や手がかりのない曲は確認に回します。タイトルの書き方によっては曲名の候補をうまく取り出せないので、レポートの確認候補を見て `include_video_ids` / `exclude_video_ids` で調整してください。短い曲名や一般的な単語は誤判定しやすいので、`exclude_keywords` で調整してください。
- 「その他○○」は監視しているチャンネルの動画から探します。それ以外のチャンネルも探すには `search: true` を使います。

## 別の認証方法（自前の OAuth クライアント）
Google Cloud の OAuth 同意画面を「本番環境」に公開できる場合、またはブランドアカウントを使う場合の方法です（「テスト」状態のままだと、トークンが7日で失効します）。

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成し、**YouTube Data API v3** を有効化
2. OAuth 同意画面（Google Auth Platform）を作成（対象：外部）し、公開ステータスを「本番環境」に変更
3. 「クライアント」→ 種類「デスクトップアプリ」で作成し、JSONをダウンロード
4. 手元で次を実行し、ブラウザで再生リストを持っているチャンネルを選んで許可
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt google-auth-oauthlib
   python scripts/get_token.py client_secret_xxx.json
   ```
5. 表示された `YT_CLIENT_ID` `YT_CLIENT_SECRET` `YT_REFRESH_TOKEN` を Secrets に登録（`YT_TOKEN_URL` は登録しない）

## 開発
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q                     # 偽APIを使ったテスト
python -m ytpl.check          # config.yaml の整合性チェック（--fix で整形も）
python -m ytpl.sync --dry-run # 手元で試す（環境変数が必要）
```
