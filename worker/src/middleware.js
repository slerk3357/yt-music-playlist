// ミドルウェアの中身（middleware/ から使う。テストでは同じものを Hono に載せる）。
import { authorize, executionCtxOf, identityEmail, isServicePath } from "./access.js";
import { text } from "./app.js";

/**
 * Cloudflare Access で認証されたリクエストだけを通す。
 * 画面と /api/decide はログインした人（メールアドレスのある認証）だけ、/api/sync/… は同期用のサービストークンだけ。
 * settings(c) は { SYNC_CLIENT_ID, ALLOWED_EMAILS } を返す。Void の内部用のパス（/__void…）は対象外。
 */
export function accessMiddleware(settings) {
  return async (c, next) => {
    if (c.req.path.startsWith("/__void")) return next();
    const service = isServicePath(c.req.path);
    const ctx = executionCtxOf(c);
    const denied = await authorize(settings(c), ctx, service, c.req.raw);
    if (denied) return text(denied, 403);
    if (!service) c.set("email", await identityEmail(ctx));
    return next();
  };
}

/** ルートで使うデータ（c.get("data")）と、決定の保存先（c.get("decisions")）を用意する。 */
export function contextMiddleware(data, decisions) {
  return async (c, next) => {
    c.set("data", data);
    c.set("decisions", decisions());
    return next();
  };
}
