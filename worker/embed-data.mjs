// ビルドの前に（vite.config.ts を読み込むときに）、非公開リポジトリの data/mix/ の索引・提案・要確認を
// src/generated/ に書き出して Worker に組み込む。ファイルがない（公開用リポジトリなど）ときは、空のデータにする。
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';

const FILES = [
  ['index.json', 'index-data.json', { version: 1, generated_at: null, playlists: [], top_favorites: [] }],
  ['suggestions.json', 'suggestions-data.json', { version: 1, generated_at: null, groups: [], hand: [], misc: null }],
  ['review.json', 'review-data.json', { version: 1, generated_at: null, items: [] }],
];

export function embedData({ quiet = false } = {}) {
  const dir = new URL('./src/generated/', import.meta.url);
  mkdirSync(dir, { recursive: true });
  const out = {};
  for (const [name, file, empty] of FILES) {
    const src = new URL(`../data/mix/${name}`, import.meta.url);
    let text = JSON.stringify(empty);
    if (existsSync(src)) {
      text = readFileSync(src, 'utf8');
      JSON.parse(text); // 壊れたファイルなら、ここでビルドを止める
    } else if (!quiet) {
      console.warn(`data/mix/${name} がありません。空のデータを組み込みます。`);
    }
    writeFileSync(new URL(file, dir), text);
    out[name] = JSON.parse(text);
  }
  return out;
}
