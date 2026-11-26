// オリジナルミックスリスト API（Cloudflare Worker）
//
//   GET /                   メニュー（各ミックスへのリンク）
//   GET /random             完全ランダム
//   GET /playlists?k=3      ランダムに選んだ k 個の再生リストから
//   GET /top/<name>         特にお気に入りのチャンネル <name>（または番号）の再生リストから
//   GET /suggestions        ほかの再生リストへの追加の提案（提案先ごとの一覧）
//   GET /suggestions/<n>?p=0  n 番目の提案先のまだ決めていない曲を、50曲ずつ YouTube の一時リストで開く（to=music で YouTube Music）
//   POST /api/decide        提案への決定（追加・却下・取り消し）。Durable Object（DECISIONS）に保存する
//   GET  /api/sync/decisions, POST /api/sync/ack   毎日の同期用（Access のサービストークン）
//
// 認証：Worker を Cloudflare Access で保護し（Google ログイン）、Access で認証されていない
// リクエストは拒否する。ALLOWED_EMAILS を設定すると、そのメールアドレスだけに限定する。
// 毎日の同期（/api/sync/…）は、Cloudflare Access のサービストークンで認証し、そのトークンの
// Client ID が SYNC_CLIENT_ID と同じかを Worker でも確かめる（合言葉のような独自の鍵は使わない）。
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

