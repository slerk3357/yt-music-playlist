// 各ルート（routes/）の処理。Hono の Context（c）を受け取り、Response を返す。
//
// c.get("data")      : { index, sug, review, sugIndex }（ビルド時に組み込んだデータ。middleware/02.context.js）
// c.get("decisions") : DecisionLog（決定の保存先。D1）
// c.get("email")     : ログインした人のメールアドレス（middleware/01.access.js）
//
// YouTube Data API は使わない（クォータ消費 0）。一時リストは watch_videos で作る。
import { ACTIONS, stateLabel } from "./decisions.js";
import {
  MAX_VIDEOS, YT_PACKAGE, intentUrl, musicUrl, parseWatchRedirect, pickMix, watchVideosUrl, youtubeUrl,
} from "./mix.js";
import {
  EMPTY_REVIEW, EMPTY_SUGGESTIONS, decisionMap, handPage, menu, reviewPage, suggestionIndex, suggestionsPage, undecided,
} from "./pages.js";

/** 組み込んだデータから、ルートで使うデータを作る。 */
export function buildData(index, suggestions, review) {
  const sug = { ...EMPTY_SUGGESTIONS, ...(suggestions || {}) };
  return { index, sug, review: { ...EMPTY_REVIEW, ...(review || {}) }, sugIndex: suggestionIndex(sug) };
}

const NO_STORE = { "Cache-Control": "no-store" };

export const html = (body, status = 200) =>
  new Response(body, { status, headers: { "Content-Type": "text/html; charset=utf-8", ...NO_STORE } });
export const text = (body, status = 200) =>
  new Response(body, { status, headers: { "Content-Type": "text/plain; charset=utf-8", ...NO_STORE } });
export const json = (data, status = 200) =>
  new Response(JSON.stringify(data, null, 2), { status, headers: { "Content-Type": "application/json; charset=utf-8", ...NO_STORE } });
const redirect = (location) => new Response(null, { status: 302, headers: { Location: location, ...NO_STORE } });

/** watch_videos で一時リスト（TLGG…）を作り、v と list を返す。失敗したら null。 */
export async function resolveTemporaryList(videoIds, fetchImpl = fetch) {
  const res = await fetchImpl(watchVideosUrl(videoIds), {
    redirect: "manual",
    headers: {
      Cookie: "SOCS=CAI", // 同意画面（consent.youtube.com）へのリダイレクトを避ける
      "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Mobile Safari/537.36",
      "Accept-Language": "ja,en;q=0.8",
    },
  });
  return parseWatchRedirect(res.headers.get("location"));
}

const query = (c) => c.req.query() || {};
const wantsJson = (q) => q.format === "json";
const pageOf = (q) => Math.max(0, Number.parseInt(q.p ?? "0", 10) || 0);
const tempList = (c, ids) => (c.get("resolveTemporaryList") || resolveTemporaryList)(ids).catch(() => null);

async function readDecisions(c) {
  const log = c.get("decisions");
  if (!log) return null;
  try {
    return decisionMap(await log.list());
  } catch {
    return null;
  }
}

// ---- 画面 ------------------------------------------------------------------------------

/** GET / メニュー */
export function menuRoute(c) {
  const { index, sug, review } = c.get("data");
  return html(menu(index, sug, review));
}

/** GET /random・/playlists?k=3・/top/<name> ミックス（YouTube Music の一時リストへ 302） */
export async function mixRoute(c, mode) {
  const q = query(c);
  const mix = pickMix(c.get("data").index, mode, { ...q, name: c.req.param("name") });
  if (mix.error) return wantsJson(q) ? json(mix, mix.status) : text(mix.error, mix.status);
  if (mix.videos.length === 0) return text("対象の曲がありません", 404);
  const temp = await tempList(c, mix.videos);
  const music = temp ? musicUrl(temp, q.open) : null;
  // 一時リストを作れなかったときは、watch_videos の URL を直接開く（YouTube 側で一時リストになる）
  const target = music ? (q.app === "intent" ? intentUrl(music) : music) : watchVideosUrl(mix.videos);
  if (wantsJson(q)) return json({ ...mix, temporary_list: temp?.list ?? null, music_url: music, open_url: target });
  return redirect(target);
}

/** GET /suggestions 追加の提案（提案先ごと） */
export async function suggestionsRoute(c) {
  return html(suggestionsPage(c.get("data").sug, await readDecisions(c)));
}

