/**
 * yt-music-playlist 用トークン発行Webアプリ
 *
 * GitHub Actions が実行のたびに GitHub から受け取る OIDC トークン（短命・その実行専用）を送ってきたときだけ、
 * このスクリプトの所有者の YouTube 用アクセストークン（約1時間有効）を返します。
 * 合言葉のような保存しておく秘密は使いません。次をすべて確かめます。
 *   - GitHub の公開鍵による署名（RS256）
 *   - 発行元が GitHub Actions、宛先（aud）がこのWebアプリ用の値
 *   - 期限内で、同じトークンが前に使われていない
 *   - スクリプト プロパティ ALLOWED_REPOSITORY のリポジトリの、main ブランチの、許可したワークフロー
 *   - GitHub が用意したランナー（self-hosted ではない）
 *
 * 初回だけ、スクリプト プロパティを設定してから、エディタで setup() を実行してください（README 参照）。
 */

const OIDC_ISSUER = 'https://token.actions.githubusercontent.com';
const OIDC_JWKS_URL = OIDC_ISSUER + '/.well-known/jwks';
const DEFAULT_AUDIENCE = 'yt-music-playlist';
const ALLOWED_REF = 'refs/heads/main';
const ALLOWED_WORKFLOWS = ['.github/workflows/sync.yml', '.github/workflows/tools.yml'];
const CLOCK_SKEW_SECONDS = 60;

/** 初回セットアップ：設定を確かめ、YouTube の権限を承認して接続先チャンネルを確認する。 */
function setup() {
  const cfg = settings_();
  if (!cfg.repository) {
    throw new Error('スクリプト プロパティ ALLOWED_REPOSITORY に、非公開リポジトリを「持ち主/名前」で設定してください');
  }
  const res = YouTube.Channels.list('snippet', { mine: true });
  const ch = (res.items || [])[0];
  Logger.log('接続先チャンネル: ' + (ch ? ch.snippet.title + ' (' + ch.id + ')' : '（チャンネルなし）'));
  Logger.log('許可するリポジトリ: ' + cfg.repository + (cfg.repositoryId ? '（ID ' + cfg.repositoryId + '）' : ''));
  Logger.log('許可するワークフロー: ' + ALLOWED_WORKFLOWS.join(', ') + '（' + ALLOWED_REF + '）');
  // 以前の方式（合言葉）の名残があれば消す
  PropertiesService.getScriptProperties().deleteProperty('TOKEN_SECRET');
}

function settings_() {
  const p = PropertiesService.getScriptProperties();
  return {
    repository: String(p.getProperty('ALLOWED_REPOSITORY') || '').trim(),
    repositoryId: String(p.getProperty('ALLOWED_REPOSITORY_ID') || '').trim(),
    audience: String(p.getProperty('OIDC_AUDIENCE') || DEFAULT_AUDIENCE).trim(),
  };
}

