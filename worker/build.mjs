// デプロイ前に実行（wrangler.toml の [build]）：非公開リポジトリの data/mix/index.json を
// Worker に組み込む。Cloudflare のビルドはリポジトリ全体を取得するので、GitHub のトークンは不要。
// 索引がない（公開用リポジトリなど）ときは、空の索引を組み込む。
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

const src = new URL("../data/mix/index.json", import.meta.url);
const dir = new URL("./src/generated/", import.meta.url);
const dst = new URL("./index-data.json", dir);

let text;
if (existsSync(src)) {
  text = readFileSync(src, "utf8");
  JSON.parse(text); // 壊れた索引なら、ここでビルドを失敗させる
} else {
  console.warn("data/mix/index.json がありません。空の索引を組み込みます。");
  text = JSON.stringify({ version: 1, generated_at: null, playlists: [], top_favorites: [] });
}
mkdirSync(dir, { recursive: true });
writeFileSync(dst, text);
const index = JSON.parse(text);
console.log(`索引を組み込みました: 再生リスト ${index.playlists.length} 件（${index.generated_at ?? "日時なし"}）`);