/** GET /suggestions/<n>?p=0 n 番目の提案先の、まだ決めていない曲を50曲ずつ一時リストで開く（to=music で YouTube Music） */
export async function suggestionsOpenRoute(c) {
  const q = query(c);
  const g = (c.get("data").sug.groups || [])[Number(c.req.param("n"))];
  const page = pageOf(q);
  const decisions = await readDecisions(c);
  const ids = g ? undecided(g, decisions).slice(page * MAX_VIDEOS, (page + 1) * MAX_VIDEOS).map((it) => it.id) : [];
  if (ids.length === 0) return text("提案がありません", 404);
  const temp = await tempList(c, ids);
  let target;
  if (!temp) target = watchVideosUrl(ids);
  else if (q.to === "music") target = q.app === "intent" ? intentUrl(musicUrl(temp)) : musicUrl(temp);
  else target = q.app === "intent" ? intentUrl(youtubeUrl(temp), YT_PACKAGE) : youtubeUrl(temp);
  if (wantsJson(q)) return json({ name: g.name, videos: ids, temporary_list: temp?.list ?? null, open_url: target });
  return redirect(target);
}

/** GET /hand 手で追加してほしい曲（クォータが足りない日の分） */
export function handRoute(c) {
  return html(handPage(c.get("data").sug));
}

/** GET /hand/<n>?p=0 n 番目の再生リストに手で追加してほしい曲を、50曲ずつ YouTube アプリの一時リストで開く */
export async function handOpenRoute(c) {
  const q = query(c);
  const g = (c.get("data").sug.hand || [])[Number(c.req.param("n"))];
  const page = pageOf(q);
  const ids = g ? g.items.slice(page * MAX_VIDEOS, (page + 1) * MAX_VIDEOS).map((it) => it.id) : [];
  if (ids.length === 0) return text("手で追加してほしい曲がありません", 404);
  const temp = await tempList(c, ids);
  const yt = temp ? youtubeUrl(temp) : watchVideosUrl(ids);
  const target = q.app === "intent" ? intentUrl(yt, YT_PACKAGE) : yt;
  if (wantsJson(q)) return json({ name: g.name, videos: ids, temporary_list: temp?.list ?? null, open_url: target });
  return redirect(target);
}

/** GET /review 要確認（config.yaml で「# 要確認」が付いている項目） */
export function reviewRoute(c) {
  return html(reviewPage(c.get("data").review));
}

// ---- API -------------------------------------------------------------------------------

/** POST /api/decide {target, video, action} 画面のボタンから（action: add / misc / reject / none） */
export async function decideRoute(c) {
  const log = c.get("decisions");
  if (!log) return json({ error: "決定の保存先（D1）が使えません" }, 503);
  const { sug, sugIndex } = c.get("data");
  // ほかのサイトから送らせない（同じオリジンの画面からの JSON だけを受け付ける）
  if (c.req.header("Origin") !== new URL(c.req.url).origin) return json({ error: "送信元が違います" }, 403);
  if (!String(c.req.header("Content-Type") || "").startsWith("application/json")) {
    return json({ error: "JSON で送ってください" }, 415);
  }
  const body = await c.req.json().catch(() => null);
  const { target, video, action } = body || {};
  if (!ACTIONS.includes(action)) return json({ error: "action は add / misc / reject / none のどれかです" }, 400);
  if (action === "misc" && !(sug.misc && sug.misc.id)) return json({ error: "「その他の曲」の再生リストがありません" }, 400);
  // 今の提案に含まれている曲と提案先だけを受け付ける
  const g = sugIndex.get(String(target || ""));
  const it = g?.items.get(String(video || ""));
  if (!it) return json({ error: "この曲は今の提案にありません（ページを読み込み直してください）" }, 404);
  const r = await log.decide(target, video, action, {
    target_name: g.group.name, title: it.title ?? null, channel: it.channel ?? null, by: c.get("email") || null,
  });
  if (!r.ok) return json({ error: r.error, record: r.record ?? null, label: stateLabel(r.record) }, r.status);
  return json({ record: r.record, label: stateLabel(r.record) });
}

/** GET /api/sync/decisions 毎日の同期が決定を読む（同期用のサービストークンだけ。middleware で確認） */
export async function syncDecisionsRoute(c) {
  const log = c.get("decisions");
  if (!log) return json({ error: "決定の保存先（D1）が使えません" }, 503);
  return json({ version: 1, decisions: await log.list() });
}

/** POST /api/sync/ack 毎日の同期が反映結果を返す {results: [...]} */
export async function syncAckRoute(c) {
  const log = c.get("decisions");
  if (!log) return json({ error: "決定の保存先（D1）が使えません" }, 503);
  const body = await c.req.json().catch(() => null);
  if (!body || !Array.isArray(body.results)) return json({ error: "results がありません" }, 400);
  return json({ acknowledged: await log.ack(body.results) });
}
