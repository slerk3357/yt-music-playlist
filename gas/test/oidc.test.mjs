// gas/Code.gs の OIDC 検証を Node で試す（Apps Script の組み込みオブジェクトは代用品）。
//   node --test gas/test/*.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const CODE = readFileSync(new URL("../Code.gs", import.meta.url), "utf8");
const REPO = "owner/private-repo";
const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const other = generateKeyPairSync("rsa", { modulusLength: 2048 });
const JWK = { ...publicKey.export({ format: "jwk" }), kid: "k1", alg: "RS256", use: "sig" };

function world({ props = {}, jwks = { keys: [JWK] } } = {}) {
  const cache = new Map();
  const fetched = [];
  const ctx = {
    console: { log() {}, warn() {} },
    Logger: { log() {} },
    Utilities: {
      DigestAlgorithm: { SHA_256: "sha256" },
      Charset: { UTF_8: "utf8" },
      computeDigest: (_a, s) => [...createHash("sha256").update(s, "utf8").digest()].map((b) => (b > 127 ? b - 256 : b)),
      base64Decode: (s) => [...Buffer.from(s, "base64")].map((b) => (b > 127 ? b - 256 : b)),
      newBlob: (bytes) => ({ getDataAsString: () => Buffer.from(bytes.map((b) => b & 255)).toString("utf8") }),
    },
    CacheService: { getScriptCache: () => ({
      get: (k) => cache.get(k) ?? null, put: (k, v) => cache.set(k, v) }) },
    UrlFetchApp: { fetch: (url) => { fetched.push(url); return { getResponseCode: () => 200, getContentText: () => JSON.stringify(jwks) }; } },
    PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => props[k] ?? null, deleteProperty() {} }) },
    ScriptApp: { getOAuthToken: () => "ya29.token" },
    ContentService: { MimeType: { JSON: "json" }, createTextOutput: (t) => ({ text: t, setMimeType() { return this; } }) },
  };
  vm.createContext(ctx);
  vm.runInContext(CODE, ctx);
  return { ctx, cache, fetched };
}

const NOW = 1_800_000_000;
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
function jwt(over = {}, { key = privateKey, kid = "k1", alg = "RS256" } = {}) {
  const claims = {
    iss: "https://token.actions.githubusercontent.com", aud: "yt-music-playlist",
    iat: NOW - 10, nbf: NOW - 10, exp: NOW + 300, jti: `j-${Math.random()}`,
    repository: REPO, repository_id: "123", ref: "refs/heads/main",
    workflow_ref: `${REPO}/.github/workflows/sync.yml@refs/heads/main`, runner_environment: "github-hosted",
    run_id: "1", ...over,
  };
  const input = `${b64({ alg, kid, typ: "JWT" })}.${b64(claims)}`;
  return `${input}.${sign("sha256", Buffer.from(input), key).toString("base64url")}`;
}
const CFG = { repository: REPO, repositoryId: "", audience: "yt-music-playlist" };
const verify = (w, token, cfg = CFG, now = NOW) => vm.runInContext("verifyGitHubOidc_", w.ctx)(token, cfg, now);

test("正しいトークンは通る", () => {
  const w = world();
  assert.equal(verify(w, jwt()).repository, REPO);
  assert.equal(verify(w, jwt({ workflow_ref: `${REPO}/.github/workflows/tools.yml@refs/heads/main` })).run_id, "1");
  assert.equal(verify(w, jwt({ aud: ["x", "yt-music-playlist"] })).repository, REPO);
  assert.equal(w.fetched.length, 1);                    // 公開鍵はキャッシュする
});

test("署名がおかしいトークンは拒む", () => {
  const w = world();
  assert.throws(() => verify(w, jwt({}, { key: other.privateKey })), /bad signature/);
  const t = jwt().split(".");
  const forged = `${t[0]}.${b64({ ...JSON.parse(Buffer.from(t[1], "base64url")), repository: "evil/repo" })}.${t[2]}`;
  assert.throws(() => verify(w, forged), /bad signature/);           // 中身の書き換え
  assert.throws(() => verify(w, jwt({}, { alg: "none" })), /unsupported alg/);
  assert.throws(() => verify(w, jwt({}, { kid: "zzz" })), /unknown key/);
  assert.throws(() => verify(w, "a.b"), /malformed/);
  assert.throws(() => verify(w, `${t[0]}.${t[1]}.`), /malformed/);
});

test("クレームが合わないトークンは拒む", () => {
  const w = world();
  const cases = [
    [{ iss: "https://evil.example" }, /issuer/],
    [{ aud: "other" }, /audience/],
    [{ exp: NOW - 120 }, /expired/],
    [{ nbf: NOW + 600 }, /not yet valid/],
    [{ repository: "owner/other-repo" }, /repository/],
    [{ ref: "refs/heads/feature" }, /ref/],
    [{ workflow_ref: `${REPO}/.github/workflows/evil.yml@refs/heads/main` }, /workflow/],
    [{ workflow_ref: `${REPO}/.github/workflows/sync.yml@refs/heads/feature` }, /workflow/],
    [{ runner_environment: "self-hosted" }, /runner/],
    [{ jti: "" }, /jti/],
  ];
  for (const [over, err] of cases) assert.throws(() => verify(w, jwt(over)), err, JSON.stringify(over));
  assert.throws(() => verify(w, jwt({ repository_id: "999" }), { ...CFG, repositoryId: "123" }), /repository id/);
  assert.throws(() => verify(w, jwt(), { ...CFG, repository: "" }), /not configured/);
});

test("同じトークンは2回使えない", () => {
  const w = world();
  const t = jwt();
  verify(w, t);
  assert.throws(() => verify(w, t), /replayed/);
});

test("doPost：通ればアクセストークン、だめなら forbidden", () => {
  const w = world({ props: { ALLOWED_REPOSITORY: REPO } });
  const post = (body) => JSON.parse(vm.runInContext("doPost", w.ctx)({ postData: { contents: JSON.stringify(body) } }).text);
  // doPost は今の時刻で確かめるので、今の時刻で作る
  const now = Math.floor(Date.now() / 1000);
  const fresh = jwt({ iat: now - 5, nbf: now - 5, exp: now + 300 });
  assert.deepEqual(post({ id_token: fresh }), { access_token: "ya29.token" });
  assert.match(post({ id_token: fresh }).error, /forbidden: replayed/);
  assert.match(post({ secret: "old-style" }).error, /forbidden: malformed/);   // 合言葉の方式はもう使えない
  const none = world();
  assert.match(JSON.parse(vm.runInContext("doPost", none.ctx)({ postData: { contents: JSON.stringify({ id_token: fresh }) } }).text).error,
    /not configured/);
});