function doPost(e) {
  let body = {};
  try {
    body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (err) {
    body = {};
  }
  try {
    const claims = verifyGitHubOidc_(String(body.id_token || ''), settings_(), Math.floor(Date.now() / 1000));
    console.log('トークンを発行: ' + claims.workflow_ref + ' run ' + claims.run_id);
    return json_({ access_token: ScriptApp.getOAuthToken() });
  } catch (err) {
    console.warn('拒否: ' + err.message);
    return json_({ error: 'forbidden: ' + err.message });
  }
}

function doGet() {
  return json_({ error: 'use POST' });
}

/**
 * GitHub Actions の OIDC トークンを検証し、クレームを返す。だめなら Error を投げる。
 * cfg: {repository, repositoryId, audience}、now: 現在の Unix 時刻（秒）
 */
function verifyGitHubOidc_(jwt, cfg, now) {
  if (!cfg.repository) throw new Error('not configured');
  const parts = jwt.split('.');
  if (parts.length !== 3 || !parts.every(function (p) { return /^[A-Za-z0-9_-]+$/.test(p); })) {
    throw new Error('malformed token');
  }
  const header = JSON.parse(b64urlText_(parts[0]));
  const claims = JSON.parse(b64urlText_(parts[1]));
  if (header.alg !== 'RS256' || typeof header.kid !== 'string') throw new Error('unsupported alg');
  const key = githubKey_(header.kid);
  if (!rs256Verify_(parts[0] + '.' + parts[1], b64urlBytes_(parts[2]), key.n, key.e)) {
    throw new Error('bad signature');
  }

  const audOk = Array.isArray(claims.aud) ? claims.aud.indexOf(cfg.audience) >= 0 : claims.aud === cfg.audience;
  if (claims.iss !== OIDC_ISSUER) throw new Error('issuer');
  if (!audOk) throw new Error('audience');
  if (typeof claims.exp !== 'number' || claims.exp + CLOCK_SKEW_SECONDS < now) throw new Error('expired');
  if (typeof claims.nbf === 'number' && claims.nbf - CLOCK_SKEW_SECONDS > now) throw new Error('not yet valid');
  if (typeof claims.iat === 'number' && claims.iat - CLOCK_SKEW_SECONDS > now) throw new Error('issued in future');

  if (claims.repository !== cfg.repository) throw new Error('repository');
  if (cfg.repositoryId && String(claims.repository_id) !== cfg.repositoryId) throw new Error('repository id');
  if (claims.ref !== ALLOWED_REF) throw new Error('ref');
  const allowed = ALLOWED_WORKFLOWS.map(function (w) { return cfg.repository + '/' + w + '@' + ALLOWED_REF; });
  if (allowed.indexOf(claims.workflow_ref) < 0) throw new Error('workflow');
  if (claims.runner_environment !== 'github-hosted') throw new Error('runner');

  // 同じトークンの使い回しを拒む（期限まで覚えておく）
  if (typeof claims.jti !== 'string' || !claims.jti) throw new Error('jti');
  const cache = CacheService.getScriptCache();
  const seenKey = 'oidc-jti:' + claims.jti;
  if (cache.get(seenKey)) throw new Error('replayed');
  cache.put(seenKey, '1', Math.min(21600, Math.max(60, claims.exp - now + CLOCK_SKEW_SECONDS)));
  return claims;
}

/** GitHub の公開鍵（kid で選ぶ）。1時間キャッシュし、見つからなければ取り直す。 */
function githubKey_(kid) {
  const cache = CacheService.getScriptCache();
  for (let attempt = 0; attempt < 2; attempt++) {
    let text = attempt === 0 ? cache.get('oidc-jwks') : null;
    if (!text) {
      const res = UrlFetchApp.fetch(OIDC_JWKS_URL, { muteHttpExceptions: true });
      if (res.getResponseCode() !== 200) throw new Error('jwks fetch failed');
      text = res.getContentText();
      cache.put('oidc-jwks', text, 3600);
    }
    const keys = (JSON.parse(text).keys || []).filter(function (k) { return k.kid === kid && k.kty === 'RSA'; });
    if (keys.length) return keys[0];
  }
  throw new Error('unknown key');
}

// ---- RS256（RSASSA-PKCS1-v1_5 + SHA-256）の検証 ------------------------------------------
// Apps Script には RSA の検証関数がないため、BigInt で計算する。

const SHA256_DIGEST_INFO_ = [0x30, 0x31, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02,
  0x01, 0x05, 0x00, 0x04, 0x20];

function rs256Verify_(signingInput, sigBytes, nB64, eB64) {
  const nBytes = stripZeros_(b64urlBytes_(nB64));
  const k = nBytes.length;
  if (k < 256) return false;                       // 2048 ビット未満の鍵は受け付けない
  if (sigBytes.length !== k) return false;
  const n = bytesToBigInt_(nBytes);
  const e = bytesToBigInt_(b64urlBytes_(eB64));
  const s = bytesToBigInt_(sigBytes);
  if (s >= n) return false;
  const em = bigIntToBytes_(modPow_(s, e, n), k);
  const hash = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, signingInput, Utilities.Charset.UTF_8);
  const t = SHA256_DIGEST_INFO_.concat(Array.prototype.map.call(hash, function (b) { return b & 255; }));
  const expected = [0x00, 0x01];
  for (let i = 0; i < k - t.length - 3; i++) expected.push(0xff);
  expected.push(0x00);
  const want = expected.concat(t);
  if (want.length !== k) return false;
  let diff = 0;
  for (let i = 0; i < k; i++) diff |= want[i] ^ em[i];
  return diff === 0;
}

function modPow_(base, exp, mod) {
  let result = BigInt(1);
  let b = base % mod;
  let x = exp;
  const zero = BigInt(0);
  const one = BigInt(1);
  while (x > zero) {
    if ((x & one) === one) result = (result * b) % mod;
    x >>= one;
    b = (b * b) % mod;
  }
  return result;
}

function bytesToBigInt_(bytes) {
  let x = BigInt(0);
  for (let i = 0; i < bytes.length; i++) x = (x << BigInt(8)) | BigInt(bytes[i] & 255);
  return x;
}

function bigIntToBytes_(x, len) {
  const out = new Array(len).fill(0);
  for (let i = len - 1; i >= 0 && x > BigInt(0); i--) {
    out[i] = Number(x & BigInt(255));
    x >>= BigInt(8);
  }
  return out;
}

function stripZeros_(bytes) {
  let i = 0;
  while (i < bytes.length - 1 && bytes[i] === 0) i++;
  return bytes.slice(i);
}

function b64urlBytes_(s) {
  let t = String(s).replace(/-/g, '+').replace(/_/g, '/');
  while (t.length % 4) t += '=';
  return Array.prototype.map.call(Utilities.base64Decode(t), function (b) { return b & 255; });
}

function b64urlText_(s) {
  const bytes = b64urlBytes_(s).map(function (b) { return b > 127 ? b - 256 : b; });
  return Utilities.newBlob(bytes).getDataAsString('UTF-8');
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
