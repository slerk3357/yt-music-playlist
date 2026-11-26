import assert from "node:assert/strict";
import test from "node:test";
import {
  clampCount, intentUrl, musicUrl, parseWatchRedirect, pickMix, safeEqual, sample, watchVideosUrl,
} from "../src/mix.js";
import { ROUTE_TABLE, createHandler } from "./harness.js";

// 決まった順に値を返す乱数
function seq(...values) {
  let i = 0;
  return () => values[i++ % values.length];
}

const INDEX_DATA = {
  version: 1,
  generated_at: "2026-11-24T04:00:00+09:00",
  playlists: [
    { id: "PL1", name: "Aチャンネル", kind: "channel", videos: ["a1", "a2", "x"] },
    { id: "PL2", name: "その他A", kind: "other", videos: ["x", "o1"] },
    { id: "PL3", name: "特別", kind: "special", videos: ["s1"] },
    { id: "PL4", name: "曲", kind: "song", videos: ["c1", "c2"] },
    { id: "PL5", name: "空", kind: "song", videos: [] },
  ],
  top_favorites: [{ name: "A", playlists: ["PL1", "PL2", "PL3"] }],
};

test("sample は重複なく n 個を選ぶ", () => {
  const out = sample(["a", "b", "c", "d"], 3, seq(0.9, 0.1, 0.5));
  assert.equal(out.length, 3);
  assert.equal(new Set(out).size, 3);
  assert.deepEqual(sample(["a"], 5), ["a"]);
});

test("clampCount は 1〜50 に丸める", () => {
  assert.equal(clampCount("0"), 1);
  assert.equal(clampCount("999"), 50);
  assert.equal(clampCount(undefined), 50);
  assert.equal(clampCount("12"), 12);
});

test("完全ランダムは全再生リストの動画を重複なく対象にする", () => {
  const m = pickMix(INDEX_DATA, "random", { n: "50" });
  assert.deepEqual(new Set(m.videos), new Set(["a1", "a2", "x", "o1", "s1", "c1", "c2"]));
  assert.equal(m.videos.length, 7);
});

test("ランダム再生リストは選んだ k 個の再生リストの動画だけ", () => {
  const m = pickMix(INDEX_DATA, "playlists", { k: "1", n: "50" }, seq(0.99));
  assert.equal(m.playlists.length, 1);
  const chosen = INDEX_DATA.playlists.find((p) => p.name === m.playlists[0]);
  assert.deepEqual(new Set(m.videos), new Set(chosen.videos));
  assert.ok(!m.playlists.includes("空"));            // 空の再生リストは選ばない
});

test("特にお気に入りのミックスは ○○チャンネル＋その他＋特別 の和集合", () => {
  const m = pickMix(INDEX_DATA, "top", { name: "A" });
  assert.deepEqual(new Set(m.videos), new Set(["a1", "a2", "x", "o1", "s1"]));
  assert.deepEqual(pickMix(INDEX_DATA, "top", { name: "0" }).title, "A ミックス");   // 番号でも指定できる
  assert.equal(pickMix(INDEX_DATA, "top", { name: "Z" }).status, 404);
  assert.equal(pickMix(INDEX_DATA, "unknown").status, 404);
});

test("URL の組み立てと解析", () => {
  assert.equal(watchVideosUrl(["a", "b"]), "https://www.youtube.com/watch_videos?video_ids=a,b");
  assert.equal(watchVideosUrl(Array.from({ length: 60 }, (_, i) => `v${i}`)).split(",").length, 50);
  const t = parseWatchRedirect("https://www.youtube.com/watch?v=a1&list=TLGGabc");
  assert.deepEqual(t, { v: "a1", list: "TLGGabc" });
  assert.equal(parseWatchRedirect("/watch?v=a1&list=TLGGabc").list, "TLGGabc");
  assert.equal(parseWatchRedirect("https://consent.youtube.com/m?continue=x"), null);
  assert.equal(musicUrl(t), "https://music.youtube.com/watch?v=a1&list=TLGGabc");
  assert.equal(musicUrl(t, "list"), "https://music.youtube.com/playlist?list=TLGGabc");
  assert.equal(intentUrl(musicUrl(t)),
    "intent://music.youtube.com/watch?v=a1&list=TLGGabc#Intent;scheme=https;package=com.google.android.apps.youtube.music;end");
  assert.ok(safeEqual("abc", "abc") && !safeEqual("abc", "abd") && !safeEqual("abc", "ab"));
});

