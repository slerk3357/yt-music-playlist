// デプロイ前に実行（wrangler.toml の [build]）：非公開リポジトリの data/mix/ の索引と提案を
// Worker に組み込む。Cloudflare のビルドはリポジトリ全体を取得するので、GitHub のトークンは不要。
// ファイルがない（公開用リポジトリなど）ときは、空のデータを組み込む。
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

const dir = new URL("./src/generated/", import.meta.url);
mkdirSync(dir, { recursive: true });

function embed(name, out, empty) {
  const src = new URL(`../data/mix/${name}`, import.meta.url);
  let text;
  if (existsSync(src)) {
    text = readFileSync(src, "utf8");
    JSON.parse(text); // 壊れたファイルなら、ここでビルドを失敗させる
  } else {
    console.warn(`data/mix/${name} がありません。空のデータを組み込みます。`);
    text = JSON.stringify(empty);
  }
  writeFileSync(new URL(out, dir), text);
  return JSON.parse(text);
}

const index = embed("index.json", "index-data.json", { version: 1, generated_at: null, playlists: [], top_favorites: [] });
const sug = embed("suggestions.json", "suggestions-data.json", { version: 1, generated_at: null, groups: [] });
console.log(`索引を組み込みました: 再生リスト ${index.playlists.length} 件（${index.generated_at ?? "日時なし"}）`);
console.log(`提案を組み込みました: ${sug.groups.reduce((n, g) => n + g.items.length, 0)} 件`);
