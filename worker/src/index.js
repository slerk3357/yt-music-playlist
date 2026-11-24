// オリジナルミックスリスト API（Cloudflare Worker）
//
//   GET /                   メニュー（各ミックスへのリンク）
//   GET /random             完全ランダム
//   GET /playlists?k=3      ランダムに選んだ k 個の再生リストから
//   GET /top/<name>         特にお気に入りのチャンネル <name>（または番号）の再生リストから
//
// 認証：Worker を Cloudflare Access で保護し（Google ログイン）、Access で認証されていない
// リクエストは拒否する。ALLOWED_EMAILS を設定すると、そのメールアドレスだけに限定する。
// ACCESS_KEY を設定した場合は、URL の先頭に /<ACCESS_KEY> も必要になる（任意・二重の鍵）。
//
// 共通のクエリ:
//   n=50           曲数（1〜50）
//   open=list      曲一覧の画面を開く（既定はすぐ再生）
//   app=intent     Android でブラウザが開いてしまう場合に、intent:// でアプリを指定する
//   format=json    リダイレクトせず、選曲結果と URL を JSON で返す
//
// YouTube Data API は使わない（クォータ消費 0）。曲の一覧は、ビルド時（build.mjs）に
// 非公開リポジトリの data/mix/index.json を Worker に組み込んだもの。毎日の同期で索引が
// 更新されると、Cloudflare が自動でビルドし直す（ビルドの監視パスに data/mix/index.json を入れる）。

import {
  escapeHtml, intentUrl, musicUrl, parseWatchRedirect, pickMix, safeEqual, watchVideosUrl,
} from "./mix.js";

/** watch_videos で一時リスト（TLGG…）を作り、v と list を返す。失敗したら null。 */
async function resolveTemporaryList(videoIds) {
  const res = await fetch(watchVideosUrl(videoIds), {
    redirect: "manual",
    headers: {
      Cookie: "SOCS=CAI", // 同意画面（consent.youtube.com）へのリダイレクトを避ける
      "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Mobile Safari/537.36",
      "Accept-Language": "ja,en;q=0.8",
    },
  });
  return parseWatchRedirect(res.headers.get("location"));
}

function menu(index, base) {
  const link = (href, label, note = "") =>
    `<li><a href="${escapeHtml(href)}">${escapeHtml(label)}</a>${note ? ` <small>${escapeHtml(note)}</small>` : ""}</li>`;
  const tops = (index.top_favorites || []).map((g) =>
    link(`${base}/top/${encodeURIComponent(g.name)}`, `${g.name} ミックス`, `${g.playlists.length} 再生リスト`)).join("");
  const n = (index.playlists || []).reduce((s, p) => s + (p.videos || []).length, 0);
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>オリジナルミックス</title>
<style>body{font-family:system-ui,sans-serif;margin:24px;line-height:1.6}a{display:inline-block;padding:10px 0;font-size:1.1em}
li{list-style:none}ul{padding:0}small{color:#666}h2{margin-top:1.5em;font-size:1em;color:#444}</style></head><body>
<h1>オリジナルミックス</h1><p><small>${(index.playlists || []).length} 再生リスト・のべ ${n} 曲（${escapeHtml(index.generated_at || "")} 時点）</small></p>
<ul>${link(`${base}/random`, "完全ランダム")}${link(`${base}/playlists?k=3`, "ランダム再生リスト", "3つの再生リストから")}</ul>
<h2>特にお気に入りのチャンネル</h2><ul>${tops || "<li><small>なし</small></li>"}</ul>
<p><small>リンクを開くたびに選び直します。Android でブラウザが開く場合は、URL に <code>?app=intent</code> を付けてください。</small></p>
<p><small><a href="${escapeHtml(base)}/random?app=intent">完全ランダム（Android アプリを指定）</a></small></p>
</body></html>`;
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

/**
 * Cloudflare Access で認証されたリクエストか確かめる。
 * Worker 単位で Access を有効にすると ctx.access が渡される（認証されていなければ undefined）。
 */
export async function authorize(env, ctx) {
  if (env.REQUIRE_ACCESS === "false") return null; // 手元での試験用。本番では使わない
  if (!ctx?.access) return "Cloudflare Access によるログインが必要です（Worker の Access 設定を確認してください）";
  const allowed = String(env.ALLOWED_EMAILS || "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (allowed.length === 0) return null;
  let email = "";
  try {
    email = String((await ctx.access.getIdentity())?.email || "").toLowerCase();
  } catch {
    email = "";
  }
  return allowed.includes(email) ? null : "このアカウントでは利用できません";
}

/** 索引を受け取ってハンドラを作る（テストでは別の索引を渡す）。 */
export function createHandler(index) {
  return { fetch: (request, env, ctx) => handle(request, env, ctx, index) };
}

async function handle(request, env, ctx, index) {
  {
    const denied = await authorize(env, ctx);
    if (denied) return new Response(denied, { status: 403, headers: { "Content-Type": "text/plain; charset=utf-8" } });

    const url = new URL(request.url);
    let parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
    let base = "";
    if (env.ACCESS_KEY) {
      // 合言葉が合わなければ、存在しないページとして扱う
      if (!safeEqual(parts[0] ?? "", env.ACCESS_KEY)) return new Response("Not Found", { status: 404 });
      base = `/${encodeURIComponent(parts[0])}`;
      parts = parts.slice(1);
    }
    if (parts.length === 0) {
      return new Response(menu(index, base), {
        headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
      });
    }

    const q = Object.fromEntries(url.searchParams);
    const mode = parts[0];
    const mix = pickMix(index, mode, { ...q, name: parts[1] });
    if (mix.error) return format(q) === "json" ? json(mix, mix.status) : new Response(mix.error, { status: mix.status });
    if (mix.videos.length === 0) return new Response("対象の曲がありません", { status: 404 });

    const temp = await resolveTemporaryList(mix.videos).catch(() => null);
    const music = temp ? musicUrl(temp, q.open) : null;
    // 一時リストを作れなかったときは、watch_videos の URL を直接開く（YouTube 側で一時リストになる）
    const target = music ? (q.app === "intent" ? intentUrl(music) : music) : watchVideosUrl(mix.videos);
    if (format(q) === "json") {
      return json({ ...mix, temporary_list: temp?.list ?? null, music_url: music, open_url: target });
    }
    return new Response(null, { status: 302, headers: { Location: target, "Cache-Control": "no-store" } });
  }
}

function format(q) {
  return q.format === "json" ? "json" : "redirect";
}