// ---- Worker 全体（fetch を差し替えて確認。索引は組み込み済みのものを渡す） ----
const worker = createHandler(INDEX_DATA);

function setupWorld({ location = "https://www.youtube.com/watch?v=a1&list=TLGGxyz" } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    if (String(url).startsWith("https://www.youtube.com/watch_videos")) {
      assert.equal(init.redirect, "manual");
      return new Response(null, { status: 303, headers: location ? { location } : {} });
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  return calls;
}

const ENV = { ALLOWED_EMAILS: "me@gmail.com" };
// Cloudflare Access で認証されたときに渡される ctx
const CTX = { access: { aud: "aud", getIdentity: async () => ({ email: "Me@gmail.com" }) } };

test("Cloudflare Access で認証されていなければ 403", async () => {
  setupWorld();
  const res = await worker.fetch(new Request("https://mix.example/random"), ENV, {});
  assert.equal(res.status, 403);
});

test("許可していないメールアドレスなら 403", async () => {
  setupWorld();
  const other = { access: { getIdentity: async () => ({ email: "someone@example.com" }) } };
  assert.equal((await worker.fetch(new Request("https://mix.example/random"), ENV, other)).status, 403);
  const broken = { access: { getIdentity: async () => { throw new Error("x"); } } };
  assert.equal((await worker.fetch(new Request("https://mix.example/random"), ENV, broken)).status, 403);
});

test("URL の先頭に合言葉は使わない（ACCESS_KEY は無視する）", async () => {
  setupWorld();
  const env = { ...ENV, ACCESS_KEY: "secret-key" };
  assert.equal((await worker.fetch(new Request("https://mix.example/random"), env, CTX)).status, 302);
  assert.equal((await worker.fetch(new Request("https://mix.example/secret-key/random"), env, CTX)).status, 404);
});

test("ミックスは YouTube Music の一時リストへ 302", async () => {
  setupWorld();
  const res = await worker.fetch(new Request("https://mix.example/top/A?n=5"), ENV, CTX);
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("location"), "https://music.youtube.com/watch?v=a1&list=TLGGxyz");
  const intent = await worker.fetch(new Request("https://mix.example/random?app=intent"), ENV, CTX);
  assert.match(intent.headers.get("location"), /^intent:\/\/music\.youtube\.com\/watch\?v=a1&list=TLGGxyz#Intent;/);
});

test("format=json は選曲結果を返す", async () => {
  setupWorld();
  const res = await worker.fetch(new Request("https://mix.example/playlists?k=2&n=3&format=json"), ENV, CTX);
  const body = await res.json();
  assert.equal(body.playlists.length, 2);
  assert.ok(body.videos.length <= 3);
  assert.equal(body.temporary_list, "TLGGxyz");
});

test("一時リストを作れなければ watch_videos の URL へ", async () => {
  setupWorld({ location: "https://consent.youtube.com/m" });
  const res = await worker.fetch(new Request("https://mix.example/random?n=2"), ENV, CTX);
  assert.match(res.headers.get("location"), /^https:\/\/www\.youtube\.com\/watch_videos\?video_ids=/);
});

test("メニューに各ミックスへのリンクがある", async () => {
  setupWorld();
  const res = await worker.fetch(new Request("https://mix.example/"), ENV, CTX);
  const html = await res.text();
  assert.match(html, /href="\/random"/);
  assert.match(html, /href="\/top\/A"/);
});


test("ビルド時の組み込み（embed-data.mjs）：data/mix/ がなければ空のデータ", async () => {
  const { embedData } = await import("../embed-data.mjs");
  const out = embedData({ quiet: true });
  for (const k of ["index.json", "suggestions.json", "review.json"]) assert.ok(out[k] && out[k].version === 1, k);
});

test("ルートのファイル構成（Void の routes/）", () => {
  const paths = ROUTE_TABLE.map((r) => `${Object.keys(r.module).filter((k) => k === k.toUpperCase()).join(",")} ${r.path}`).sort();
  assert.deepEqual(paths, [
    "GET /", "GET /hand", "GET /hand/:n", "GET /playlists", "GET /random", "GET /review",
    "GET /suggestions", "GET /suggestions/:n", "GET /top/:name",
    "GET /api/sync/decisions", "POST /api/decide", "POST /api/sync/ack",
  ].sort());
});

test("ALLOWED_EMAILS が空なら Access の認証だけで使える", async () => {
  setupWorld();
  const res = await worker.fetch(new Request("https://mix.example/random"), {}, CTX);
  assert.equal(res.status, 302);
});

// ---- 追加の提案 ----
const SUG = {
  version: 1,
  generated_at: "2026-11-25T04:00:00+09:00",
  groups: [
    { name: "その他A", id: "PLother", items: Array.from({ length: 60 }, (_, i) => ({
      id: `s${i}`, title: `曲${i}`, channel: "B", reason: "名前（aliases）だけの一致", in: ["その他の曲"] })) },
    { name: "曲X", id: null, items: [{ id: "x1", title: "曲X / C", channel: "C", reason: "曲名の部分一致", in: [] }] },
  ],
};

test("提案ページに提案先ごとの一覧と開くボタンがある", async () => {
  setupWorld();
  const w = createHandler(INDEX_DATA, SUG);
  const html = await (await w.fetch(new Request("https://mix.example/suggestions"), ENV, CTX)).text();
  assert.match(html, /提案先: その他A（60 曲）/);
  assert.match(html, /まとめて YouTube で開く/);
  assert.match(html, /提案先: 曲X（1 曲）/);
  assert.match(html, /決定の保存先（D1）が使えないため/);   // 保存先がなければボタンは出さない
  assert.doesNotMatch(html, /class="act add"/);
  assert.match(html, /href="\/suggestions\/0\?p=0"/);
  assert.match(html, /href="\/suggestions\/0\?p=1"/);            // 60曲 → 50曲ずつ2つ
  assert.match(html, /list=PLother/);
  assert.match(html, /今入っている: その他の曲/);
  const menu = await (await w.fetch(new Request("https://mix.example/"), ENV, CTX)).text();
  assert.match(menu, /href="\/suggestions"/);
});

test("提案を YouTube の一時リストで開く（50曲ずつ）", async () => {
  const calls = setupWorld({ location: "https://www.youtube.com/watch?v=s50&list=TLGGsug" });
  const w = createHandler(INDEX_DATA, SUG);
  const res = await w.fetch(new Request("https://mix.example/suggestions/0?p=1"), ENV, CTX);
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("location"), "https://www.youtube.com/watch?v=s50&list=TLGGsug");
  const wv = calls.find((c) => c.url.includes("watch_videos"));
  assert.equal(new URL(wv.url).searchParams.get("video_ids").split(",").length, 10);   // 51〜60曲目
  const music = await w.fetch(new Request("https://mix.example/suggestions/1?to=music"), ENV, CTX);
  assert.match(music.headers.get("location"), /^https:\/\/music\.youtube\.com\/watch/);
  const intent = await w.fetch(new Request("https://mix.example/suggestions/1?app=intent"), ENV, CTX);
  assert.match(intent.headers.get("location"), /package=com\.google\.android\.youtube;end$/);
  assert.equal((await w.fetch(new Request("https://mix.example/suggestions/9"), ENV, CTX)).status, 404);
});

