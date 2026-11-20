/**
 * yt-music-playlist 用トークン発行Webアプリ
 *
 * GitHub Actions から秘密の文字列つきで POST されたときだけ、
 * このスクリプトの所有者の YouTube 用アクセストークン（約1時間有効）を返します。
 *
 * 初回だけ、エディタで setup() を実行してください（README 参照）。
 */

/** 初回セットアップ：秘密の文字列を作り、YouTube の権限を承認して接続先チャンネルを確認する。 */
function setup() {
  const props = PropertiesService.getScriptProperties();
  let secret = props.getProperty('TOKEN_SECRET');
  if (!secret) {
    secret = (Utilities.getUuid() + Utilities.getUuid()).replace(/-/g, '');
    props.setProperty('TOKEN_SECRET', secret);
  }
  const res = YouTube.Channels.list('snippet', { mine: true });
  const ch = (res.items || [])[0];
  Logger.log('接続先チャンネル: ' + (ch ? ch.snippet.title + ' (' + ch.id + ')' : '（チャンネルなし）'));
  Logger.log('YT_TOKEN_SECRET = ' + secret);
}

/** 秘密の文字列を作り直す（漏れた疑いがあるとき）。GitHub の Secrets も更新すること。 */
function rotateSecret() {
  PropertiesService.getScriptProperties().deleteProperty('TOKEN_SECRET');
  setup();
}

function doPost(e) {
  const secret = PropertiesService.getScriptProperties().getProperty('TOKEN_SECRET');
  let body = {};
  try {
    body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (err) {
    body = {};
  }
  if (!secret || typeof body.secret !== 'string' || !safeEqual_(body.secret, secret)) {
    return json_({ error: 'forbidden' });
  }
  return json_({ access_token: ScriptApp.getOAuthToken() });
}

function doGet() {
  return json_({ error: 'use POST' });
}

function safeEqual_(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
