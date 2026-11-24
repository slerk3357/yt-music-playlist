// オリジナルミックスリストの選曲と URL 組み立て（外部通信なし・テスト可能な部分）

export const MAX_VIDEOS = 50; // watch_videos に渡せる上限
export const YTM_PACKAGE = "com.google.android.apps.youtube.music";

/** 0 以上 1 未満の乱数（暗号論的乱数）。テストでは差し替える。 */
export function cryptoRandom() {
  const a = new Uint32Array(1);
  crypto.getRandomValues(a);
  return a[0] / 2 ** 32;
}

/** 配列から重複なく n 個をランダムに選ぶ（順番もランダム）。 */
export function sample(items, n, rand = cryptoRandom) {
  const a = [...items];
  const k = Math.min(n, a.length);
  for (let i = 0; i < k; i++) {
    const j = i + Math.floor(rand() * (a.length - i));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a.slice(0, k);
}

/** 1〜50 に丸めた曲数。 */
export function clampCount(value, fallback = MAX_VIDEOS) {
  const n = Number.parseInt(value ?? "", 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(1, Math.min(MAX_VIDEOS, n));
}

function unionVideos(playlists) {
  const set = new Set();
  for (const p of playlists) for (const v of p.videos || []) set.add(v);
  return [...set];
}

/**
 * ミックスの選曲。
 *   mode "random"    : 全再生リストの動画（重複は1つ）からランダム
 *   mode "playlists" : ランダムに k 個の再生リストを選び、その動画からランダム
 *   mode "top"       : 特にお気に入りのチャンネル name の ○○チャンネル＋その他○○＋特別再生リスト からランダム
 * 戻り値 { videos, playlists: [選んだ再生リスト名], title } または { error, status }
 */
export function pickMix(index, mode, params = {}, rand = cryptoRandom) {
  const n = clampCount(params.n);
  const all = (index.playlists || []).filter((p) => (p.videos || []).length > 0);
  if (mode === "random") {
    return { title: "完全ランダム", playlists: all.map((p) => p.name), videos: sample(unionVideos(all), n, rand) };
  }
  if (mode === "playlists") {
    const k = Math.max(1, Math.min(all.length || 1, Number.parseInt(params.k ?? "3", 10) || 3));
    const chosen = sample(all, k, rand);
    return { title: `ランダム再生リスト（${chosen.length}件）`, playlists: chosen.map((p) => p.name),
             videos: sample(unionVideos(chosen), n, rand) };
  }
  if (mode === "top") {
    const groups = index.top_favorites || [];
    const key = params.name ?? "";
    const g = groups.find((x) => x.name === key) ?? (/^\d+$/.test(key) ? groups[Number(key)] : undefined);
    if (!g) return { error: `特にお気に入りのチャンネル「${key}」が見つかりません`, status: 404 };
    const byId = new Map((index.playlists || []).map((p) => [p.id, p]));
    const pls = g.playlists.map((id) => byId.get(id)).filter(Boolean);
    return { title: `${g.name} ミックス`, playlists: pls.map((p) => p.name), videos: sample(unionVideos(pls), n, rand) };
  }
  return { error: `不明なミックスの種類です: ${mode}`, status: 404 };
}

export function watchVideosUrl(videoIds) {
  return `https://www.youtube.com/watch_videos?video_ids=${videoIds.slice(0, MAX_VIDEOS).join(",")}`;
}

/** watch_videos のリダイレクト先（Location）から v と list（TLGG…）を取り出す。 */
export function parseWatchRedirect(location) {
  if (!location) return null;
  let url;
  try {
    url = new URL(location, "https://www.youtube.com");
  } catch {
    return null;
  }
  const v = url.searchParams.get("v");
  const list = url.searchParams.get("list");
  return v && list ? { v, list } : null;
}

/** YouTube Music で開く URL。open="list" なら曲一覧の画面、既定はすぐ再生。 */
export function musicUrl({ v, list }, open = "watch") {
  return open === "list"
    ? `https://music.youtube.com/playlist?list=${encodeURIComponent(list)}`
    : `https://music.youtube.com/watch?v=${encodeURIComponent(v)}&list=${encodeURIComponent(list)}`;
}

/** Android でブラウザが開いてしまう場合の intent:// 形式。 */
export function intentUrl(target) {
  const rest = target.replace(/^https:\/\//, "");
  return `intent://${rest}#Intent;scheme=https;package=${YTM_PACKAGE};end`;
}

/** 文字列の比較（長さ以外の情報を漏らしにくい形）。 */
export function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