// ---- 追加・却下の決定（Durable Object の代わりに Map を使う） ----
import { DecisionLog, memoryRepo, stateLabel } from "../src/decisions.js";

function fakeNamespace() {
  const repo = memoryRepo();
  let t = 0;
  const log = new DecisionLog(repo, () => `2026-11-25T00:00:${String(t++).padStart(2, "0")}Z`);
  return { map: repo.map, idFromName: (n) => n, get: () => log };
}

const VID = (n) => `vid${String(n).padStart(8, "0")}`;     // 11文字の動画ID
const SUG2 = {
  version: 1, generated_at: "2026-11-25T04:00:00+09:00",
  groups: [{ name: "その他A", id: "PLother", items: [1, 2, 3].map((n) => ({
    id: VID(n), title: `曲${n}`, channel: "B", reason: "名前（aliases）だけの一致", in: ["その他の曲"] })) }],
};

function post(path, body, headers = {}) {
  return new Request(`https://mix.example${path}`, {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", Origin: "https://mix.example", ...headers },
  });
}

test("提案ページに埋め込み再生と追加・却下のボタンがある", async () => {
  setupWorld();
  const env = { ...ENV, DECISIONS: fakeNamespace() };
  const html = await (await createHandler(INDEX_DATA, SUG2).fetch(new Request("https://mix.example/suggestions"), env, CTX)).text();
  assert.match(html, /class="play" data-v="vid00000001"/);
  assert.match(html, /i\.ytimg\.com\/vi\/vid00000001\/mqdefault\.jpg/);
  assert.match(html, /youtube-nocookie\.com\/embed\//);         // ▶ で埋め込みの動画に差し替える
  assert.match(html, /「その他A」に追加<\/button>/);
  assert.match(html, /却下<\/button>/);
});

test("追加・却下を送ると保存され、ページと一時リストに反映される", async () => {
  setupWorld();
  const ns = fakeNamespace();
  const env = { ...ENV, DECISIONS: ns };
  const w = createHandler(INDEX_DATA, SUG2);
  let res = await w.fetch(post("/api/decide", { target: "PLother", video: VID(1), action: "add" }), env, CTX);
  assert.equal(res.status, 200);
  const r = await res.json();
  assert.equal(r.record.action, "add");
  assert.equal(r.record.by, "me@gmail.com");
  assert.equal(r.label, "追加予定（次回の同期で反映）");
  res = await w.fetch(post("/api/decide", { target: "PLother", video: VID(2), action: "reject" }), env, CTX);
  assert.equal((await res.json()).label, "却下");

  const html = await (await w.fetch(new Request("https://mix.example/suggestions"), env, CTX)).text();
  assert.match(html, /data-v="vid00000001" data-action="add"/);
  assert.match(html, /追加予定（次回の同期で反映）/);
  const j = await (await w.fetch(new Request("https://mix.example/suggestions/0?format=json"), env, CTX)).json();
  assert.deepEqual(j.videos, [VID(3)]);                          // まとめて開くのは、まだ決めていない曲だけ

  // 取り消し
  res = await w.fetch(post("/api/decide", { target: "PLother", video: VID(2), action: "none" }), env, CTX);
  assert.equal((await res.json()).record.action, "none");
});

test("決定の送信は同じ画面からの、今の提案にある曲だけ", async () => {
  setupWorld();
  const env = { ...ENV, DECISIONS: fakeNamespace() };
  const w = createHandler(INDEX_DATA, SUG2);
  const ok = { target: "PLother", video: VID(1), action: "add" };
  assert.equal((await w.fetch(post("/api/decide", ok, { Origin: "https://evil.example" }), env, CTX)).status, 403);
  assert.equal((await w.fetch(post("/api/decide", ok, { "Content-Type": "text/plain" }), env, CTX)).status, 415);
  assert.equal((await w.fetch(post("/api/decide", { ...ok, video: VID(9) }), env, CTX)).status, 404);
  assert.equal((await w.fetch(post("/api/decide", { ...ok, target: "PLelse" }), env, CTX)).status, 404);
  assert.equal((await w.fetch(post("/api/decide", { ...ok, action: "delete" }), env, CTX)).status, 400);
  assert.equal((await w.fetch(post("/api/decide", ok), env, {})).status, 403);          // Access なし
  assert.equal((await w.fetch(post("/api/decide", ok), { ...env, DECISIONS: undefined }, CTX)).status, 503);
});

function jwtWith(payload) {
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "RS256" })}.${b64(payload)}.sig`;
}

test("同期用 API：サービストークンの Client ID で確かめ、決定を返し、反映結果を記録する", async () => {
  setupWorld();
  const ns = fakeNamespace();
  const env = { ...ENV, DECISIONS: ns, SYNC_CLIENT_ID: "abc.access" };
  const w = createHandler(INDEX_DATA, SUG2);
  await w.fetch(post("/api/decide", { target: "PLother", video: VID(1), action: "add" }), env, CTX);
  // Access が通したサービストークンのリクエスト（メールアドレスなし、common_name が Client ID）
  const svc = { access: { getIdentity: async () => ({}) } };
  const get = (cn) => new Request("https://mix.example/api/sync/decisions", {
    headers: cn ? { "Cf-Access-Jwt-Assertion": jwtWith({ common_name: cn, type: "app" }) } : {},
  });
  assert.equal((await w.fetch(get(null), env, svc)).status, 403);                          // トークンの情報なし
  assert.equal((await w.fetch(get("other.access"), env, svc)).status, 403);                 // 別のトークン
  assert.equal((await w.fetch(get("abc.access"), env, {})).status, 403);                    // Access を通っていない
  assert.equal((await w.fetch(get("abc.access"), { ...env, SYNC_CLIENT_ID: "" }, svc)).status, 403);
  // ログインした人（メールアドレスあり）は同期用 API を使えない
  assert.equal((await w.fetch(get(null), env, CTX)).status, 403);
  // getIdentity() から読めても通す
  const svc2 = { access: { getIdentity: async () => ({ common_name: "abc.access" }) } };
  assert.equal((await w.fetch(get(null), env, svc2)).status, 200);
  // サービストークンでは、画面や /api/decide は使えない（トークンが漏れても決定は送れない）
  assert.equal((await w.fetch(new Request("https://mix.example/suggestions"), env, svc2)).status, 403);
  assert.equal((await w.fetch(post("/api/decide", { target: "PLother", video: VID(2), action: "add" }), env, svc2)).status, 403);
  const { decisions } = await (await w.fetch(get("abc.access"), env, svc)).json();
  assert.equal(decisions.length, 1);
  const d = decisions[0];
  assert.equal(d.target_name, "その他A");

  const ack = (results) => new Request("https://mix.example/api/sync/ack", {
    method: "POST",
    headers: { "Cf-Access-Jwt-Assertion": jwtWith({ common_name: "abc.access" }), "Content-Type": "application/json" },
    body: JSON.stringify({ results }),
  });
  // 古い decided_at の結果は無視する
  let r = await (await w.fetch(ack([{ ...d, decided_at: "old", result: "added" }]), env, svc)).json();
  assert.equal(r.acknowledged, 0);
  r = await (await w.fetch(ack([{ target: d.target, video: d.video, decided_at: d.decided_at, result: "added" }]), env, svc)).json();
  assert.equal(r.acknowledged, 1);
  const html = await (await w.fetch(new Request("https://mix.example/suggestions"), env, CTX)).text();
  assert.match(html, /追加済み/);
  // 反映済みの追加は取り消せない（このシステムは曲を消さない）
  const res = await w.fetch(post("/api/decide", { target: "PLother", video: VID(1), action: "none" }), env, CTX);
  assert.equal(res.status, 409);
});

test("stateLabel", () => {
  assert.equal(stateLabel(null), "");
  assert.equal(stateLabel({ action: "add", applied_at: "x", result: "failed", error: "videoNotFound" }),
    "追加できませんでした（videoNotFound）");
});

test("「その他の曲に追加」のボタンと決定", async () => {
  setupWorld();
  const env = { ...ENV, DECISIONS: fakeNamespace() };
  const sug = { ...SUG2, misc: { id: "PLmiscX", name: "その他の曲" },
    groups: [{ ...SUG2.groups[0], items: [
      { ...SUG2.groups[0].items[0], in: ["夜の曲"] },
      { ...SUG2.groups[0].items[1], in: ["その他の曲"] } ] }] };   // すでに「その他の曲」にある曲にはボタンを出さない
  const w = createHandler(INDEX_DATA, sug);
  const html = await (await w.fetch(new Request("https://mix.example/suggestions"), env, CTX)).text();
  assert.equal((html.match(/class="act misc" data-a="misc">「その他の曲」に追加/g) || []).length, 1);
  const res = await w.fetch(post("/api/decide", { target: "PLother", video: VID(1), action: "misc" }), env, CTX);
  assert.equal(res.status, 200);
  const r = await res.json();
  assert.equal(r.record.action, "misc");
  assert.equal(r.label, "「その他の曲」に追加予定（次回の同期で反映）");
  // その他の曲の再生リストがないときは受け付けない
  const w2 = createHandler(INDEX_DATA, SUG2);
  assert.equal((await w2.fetch(post("/api/decide", { target: "PLother", video: VID(2), action: "misc" }), env, CTX)).status, 400);
  assert.doesNotMatch(await (await w2.fetch(new Request("https://mix.example/suggestions"), env, CTX)).text(), /class="act misc"/);
});

test("stateLabel：その他の曲に追加", () => {
  assert.equal(stateLabel({ action: "misc", applied_at: "x", result: "added" }), "「その他の曲」に追加済み");
});

test("手で追加してほしい曲：メニューのお知らせ・手順・YouTube アプリで開く", async () => {
  setupWorld();
  const ids = Array.from({ length: 51 }, (_, i) => VID(100 + i));
  const sug = { ...SUG2, hand: [{ name: "未仕分け", id: "PLunsortedX", items: ids.map((id) => ({ id, title: `曲${id}`, channel: "B" })) }] };
  const w = createHandler(INDEX_DATA, sug);
  const menuHtml = await (await w.fetch(new Request("https://mix.example/"), ENV, CTX)).text();
  assert.match(menuHtml, /手で追加してほしい曲が 51 曲あります/);
  const html = await (await w.fetch(new Request("https://mix.example/hand"), ENV, CTX)).text();
  assert.match(html, /YouTube Music ではなく YouTube です/);
  assert.match(html, /1〜50曲目/);
  assert.match(html, /51〜51曲目/);
  assert.doesNotMatch(html, /to=music/);                                // YouTube Music では開かない
  const j = await (await w.fetch(new Request("https://mix.example/hand/0?p=1&format=json"), ENV, CTX)).json();
  assert.deepEqual(j.videos, [VID(150)]);
  assert.match(j.open_url, /^https:\/\/www\.youtube\.com\//);
  const a = await (await w.fetch(new Request("https://mix.example/hand/0?app=intent&format=json"), ENV, CTX)).json();
  assert.match(a.open_url, /^intent:\/\/.*package=com\.google\.android\.youtube/);
  assert.equal((await w.fetch(new Request("https://mix.example/hand/5"), ENV, CTX)).status, 404);
  // ないときはお知らせを出さない
  const plain = await (await createHandler(INDEX_DATA, SUG2).fetch(new Request("https://mix.example/"), ENV, CTX)).text();
  assert.doesNotMatch(plain, /class="notice"/);
});

test("要確認：メニューのお知らせと一覧（チャンネル・再生リスト）", async () => {
  setupWorld();
  const review = { version: 1, generated_at: "2026-11-27T04:17:00+09:00", items: [
    { kind: "channel", section: "ignore_channels", id: "UCxxxxxxxxxxxxxxxxxxxxxx", name: "新しい人", note: "2026-11-27 自動追加：「その他の曲」の「曲」" },
    { kind: "playlist", section: "ignore_playlists", id: "PLyyyy", name: "作業用 <BGM>", note: "2026-11-27 自動追加" }] };
  const w = createHandler(INDEX_DATA, SUG2, review);
  const menuHtml = await (await w.fetch(new Request("https://mix.example/"), ENV, CTX)).text();
  assert.match(menuHtml, /要確認の項目が 2 件あります/);
  const html = await (await w.fetch(new Request("https://mix.example/review"), ENV, CTX)).text();
  assert.match(html, /チャンネル（ignore_channels）（1 件）/);
  assert.match(html, /href="https:\/\/www\.youtube\.com\/channel\/UCxxxxxxxxxxxxxxxxxxxxxx"/);
  assert.match(html, /作業用 &lt;BGM&gt;/);                       // エスケープする
  assert.match(html, /「# 要確認…」のコメントを消します/);
  const none = await (await createHandler(INDEX_DATA, SUG2).fetch(new Request("https://mix.example/review"), ENV, CTX)).text();
  assert.match(none, /要確認の項目はありません/);
});
