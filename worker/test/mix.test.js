import assert from "node:assert/strict";
import test from "node:test";
import {
  clampCount, intentUrl, musicUrl, parseWatchRedirect, pickMix, safeEqual, sample, watchVideosUrl,
} from "../src/mix.js";
import worker from "../src/index.js";

// 決まった順に値を返す乱数
function seq(...values) {
  let i = 0;
  return () => values[i++ % values.length];
}

const INDEX = {
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
  const m = pickMix(INDEX, "random", { n: "50" });
  assert.deepEqual(new Set(m.videos), new Set(["a1", "a2", "x", "o1", "s1", "c1", "c2"]));
  assert.equal(m.videos.length, 7);
});

test("ランダム再生リストは選んだ k 個の再生リストの動画だけ", () => {
  const m = pickMix(INDEX, "playlists", { k: "1", n: "50" }, seq(0.99));
  assert.equal(m.playlists.length, 1);
  const chosen = INDEX.playlists.find((p) => p.name === m.playlists[0]);
  assert.deepEqual(new Set(m.videos), new Set(chosen.videos));
  assert.ok(!m.playlists.includes("空"));            // 空の再生リストは選ばない
});

test("特にお気に入りのミックスは ○○チャンネル＋その他＋特別 の和集合", () => {
  const m = pickMix(INDEX, "top", { name: "A" });
  assert.deepEqual(new Set(m.videos), new Set(["a1", "a2", "x", "o1", "s1"]));
  assert.deepEqual(pickMix(INDEX, "top", { name: "0" }).title, "A ミックス");   // 番号でも指定できる
  assert.equal(pickMix(INDEX, "top", { name: "Z" }).status, 404);
  assert.equal(pickMix(INDEX, "unknown").status, 404);
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

// ---- Worker 全体（fetch と caches を差し替えて確認） ----
function setupWorld({ location = "https://www.youtube.com/watch?v=a1&list=TLGGxyz" } = {}) {
  const calls = [];
  globalThis.caches = { default: { match: async () => undefined, put: async () => {} } };
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    if (String(url).startsWith("https://api.github.com/")) {
      assert.equal(init.headers.Authorization, "Bearer gh-token");
      return new Response(JSON.stringify(INDEX), { status: 200 });
    }
    if (String(url).startsWith("https://www.youtube.com/watch_videos")) {
      assert.equal(init.redirect, "manual");
      return new Response(null, { status: 303, headers: location ? { location } : {} });
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  return calls;
}

const ENV = { ACCESS_KEY: "secret-key", GITHUB_TOKEN: "gh-token", GITHUB_REPO: "me/private", CACHE_SECONDS: "0" };

test("アクセスキーが違えば 404", async () => {
  setupWorld();
  const res = await worker.fetch(new Request("https://mix.example/wrong/random"), ENV, {});
  assert.equal(res.status, 404);
});

test("ミックスは YouTube Music の一時リストへ 302", async () => {
  setupWorld();
  const res = await worker.fetch(new Request("https://mix.example/secret-key/top/A?n=5"), ENV, {});
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("location"), "https://music.youtube.com/watch?v=a1&list=TLGGxyz");
  const intent = await worker.fetch(new Request("https://mix.example/secret-key/random?app=intent"), ENV, {});
  assert.match(intent.headers.get("location"), /^intent:\/\/music\.youtube\.com\/watch\?v=a1&list=TLGGxyz#Intent;/);
});

test("format=json は選曲結果を返す", async () => {
  setupWorld();
  const res = await worker.fetch(new Request("https://mix.example/secret-key/playlists?k=2&n=3&format=json"), ENV, {});
  const body = await res.json();
  assert.equal(body.playlists.length, 2);
  assert.ok(body.videos.length <= 3);
  assert.equal(body.temporary_list, "TLGGxyz");
});

test("一時リストを作れなければ watch_videos の URL へ", async () => {
  setupWorld({ location: "https://consent.youtube.com/m" });
  const res = await worker.fetch(new Request("https://mix.example/secret-key/random?n=2"), ENV, {});
  assert.match(res.headers.get("location"), /^https:\/\/www\.youtube\.com\/watch_videos\?video_ids=/);
});

test("メニューに各ミックスへのリンクがある", async () => {
  setupWorld();
  const res = await worker.fetch(new Request("https://mix.example/secret-key/"), ENV, {});
  const html = await res.text();
  assert.match(html, /href="\/secret-key\/random"/);
  assert.match(html, /href="\/secret-key\/top\/A"/);
});
