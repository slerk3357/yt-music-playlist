// Cloudflare Access による認証の確認（Hono に依存しない部分。middleware/01.access.js から使う）。
//
// Worker 単位で Access を有効にすると、Workers の実行コンテキスト（ctx）に ctx.access が付く
// （Void はこの ctx をそのまま Hono の c.executionCtx に渡す）。
import { safeEqual } from "./mix.js";

/** Hono の c.executionCtx を安全に読む（ない場合 Hono は例外を投げる）。 */
export function executionCtxOf(c) {
  try {
    return c.executionCtx;
  } catch {
    return undefined;
  }
}

/** 同期用のパス（サービストークンで呼ぶ）か。 */
export const isServicePath = (path) => path === "/api/sync" || path.startsWith("/api/sync/");

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

export async function identityEmail(ctx) {
  try {
    return String((await ctx?.access?.getIdentity())?.email || "").toLowerCase();
  } catch {
    return "";
  }
}

