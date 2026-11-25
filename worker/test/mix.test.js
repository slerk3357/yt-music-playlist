import assert from "node:assert/strict";
import test from "node:test";
import {
  clampCount, intentUrl, musicUrl, parseWatchRedirect, pickMix, safeEqual, sample, watchVideosUrl,
} from "../src/mix.js";
import { createHandler } from "../src/index.js";

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

test("合言葉を設定した場合は URL の先頭にも必要", async () => {
  setupWorld();
  const env = { ...ENV, ACCESS_KEY: "secret-key" };
  assert.equal((await worker.fetch(new Request("https://mix.example/wrong/random"), env, CTX)).status, 404);
  assert.equal((await worker.fetch(new Request("https://mix.example/random"), env, CTX)).status, 404);
  const ok = await worker.fetch(new Request("https://mix.example/secret-key/random"), env, CTX);
  assert.equal(ok.status, 302);
  const menu = await (await worker.fetch(new Request("https://mix.example/secret-key/"), env, CTX)).text();
  assert.match(menu, /href="\/secret-key\/random"/);
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


test("ビルド時の組み込みと入口（build.mjs → entry.js）", async () => {
  const { execFileSync } = await import("node:child_process");
  const { fileURLToPath } = await import("node:url");
  const dir = fileURLToPath(new URL("..", import.meta.url));
  const out = execFileSync(process.execPath, ["build.mjs"], { cwd: dir, encoding: "utf8" });
  assert.match(out, /索引を組み込みました/);
  const entry = (await import("../src/entry.js")).default;
  setupWorld();
  const res = await entry.fetch(new Request("https://mix.example/"), ENV, CTX);
  assert.equal(res.status, 200);
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
  assert.match(html, /決定の保存先（Durable Object）が設定されていない/);   // DECISIONS がなければボタンは出さない
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
import { DecisionLog, stateLabel } from "../src/decisions.js";

function fakeNamespace() {
  const map = new Map();
  const storage = {
    async get(k) { return structuredClone(map.get(k)); },
    async put(k, v) { map.set(k, structuredClone(v)); },
    async list({ prefix }) { return new Map([...map].filter(([k]) => k.startsWith(prefix)).sort()); },
  };
  let t = 0;
  const log = new DecisionLog(storage, () => `2026-11-25T00:00:${String(t++).padStart(2, "0")}Z`);
  return { map, idFromName: (n) => n, get: () => log };
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

test("同期用 API：鍵で確かめ、決定を返し、反映結果を記録する", async () => {
  setupWorld();
  const ns = fakeNamespace();
  const env = { ...ENV, DECISIONS: ns, SYNC_KEY: "sync-secret" };
  const w = createHandler(INDEX_DATA, SUG2);
  await w.fetch(post("/api/decide", { target: "PLother", video: VID(1), action: "add" }), env, CTX);
  // サービストークンで来たリクエスト（メールアドレスなし）。ALLOWED_EMAILS があっても鍵で通す
  const svc = { access: { getIdentity: async () => ({}) } };
  const get = (key) => new Request("https://mix.example/api/sync/decisions", { headers: key ? { "X-Sync-Key": key } : {} });
  assert.equal((await w.fetch(get(null), env, svc)).status, 403);
  assert.equal((await w.fetch(get("wrong"), env, svc)).status, 403);
  assert.equal((await w.fetch(get("sync-secret"), env, {})).status, 403);              // Access を通っていない
  assert.equal((await w.fetch(get("sync-secret"), { ...env, SYNC_KEY: "" }, svc)).status, 503);
  const { decisions } = await (await w.fetch(get("sync-secret"), env, svc)).json();
  assert.equal(decisions.length, 1);
  const d = decisions[0];
  assert.equal(d.target_name, "その他A");

  const ack = (results) => new Request("https://mix.example/api/sync/ack", {
    method: "POST", headers: { "X-Sync-Key": "sync-secret", "Content-Type": "application/json" },
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
