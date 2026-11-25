// オリジナルミックスリスト API（Cloudflare Worker）
//
//   GET /                   メニュー（各ミックスへのリンク）
//   GET /random             完全ランダム
//   GET /playlists?k=3      ランダムに選んだ k 個の再生リストから
//   GET /top/<name>         特にお気に入りのチャンネル <name>（または番号）の再生リストから
//   GET /suggestions        ほかの再生リストへの追加の提案（提案先ごとの一覧）
//   GET /suggestions/<n>?p=0  n 番目の提案先の曲を、50曲ずつ YouTube の一時リストで開く（to=music で YouTube Music）
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
  MAX_VIDEOS, YT_PACKAGE, escapeHtml, intentUrl, musicUrl, parseWatchRedirect, pickMix, safeEqual, watchVideosUrl,
  youtubeUrl,
} from "./mix.js";

const EMPTY_SUGGESTIONS = { version: 1, generated_at: null, groups: [] };

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

const STYLE = `<style>body{font-family:system-ui,sans-serif;margin:24px;line-height:1.6}a{display:inline-block;padding:10px 0;font-size:1.1em}
li{list-style:none}ul{padding:0}small{color:#666}h2{margin-top:1.5em;font-size:1em;color:#444}
.btn{display:inline-block;margin:4px 8px 4px 0;padding:8px 12px;border:1px solid #888;border-radius:8px;text-decoration:none;font-size:1em}
.item{padding:6px 0;border-bottom:1px solid #eee}.item a{padding:0;font-size:1em}
.group{margin-top:1.5em;padding-top:.5em;border-top:3px solid #ccc}h2{font-size:1.15em;color:#222}</style>`;

