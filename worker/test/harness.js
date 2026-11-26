// テスト用：routes/ のファイルを Void と同じ規則で URL に載せた Hono アプリを作る。
//   routes/index.js → /、routes/top/[name].js → /top/:name、routes/suggestions/index.js → /suggestions
// 本番の middleware/ の代わりに、src/middleware.js の同じ処理を、テスト用の値（env）で載せる。
//   env.ALLOWED_EMAILS / env.SYNC_CLIENT_ID : Access の確認に使う値
//   env.DECISIONS                           : 決定の保存先（{ idFromName, get } を持つ代用品。なければ null）
import { readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { Hono } from "hono";
import { buildData } from "../src/app.js";
import { accessMiddleware } from "../src/middleware.js";

const ROUTES = fileURLToPath(new URL("../routes/", import.meta.url));
const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];

function files(dir) {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

/** routes/ のファイルの一覧 [{ path, module }]（URL の形に直したもの）。 */
export const ROUTE_TABLE = await Promise.all(files(ROUTES).filter((f) => /\.(m?js|ts)$/.test(f)).sort().map(async (f) => {
  const rel = relative(ROUTES, f).replace(/\\/g, "/").replace(/\.(m?js|ts)$/, "");
  const path = "/" + rel.split("/").filter((s) => s !== "index").map((s) => s.replace(/^\[(.+)\]$/, ":$1")).join("/");
  return { path: path === "/" ? "/" : path.replace(/\/$/, ""), module: await import(pathToFileURL(f).href) };
}));

export function createHandler(index, suggestions, review) {
  const data = buildData(index, suggestions, review);
  const app = new Hono();
  app.use(accessMiddleware((c) => ({ SYNC_CLIENT_ID: c.env?.SYNC_CLIENT_ID, ALLOWED_EMAILS: c.env?.ALLOWED_EMAILS })));
  app.use(async (c, next) => {
    const ns = c.env?.DECISIONS;
    c.set("data", data);
    c.set("decisions", ns ? ns.get(ns.idFromName("main")) : null);
    await next();
  });
  // 静的なパスを先に載せる（Void と同じく、静的な部分がパラメータより優先）
  const table = [...ROUTE_TABLE].sort((a, b) => a.path.split(":").length - b.path.split(":").length);
  for (const { path, module } of table) {
    for (const m of METHODS) if (module[m]) app.on(m, path, module[m]);
  }
  return { fetch: (request, env, ctx) => app.fetch(request, env, ctx) };
}