import { ACTIONS, ADD_ACTIONS, isFixed, stateLabel } from "./decisions.js";
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
.group{margin-top:1.5em;padding-top:.5em;border-top:3px solid #ccc}h2{font-size:1.15em;color:#222}
.item{display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap}.item[hidden]{display:none}.item.decided{opacity:.6}
.meta{flex:1 1 260px;min-width:0}.play{position:relative;padding:0;border:0;background:#000;cursor:pointer;width:160px;aspect-ratio:16/9}
.play img{width:100%;height:100%;object-fit:cover;display:block}.play span{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#fff;font-size:2em;text-shadow:0 0 6px #000}
.item iframe{width:min(100%,480px);aspect-ratio:16/9;border:0;flex:1 1 320px}
.actions{margin-top:6px}.actions button{margin:4px 6px 4px 0;padding:8px 14px;border-radius:8px;border:1px solid #888;background:#fff;font-size:1em}
.actions .add{background:#e7f3e7}.actions .misc{background:#eef0fb}.actions .reject{background:#f6e6e6}.actions button:disabled{opacity:.45}</style>`;

/** 提案先の再生リストID -> {group, n, items: Map(動画ID -> 提案)} */
function suggestionIndex(sug) {
  const m = new Map();
  (sug.groups || []).forEach((g, n) => {
    if (g.id) m.set(g.id, { group: g, n, items: new Map(g.items.map((it) => [it.id, it])) });
  });
  return m;
}

function decisionMap(records) {
  const m = new Map();
  for (const r of records || []) m.set(`${r.target}:${r.video}`, r);
  return m;
}

/** まだ決めていない提案（取り消したものも含む）。 */
function undecided(g, decisions) {
  if (!decisions || !g.id) return g.items;
  return g.items.filter((it) => {
    const r = decisions.get(`${g.id}:${it.id}`);
    return !r || r.action === "none" || (ADD_ACTIONS.includes(r.action) && r.applied_at && !isFixed(r));
  });
}

const PAGE_SCRIPT = `
const hide = document.getElementById("hide");
function refresh() {
  for (const li of document.querySelectorAll(".item")) {
    const a = li.dataset.action;
    li.classList.toggle("decided", !!a && a !== "none");
    li.hidden = hide.checked && li.classList.contains("decided");
    const done = li.dataset.fixed === "1";
    li.querySelector(".undo").hidden = !a || a === "none" || done;
    for (const b of li.querySelectorAll(".act")) b.disabled = done || li.dataset.busy === "1" || b.dataset.a === a;
  }
  for (const sec of document.querySelectorAll(".group")) {
    const all = sec.querySelectorAll(".item").length;
    const left = [...sec.querySelectorAll(".item")].filter((li) => !li.classList.contains("decided")).length;
    sec.querySelector(".left").textContent = "未決定 " + left + " / " + all + " 曲";
  }
}
document.addEventListener("click", async (ev) => {
  const play = ev.target.closest(".play");
  if (play) {
    const id = play.dataset.v;
    const f = document.createElement("iframe");
    f.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(id) + "?autoplay=1&playsinline=1&rel=0";
    f.allow = "autoplay; encrypted-media; picture-in-picture";
    f.allowFullscreen = true;
    f.title = play.dataset.title || id;
    play.replaceWith(f);
    return;
  }
  const btn = ev.target.closest(".act, .undo");
  if (!btn) return;
  const li = btn.closest(".item");
  const action = btn.classList.contains("undo") ? "none" : btn.dataset.a;
  li.dataset.busy = "1"; refresh();
  const st = li.querySelector(".state");
  st.textContent = "送信中…";
  try {
    const res = await fetch("/api/decide", {
      method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
      body: JSON.stringify({ target: li.dataset.t, video: li.dataset.v, action }),
    });
    const r = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(r.error || res.status);
    li.dataset.action = r.record ? r.record.action : "none";
    st.textContent = r.label || "";
  } catch (e) {
    st.textContent = "送信できませんでした: " + e.message;
  }
  li.dataset.busy = "0"; refresh();
});
hide.addEventListener("change", refresh);
refresh();
`;

function suggestionsPage(sug, decisions) {
  const groups = sug.groups || [];
  const total = groups.reduce((n, g) => n + g.items.length, 0);
  // 同じ曲が複数の再生リストに提案されている場合に、ほかの提案先も表示する
  const targetsOf = new Map();
  for (const g of groups) for (const it of g.items) targetsOf.set(it.id, [...(targetsOf.get(it.id) || []), g.name]);
  const nav = groups.map((g, n) => `<a class="btn" href="#g${n}">${escapeHtml(g.name)}（${g.items.length}）</a>`).join("");
  const body = groups.map((g, n) => {
    const name = escapeHtml(g.name);
    const left = undecided(g, decisions);
    const pages = Math.ceil(left.length / MAX_VIDEOS);
    const opens = Array.from({ length: pages }, (_, p) => {
      const from = p * MAX_VIDEOS + 1;
      const to = Math.min(left.length, (p + 1) * MAX_VIDEOS);
      const label = pages > 1 ? `（${from}〜${to}曲目）` : "";
      return `<a class="btn" href="/suggestions/${n}?p=${p}">まとめて YouTube で開く${label}</a>` +
        `<a class="btn" href="/suggestions/${n}?p=${p}&to=music">YouTube Music で開く${label}</a>`;
    }).join("");
    const target = g.id
      ? `<a href="https://www.youtube.com/playlist?list=${encodeURIComponent(g.id)}">提案先「${name}」を YouTube で開く</a>`
      : "<small>（提案先の再生リストが見つからないため、ここでは決められません）</small>";
    const items = g.items.map((it) => {
      const others = (targetsOf.get(it.id) || []).filter((x) => x !== g.name);
      const rec = decisions && g.id ? decisions.get(`${g.id}:${it.id}`) : null;
      const fixed = isFixed(rec);
      // 「その他の曲に追加」：その他の曲の再生リストがあり、提案先がその他の曲ではなく、まだ入っていない曲だけ
      const canMisc = !!(sug.misc && sug.misc.id && g.id !== sug.misc.id && !(it.in || []).includes(sug.misc.name));
      const vid = escapeHtml(it.id);
      const canDecide = !!(g.id && decisions);
      return `<li class="item" data-t="${escapeHtml(g.id || "")}" data-v="${vid}" data-action="${escapeHtml(rec?.action || "")}" data-fixed="${fixed ? 1 : 0}">
<button class="play" data-v="${vid}" data-title="${escapeHtml(it.title || "")}" aria-label="再生"><img loading="lazy" alt="" src="https://i.ytimg.com/vi/${vid}/mqdefault.jpg"><span>▶</span></button>
<div class="meta"><a href="https://youtu.be/${vid}">${escapeHtml(it.title || it.id)}</a>
<br><small>${escapeHtml(it.channel || "")}${it.published_at ? `・${escapeHtml(String(it.published_at).slice(0, 10))}` : ""}</small>
<br><small>理由: ${escapeHtml(it.reason)}${
        it.in && it.in.length ? `・今入っている: ${escapeHtml(it.in.join("、"))}` : ""}${
        others.length ? `・ほかの提案先: ${escapeHtml(others.join("、"))}` : ""}</small>
${canDecide ? `<div class="actions"><button class="act add" data-a="add">「${name}」に追加</button>${
        canMisc ? `<button class="act misc" data-a="misc">「${escapeHtml(sug.misc.name)}」に追加</button>` : ""}<button class="act reject" data-a="reject">却下</button>
<button class="undo" hidden>取り消し</button> <small class="state">${escapeHtml(stateLabel(rec))}</small></div>` : ""}</div></li>`;
    }).join("");
    return `<section id="g${n}" class="group"><h2>提案先: ${name}（${g.items.length} 曲）</h2>
<p><small class="left"></small></p><div>${target}</div><div>${opens}</div><ul>${items}</ul></section>`;
  }).join("");
  const note = decisions
    ? `<p><small>曲ごとに ▶ で再生して確かめ、<b>追加</b>・<b>その他の曲に追加</b>・<b>却下</b>のどれかを選びます。
「その他の曲に追加」にした曲は、提案先ではなく「その他の曲」に入り、その提案先にはもう提案しません。追加にした曲は、<b>次回の同期</b>（毎朝）で提案先の再生リストに入ります。
却下にした曲は、その提案先にはもう提案しません。どちらも選ばなかった曲は、次回も提案されます。
追加の反映前と、却下は「取り消し」で戻せます（反映済みの追加は、このシステムでは消せません）。</small></p>`
    : `<p><small><b>決定の保存先（Durable Object）が設定されていないため、追加・却下は選べません。</b>README の「追加の提案の決定を、毎日の同期に渡す」を確認してください。</small></p>`;
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>追加の提案</title>${STYLE}</head><body>
<h1>追加の提案</h1>
<p><small>${total} 件（${escapeHtml(sug.generated_at || "")} 時点）。提案は<b>提案先の再生リストごと</b>に分かれています。</small></p>
${note}
<p><label><input type="checkbox" id="hide" checked> 決めた曲を隠す</label></p>
<div>${nav}</div>
${body || "<p>提案はありません。</p>"}<p><a href="/">← メニュー</a></p>
<script>${PAGE_SCRIPT}</script></body></html>`;
}

function menu(index, sug = EMPTY_SUGGESTIONS) {
  const link = (href, label, note = "") =>
    `<li><a href="${escapeHtml(href)}">${escapeHtml(label)}</a>${note ? ` <small>${escapeHtml(note)}</small>` : ""}</li>`;
  const tops = (index.top_favorites || []).map((g) =>
    link(`/top/${encodeURIComponent(g.name)}`, `${g.name} ミックス`, `${g.playlists.length} 再生リスト`)).join("");
  const n = (index.playlists || []).reduce((s, p) => s + (p.videos || []).length, 0);
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>オリジナルミックス</title>
${STYLE}</head><body>
<h1>オリジナルミックス</h1><p><small>${(index.playlists || []).length} 再生リスト・のべ ${n} 曲（${escapeHtml(index.generated_at || "")} 時点）</small></p>
<ul>${link(`/random`, "完全ランダム")}${link(`/playlists?k=3`, "ランダム再生リスト", "3つの再生リストから")}</ul>
<h2>特にお気に入りのチャンネル</h2><ul>${tops || "<li><small>なし</small></li>"}</ul>
<h2>管理</h2><ul>${link(`/suggestions`, "追加の提案",
    `${(sug.groups || []).reduce((n, g) => n + g.items.length, 0)} 件`)}</ul>
<p><small>リンクを開くたびに選び直します。Android でブラウザが開く場合は、URL に <code>?app=intent</code> を付けてください。</small></p>
<p><small><a href="/random?app=intent">完全ランダム（Android アプリを指定）</a></small></p>
</body></html>`;
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

/**
 * Cloudflare Access で認証されたリクエストか確かめる。拒否の理由（文字列）か null を返す。
 * Worker 単位で Access を有効にすると ctx.access が渡される（認証されていなければ undefined）。
 * service が true（毎日の同期からの呼び出し）のときは、メールアドレスでは確かめない
 * （サービストークンにはメールアドレスがないため。代わりにトークンの Client ID で確かめる）。
 */
export async function authorize(env, ctx, service = false, request = null) {
  if (!ctx?.access) return "Cloudflare Access によるログインが必要です（Worker の Access 設定を確認してください）";
  if (service) {
    const expected = String(env.SYNC_CLIENT_ID || "").trim();
    if (!expected) return "SYNC_CLIENT_ID（同期用サービストークンの Client ID）が設定されていません";
    const got = await serviceClientId(ctx, request);
    return got && safeEqual(got, expected) ? null : "同期用のサービストークンではありません";
  }
  // 画面と /api/decide は、ログインした人（メールアドレスのある認証）だけ。サービストークンでは使えない
  const email = await identityEmail(ctx);
  if (!email) return "ログインした人だけが使えます（サービストークンでは使えません）";
  const allowed = String(env.ALLOWED_EMAILS || "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (allowed.length === 0) return null;
  return allowed.includes(email) ? null : "このアカウントでは利用できません";
}

/**
 * サービストークンで認証されたリクエストなら、そのトークンの Client ID（common_name）を返す。
 * Access が検証済みのトークン（Cf-Access-Jwt-Assertion）か、ctx.access.getIdentity() から読む。
 * ctx.access があること（＝Access が通したこと）を確かめてから呼ぶ。
 */
export async function serviceClientId(ctx, request) {
  const pick = (o) => (o && !o.email && typeof o.common_name === "string" && o.common_name) || "";
  const jwt = request?.headers.get("Cf-Access-Jwt-Assertion") || "";
  const part = jwt.split(".")[1];
  if (part) {
    try {
      const b64 = part.replace(/-/g, "+").replace(/_/g, "/");
      const got = pick(JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)))));
      if (got) return got;
    } catch {
      // 読めなければ getIdentity() を試す
    }
  }
  try {
    return pick(await ctx?.access?.getIdentity());
  } catch {
    return "";
  }
}

async function identityEmail(ctx) {
  try {
    return String((await ctx?.access?.getIdentity())?.email || "").toLowerCase();
  } catch {
    return "";
  }
}

/** 決定の保存先（Durable Object）。設定されていなければ null。 */
function decisionStore(env) {
  const ns = env.DECISIONS;
  if (!ns) return null;
  return ns.get(ns.idFromName("main"));
}

async function readDecisions(env) {
  const store = decisionStore(env);
  if (!store) return null;
  return decisionMap(await store.list());
}

/** 索引と提案を受け取ってハンドラを作る（テストでは別のデータを渡す）。 */
export function createHandler(index, suggestions = EMPTY_SUGGESTIONS) {
  const sugIndex = suggestionIndex(suggestions);
  return { fetch: (request, env, ctx) => handle(request, env, ctx, index, suggestions, sugIndex) };
}

const text = (body, status) => new Response(body, { status, headers: { "Content-Type": "text/plain; charset=utf-8" } });

async function handle(request, env, ctx, index, sug, sugIndex) {
  const url = new URL(request.url);
  const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  const service = parts[0] === "api" && parts[1] === "sync";
  const denied = await authorize(env, ctx, service, request);
  if (denied) return text(denied, 403);
  if (parts[0] === "api") return api(request, env, ctx, url, parts, sugIndex, sug);
  if (request.method !== "GET" && request.method !== "HEAD") return text("Method Not Allowed", 405);

  const html = (body) => new Response(body, {
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
  if (parts.length === 0) return html(menu(index, sug));

  const q = Object.fromEntries(url.searchParams);
  if (parts[0] === "suggestions") {
    const decisions = await readDecisions(env).catch(() => null);
    if (parts.length === 1) return html(suggestionsPage(sug, decisions));
    const g = (sug.groups || [])[Number(parts[1])];
    const page = Math.max(0, Number.parseInt(q.p ?? "0", 10) || 0);
    const ids = g ? undecided(g, decisions).slice(page * MAX_VIDEOS, (page + 1) * MAX_VIDEOS).map((it) => it.id) : [];
    if (ids.length === 0) return text("提案がありません", 404);
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
  if (mix.error) return format(q) === "json" ? json(mix, mix.status) : text(mix.error, mix.status);
  if (mix.videos.length === 0) return text("対象の曲がありません", 404);

  const temp = await resolveTemporaryList(mix.videos).catch(() => null);
  const music = temp ? musicUrl(temp, q.open) : null;
  // 一時リストを作れなかったときは、watch_videos の URL を直接開く（YouTube 側で一時リストになる）
  const target = music ? (q.app === "intent" ? intentUrl(music) : music) : watchVideosUrl(mix.videos);
  if (format(q) === "json") {
    return json({ ...mix, temporary_list: temp?.list ?? null, music_url: music, open_url: target });
  }
  return new Response(null, { status: 302, headers: { Location: target, "Cache-Control": "no-store" } });
}

/**
 * API
 *   POST /api/decide              {target, video, action}  画面のボタンから（action: add / reject / none）
 *   GET  /api/sync/decisions      毎日の同期が決定を読む（同期用のサービストークンが必要）
 *   POST /api/sync/ack            毎日の同期が反映結果を返す {results: [...]}（同期用のサービストークンが必要）
 */
async function api(request, env, ctx, url, parts, sugIndex, sug) {
  const store = decisionStore(env);
  if (!store) return json({ error: "決定の保存先（DECISIONS）が設定されていません" }, 503);
  const route = parts.slice(1).join("/");

  if (route.startsWith("sync/")) {
    if (route === "sync/decisions" && request.method === "GET") {
      return json({ version: 1, decisions: await store.list() });
    }
    if (route === "sync/ack" && request.method === "POST") {
      const body = await request.json().catch(() => null);
      if (!body || !Array.isArray(body.results)) return json({ error: "results がありません" }, 400);
      return json({ acknowledged: await store.ack(body.results) });
    }
    return json({ error: "Not Found" }, 404);
  }

  if (route === "decide" && request.method === "POST") {
    // ほかのサイトから送らせない（同じオリジンの画面からの JSON だけを受け付ける）
    if (request.headers.get("Origin") !== url.origin) return json({ error: "送信元が違います" }, 403);
    if (!String(request.headers.get("Content-Type") || "").startsWith("application/json")) {
      return json({ error: "JSON で送ってください" }, 415);
    }
    const body = await request.json().catch(() => null);
    const { target, video, action } = body || {};
    if (!ACTIONS.includes(action)) return json({ error: "action は add / misc / reject / none のどれかです" }, 400);
    if (action === "misc" && !(sug.misc && sug.misc.id)) return json({ error: "「その他の曲」の再生リストがありません" }, 400);
    // 今の提案に含まれている曲と提案先だけを受け付ける
    const g = sugIndex.get(String(target || ""));
    const it = g?.items.get(String(video || ""));
    if (!it) return json({ error: "この曲は今の提案にありません（ページを読み込み直してください）" }, 404);
    const r = await store.decide(target, video, action, {
      target_name: g.group.name, title: it.title ?? null, channel: it.channel ?? null, by: (await identityEmail(ctx)) || null,
    });
    if (!r.ok) return json({ error: r.error, record: r.record ?? null, label: stateLabel(r.record) }, r.status);
    return json({ record: r.record, label: stateLabel(r.record) });
  }
  return json({ error: "Not Found" }, 404);
}

function format(q) {
  return q.format === "json" ? "json" : "redirect";
}