function suggestionsPage(sug, base) {
  const groups = sug.groups || [];
  const total = groups.reduce((n, g) => n + g.items.length, 0);
  // 同じ曲が複数の再生リストに提案されている場合に、ほかの提案先も表示する
  const targetsOf = new Map();
  for (const g of groups) for (const it of g.items) targetsOf.set(it.id, [...(targetsOf.get(it.id) || []), g.name]);
  const nav = groups.map((g, n) => `<a class="btn" href="#g${n}">${escapeHtml(g.name)}（${g.items.length}）</a>`).join("");
  const body = groups.map((g, n) => {
    const name = escapeHtml(g.name);
    const pages = Math.ceil(g.items.length / MAX_VIDEOS);
    const opens = Array.from({ length: pages }, (_, p) => {
      const from = p * MAX_VIDEOS + 1;
      const to = Math.min(g.items.length, (p + 1) * MAX_VIDEOS);
      const label = pages > 1 ? `（${from}〜${to}曲目）` : "";
      return `<a class="btn" href="${base}/suggestions/${n}?p=${p}">「${name}」への提案を YouTube で開く${label}</a>` +
        `<a class="btn" href="${base}/suggestions/${n}?p=${p}&to=music">YouTube Music で開く${label}</a>`;
    }).join("");
    const target = g.id
      ? `<a href="https://www.youtube.com/playlist?list=${encodeURIComponent(g.id)}">提案先「${name}」を YouTube で開く</a>`
      : "<small>（提案先の再生リストはまだ作られていません）</small>";
    const items = g.items.map((it) => {
      const others = (targetsOf.get(it.id) || []).filter((x) => x !== g.name);
      return `<li class="item"><a href="https://youtu.be/${encodeURIComponent(it.id)}">${escapeHtml(it.title || it.id)}</a>
<br><small>${escapeHtml(it.channel || "")}</small>
<br><small>提案先: <b>${name}</b>・理由: ${escapeHtml(it.reason)}${
        it.in && it.in.length ? `・今入っている: ${escapeHtml(it.in.join("、"))}` : ""}${
        others.length ? `・ほかの提案先: ${escapeHtml(others.join("、"))}` : ""}</small></li>`;
    }).join("");
    return `<section id="g${n}" class="group"><h2>提案先: ${name}（${g.items.length} 曲）</h2>
<div>${opens}</div><div>${target}</div><ul>${items}</ul></section>`;
  }).join("");
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>追加の提案</title>${STYLE}</head><body>
<h1>追加の提案</h1>
<p><small>${total} 件（${escapeHtml(sug.generated_at || "")} 時点）。提案は<b>提案先の再生リストごと</b>に分かれています。</small></p>
<ol><li><small>「○○への提案を YouTube で開く」で、その提案先への提案だけを一時的な再生リストとして開きます。</small></li>
<li><small>採用する曲：再生画面の「保存」から、<b>その提案先</b>の再生リストに保存します。</small></li>
<li><small>却下する曲：「保存」から<b>「提案を却下」</b>に保存します。次回から提案されません。</small></li>
<li><small>どちらにもしなかった曲は「未確認」として、次回も提案されます。</small></li></ol>
<p><small>YouTube の一時的な再生リストには名前が付かないため、どの提案先の分を開いたかは、このページで確認してください。</small></p>
<div>${nav}</div>
${body || "<p>提案はありません。</p>"}<p><a href="${base}/">← メニュー</a></p></body></html>`;
}

function menu(index, base, sug = EMPTY_SUGGESTIONS) {
  const link = (href, label, note = "") =>
    `<li><a href="${escapeHtml(href)}">${escapeHtml(label)}</a>${note ? ` <small>${escapeHtml(note)}</small>` : ""}</li>`;
  const tops = (index.top_favorites || []).map((g) =>
    link(`${base}/top/${encodeURIComponent(g.name)}`, `${g.name} ミックス`, `${g.playlists.length} 再生リスト`)).join("");
  const n = (index.playlists || []).reduce((s, p) => s + (p.videos || []).length, 0);
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>オリジナルミックス</title>
${STYLE}</head><body>
<h1>オリジナルミックス</h1><p><small>${(index.playlists || []).length} 再生リスト・のべ ${n} 曲（${escapeHtml(index.generated_at || "")} 時点）</small></p>
<ul>${link(`${base}/random`, "完全ランダム")}${link(`${base}/playlists?k=3`, "ランダム再生リスト", "3つの再生リストから")}</ul>
<h2>特にお気に入りのチャンネル</h2><ul>${tops || "<li><small>なし</small></li>"}</ul>
<h2>管理</h2><ul>${link(`${base}/suggestions`, "追加の提案",
    `${(sug.groups || []).reduce((n, g) => n + g.items.length, 0)} 件`)}</ul>
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

/** 索引と提案を受け取ってハンドラを作る（テストでは別のデータを渡す）。 */
export function createHandler(index, suggestions = EMPTY_SUGGESTIONS) {
  return { fetch: (request, env, ctx) => handle(request, env, ctx, index, suggestions) };
}

async function handle(request, env, ctx, index, sug) {
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
    const html = (body) => new Response(body, {
      headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
    });
    if (parts.length === 0) return html(menu(index, base, sug));

    const q = Object.fromEntries(url.searchParams);
    if (parts[0] === "suggestions") {
      if (parts.length === 1) return html(suggestionsPage(sug, base));
      const g = (sug.groups || [])[Number(parts[1])];
      const page = Math.max(0, Number.parseInt(q.p ?? "0", 10) || 0);
      const ids = g ? g.items.slice(page * MAX_VIDEOS, (page + 1) * MAX_VIDEOS).map((it) => it.id) : [];
      if (ids.length === 0) return new Response("提案がありません", { status: 404 });
      const temp = await resolveTemporaryList(ids).catch(() => null);
      let target;
      if (!temp) target = watchVideosUrl(ids);
      else if (q.to === "music") target = q.app === "intent" ? intentUrl(musicUrl(temp)) : musicUrl(temp);
      else target = q.app === "intent" ? intentUrl(youtubeUrl(temp), YT_PACKAGE) : youtubeUrl(temp);
      if (format(q) === "json") return json({ name: g.name, videos: ids, temporary_list: temp?.list ?? null, open_url: target });
      return new Response(null, { status: 302, headers: { Location: target, "Cache-Control": "no-store" } });
    }
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
