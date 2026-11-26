// 画面（HTML）を作る関数。ルート（routes/）から使う。
import { ADD_ACTIONS, isFixed, stateLabel } from "./decisions.js";
import { MAX_VIDEOS, escapeHtml } from "./mix.js";

export const EMPTY_SUGGESTIONS = { version: 1, generated_at: null, groups: [], hand: [], misc: null };
export const EMPTY_REVIEW = { version: 1, generated_at: null, items: [] };

export const STYLE = `<style>body{font-family:system-ui,sans-serif;margin:24px;line-height:1.6}a{display:inline-block;padding:10px 0;font-size:1.1em}
li{list-style:none}ul{padding:0}small{color:#666}h2{margin-top:1.5em;font-size:1em;color:#444}
.btn{display:inline-block;margin:4px 8px 4px 0;padding:8px 12px;border:1px solid #888;border-radius:8px;text-decoration:none;font-size:1em}
.item{padding:6px 0;border-bottom:1px solid #eee}.item a{padding:0;font-size:1em}
.group{margin-top:1.5em;padding-top:.5em;border-top:3px solid #ccc}h2{font-size:1.15em;color:#222}
.item{display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap}.item[hidden]{display:none}.item.decided{opacity:.6}
.meta{flex:1 1 260px;min-width:0}.play{position:relative;padding:0;border:0;background:#000;cursor:pointer;width:160px;aspect-ratio:16/9}
.play img{width:100%;height:100%;object-fit:cover;display:block}.play span{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#fff;font-size:2em;text-shadow:0 0 6px #000}
.item iframe{width:min(100%,480px);aspect-ratio:16/9;border:0;flex:1 1 320px}
.actions{margin-top:6px}.actions button{margin:4px 6px 4px 0;padding:8px 14px;border-radius:8px;border:1px solid #888;background:#fff;font-size:1em}
.notice{background:#fff4d6;border:1px solid #e0b84c;border-radius:8px;padding:8px 10px}.actions .add{background:#e7f3e7}.actions .misc{background:#eef0fb}.actions .reject{background:#f6e6e6}.actions button:disabled{opacity:.45}</style>`;

/** 提案先の再生リストID -> {group, n, items: Map(動画ID -> 提案)} */
export function suggestionIndex(sug) {
  const m = new Map();
  (sug.groups || []).forEach((g, n) => {
    if (g.id) m.set(g.id, { group: g, n, items: new Map(g.items.map((it) => [it.id, it])) });
  });
  return m;
}

export function decisionMap(records) {
  const m = new Map();
  for (const r of records || []) m.set(`${r.target}:${r.video}`, r);
  return m;
}

/** まだ決めていない提案（取り消したものも含む）。 */
export function undecided(g, decisions) {
  if (!decisions || !g.id) return g.items;
  return g.items.filter((it) => {
    const r = decisions.get(`${g.id}:${it.id}`);
    return !r || r.action === "none" || (ADD_ACTIONS.includes(r.action) && r.applied_at && !isFixed(r));
  });
}

const PAGE_SCRIPT = `
const hide = document.getElementById("hide");
function refresh() {
  for (const li of document.querySelectorAll(".item")) {
    const a = li.dataset.action;
    li.classList.toggle("decided", !!a && a !== "none");
    li.hidden = hide.checked && li.classList.contains("decided");
    const done = li.dataset.fixed === "1";
    li.querySelector(".undo").hidden = !a || a === "none" || done;
    for (const b of li.querySelectorAll(".act")) b.disabled = done || li.dataset.busy === "1" || b.dataset.a === a;
  }
  for (const sec of document.querySelectorAll(".group")) {
    const all = sec.querySelectorAll(".item").length;
    const left = [...sec.querySelectorAll(".item")].filter((li) => !li.classList.contains("decided")).length;
    sec.querySelector(".left").textContent = "未決定 " + left + " / " + all + " 曲";
  }
}
document.addEventListener("click", async (ev) => {
  const play = ev.target.closest(".play");
  if (play) {
    const id = play.dataset.v;
    const f = document.createElement("iframe");
    f.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(id) + "?autoplay=1&playsinline=1&rel=0";
    f.allow = "autoplay; encrypted-media; picture-in-picture";
    f.allowFullscreen = true;
    f.title = play.dataset.title || id;
    play.replaceWith(f);
    return;
  }
  const btn = ev.target.closest(".act, .undo");
  if (!btn) return;
  const li = btn.closest(".item");
  const action = btn.classList.contains("undo") ? "none" : btn.dataset.a;
  li.dataset.busy = "1"; refresh();
  const st = li.querySelector(".state");
  st.textContent = "送信中…";
  try {
    const res = await fetch("/api/decide", {
      method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
      body: JSON.stringify({ target: li.dataset.t, video: li.dataset.v, action }),
    });
    const r = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(r.error || res.status);
    li.dataset.action = r.record ? r.record.action : "none";
    st.textContent = r.label || "";
  } catch (e) {
    st.textContent = "送信できませんでした: " + e.message;
  }
  li.dataset.busy = "0"; refresh();
});
hide.addEventListener("change", refresh);
refresh();
`;

export function suggestionsPage(sug, decisions) {
  const groups = sug.groups || [];
  const total = groups.reduce((n, g) => n + g.items.length, 0);
  // 同じ曲が複数の再生リストに提案されている場合に、ほかの提案先も表示する
  const targetsOf = new Map();
  for (const g of groups) for (const it of g.items) targetsOf.set(it.id, [...(targetsOf.get(it.id) || []), g.name]);
  const nav = groups.map((g, n) => `<a class="btn" href="#g${n}">${escapeHtml(g.name)}（${g.items.length}）</a>`).join("");
  const body = groups.map((g, n) => {
    const name = escapeHtml(g.name);
    const left = undecided(g, decisions);
    const pages = Math.ceil(left.length / MAX_VIDEOS);
    const opens = Array.from({ length: pages }, (_, p) => {
      const from = p * MAX_VIDEOS + 1;
      const to = Math.min(left.length, (p + 1) * MAX_VIDEOS);
      const label = pages > 1 ? `（${from}〜${to}曲目）` : "";
      return `<a class="btn" href="/suggestions/${n}?p=${p}">まとめて YouTube で開く${label}</a>` +
        `<a class="btn" href="/suggestions/${n}?p=${p}&to=music">YouTube Music で開く${label}</a>`;
    }).join("");
    const target = g.id
      ? `<a href="https://www.youtube.com/playlist?list=${encodeURIComponent(g.id)}">提案先「${name}」を YouTube で開く</a>`
      : "<small>（提案先の再生リストが見つからないため、ここでは決められません）</small>";
    const items = g.items.map((it) => {
      const others = (targetsOf.get(it.id) || []).filter((x) => x !== g.name);
      const rec = decisions && g.id ? decisions.get(`${g.id}:${it.id}`) : null;
      const fixed = isFixed(rec);
      // 「その他の曲に追加」：その他の曲の再生リストがあり、提案先がその他の曲ではなく、まだ入っていない曲だけ
      const canMisc = !!(sug.misc && sug.misc.id && g.id !== sug.misc.id && !(it.in || []).includes(sug.misc.name));
      const vid = escapeHtml(it.id);
      const canDecide = !!(g.id && decisions);
      return `<li class="item" data-t="${escapeHtml(g.id || "")}" data-v="${vid}" data-action="${escapeHtml(rec?.action || "")}" data-fixed="${fixed ? 1 : 0}">
<button class="play" data-v="${vid}" data-title="${escapeHtml(it.title || "")}" aria-label="再生"><img loading="lazy" alt="" src="https://i.ytimg.com/vi/${vid}/mqdefault.jpg"><span>▶</span></button>
<div class="meta"><a href="https://youtu.be/${vid}">${escapeHtml(it.title || it.id)}</a>
<br><small>${escapeHtml(it.channel || "")}${it.published_at ? `・${escapeHtml(String(it.published_at).slice(0, 10))}` : ""}</small>
<br><small>理由: ${escapeHtml(it.reason)}${
        it.in && it.in.length ? `・今入っている: ${escapeHtml(it.in.join("、"))}` : ""}${
        others.length ? `・ほかの提案先: ${escapeHtml(others.join("、"))}` : ""}</small>
${canDecide ? `<div class="actions"><button class="act add" data-a="add">「${name}」に追加</button>${
        canMisc ? `<button class="act misc" data-a="misc">「${escapeHtml(sug.misc.name)}」に追加</button>` : ""}<button class="act reject" data-a="reject">却下</button>
<button class="undo" hidden>取り消し</button> <small class="state">${escapeHtml(stateLabel(rec))}</small></div>` : ""}</div></li>`;
    }).join("");
    return `<section id="g${n}" class="group"><h2>提案先: ${name}（${g.items.length} 曲）</h2>
<p><small class="left"></small></p><div>${target}</div><div>${opens}</div><ul>${items}</ul></section>`;
  }).join("");
  const note = decisions
    ? `<p><small>曲ごとに ▶ で再生して確かめ、<b>追加</b>・<b>その他の曲に追加</b>・<b>却下</b>のどれかを選びます。
「その他の曲に追加」にした曲は、提案先ではなく「その他の曲」に入り、その提案先にはもう提案しません。追加にした曲は、<b>次回の同期</b>（毎朝）で提案先の再生リストに入ります。
却下にした曲は、その提案先にはもう提案しません。どちらも選ばなかった曲は、次回も提案されます。
追加の反映前と、却下は「取り消し」で戻せます（反映済みの追加は、このシステムでは消せません）。</small></p>`
    : `<p><small><b>決定の保存先（D1）が使えないため、追加・却下は選べません。</b>Worker のデプロイ（void deploy）でデータベースが作られているかを確認してください。</small></p>`;
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>追加の提案</title>${STYLE}</head><body>
<h1>追加の提案</h1>
<p><small>${total} 件（${escapeHtml(sug.generated_at || "")} 時点）。提案は<b>提案先の再生リストごと</b>に分かれています。</small></p>
${note}
<p><label><input type="checkbox" id="hide" checked> 決めた曲を隠す</label></p>
<div>${nav}</div>
${body || "<p>提案はありません。</p>"}<p><a href="/">← メニュー</a></p>
<script>${PAGE_SCRIPT}</script></body></html>`;
}

/**
 * 手で追加してほしい曲（クォータが足りない日に、API で入れる代わりに一時リストで渡す分）。
 * 保存は YouTube アプリで行う（一時リストをまとめて再生リストに保存できる）ので、YouTube で開くボタンだけを出す。
 */
export function handPage(sug) {
  const groups = sug.hand || [];
  const total = groups.reduce((n, g) => n + g.items.length, 0);
  const body = groups.map((g, n) => {
    const name = escapeHtml(g.name);
    const pages = Math.ceil(g.items.length / MAX_VIDEOS);
    const opens = Array.from({ length: pages }, (_, p) => {
      const from = p * MAX_VIDEOS + 1;
      const to = Math.min(g.items.length, (p + 1) * MAX_VIDEOS);
      return `<p><b>${from}〜${to}曲目：</b><a class="btn" href="/hand/${n}?p=${p}">YouTube アプリで開く</a>` +
        `<a class="btn" href="/hand/${n}?p=${p}&app=intent">Android の YouTube アプリで開く</a></p>`;
    }).join("");
    const items = g.items.map((it) => `<li class="item"><a href="https://youtu.be/${escapeHtml(it.id)}">${escapeHtml(it.title || it.id)}</a>` +
      `${it.channel ? ` <small>${escapeHtml(it.channel)}</small>` : ""}</li>`).join("");
    return `<section class="group"><h2>「${name}」に追加（${g.items.length} 曲）</h2>${opens}
<details><summary>曲の一覧</summary><ul>${items}</ul></details>
<p><small><a href="https://www.youtube.com/playlist?list=${encodeURIComponent(g.id)}">「${name}」を YouTube で開く</a></small></p></section>`;
  }).join("");
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>手で追加してほしい曲</title>${STYLE}</head><body>
<h1>手で追加してほしい曲</h1>
<p><small>${total} 曲（${escapeHtml(sug.generated_at || "")} 時点）。その日の YouTube API の使用量（クォータ）が足りなかったため、
システムが入れる代わりに、まとめて保存してほしい曲です。</small></p>
<ol>
<li>下のボタンで、曲を一時リストとして <b>YouTube アプリ</b>で開きます（YouTube Music ではなく YouTube です）。</li>
<li>一時リストを保存するメニューから、見出しの再生リスト（例：「${escapeHtml((groups[0] || {}).name || "未仕分け")}」）を選んで、まとめて保存します。</li>
<li>50曲を超える場合は、ボタンごとに繰り返します。</li>
<li>次の同期（毎朝）で、入っているかを確かめます。入っていればこの一覧から消え、入っていなければまた表示されます。</li>
</ol>
${body || "<p>手で追加してほしい曲はありません。</p>"}<p><a href="/">← メニュー</a></p></body></html>`;
}

export function menu(index, sug = EMPTY_SUGGESTIONS, review = EMPTY_REVIEW) {
  const link = (href, label, note = "") =>
    `<li><a href="${escapeHtml(href)}">${escapeHtml(label)}</a>${note ? ` <small>${escapeHtml(note)}</small>` : ""}</li>`;
  const tops = (index.top_favorites || []).map((g) =>
    link(`/top/${encodeURIComponent(g.name)}`, `${g.name} ミックス`, `${g.playlists.length} 再生リスト`)).join("");
  const n = (index.playlists || []).reduce((s, p) => s + (p.videos || []).length, 0);
  const handN = (sug.hand || []).reduce((s, g) => s + g.items.length, 0);
  const reviewN = (review.items || []).length;
  const notice = (handN
    ? `<p class="notice"><b>手で追加してほしい曲が ${handN} 曲あります。</b> <a href="/hand">手順と一時リストを開く</a></p>` : "") +
    (reviewN ? `<p class="notice"><b>要確認の項目が ${reviewN} 件あります。</b> <a href="/review">一覧を開く</a></p>` : "");
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>オリジナルミックス</title>
${STYLE}</head><body>
<h1>オリジナルミックス</h1>${notice}<p><small>${(index.playlists || []).length} 再生リスト・のべ ${n} 曲（${escapeHtml(index.generated_at || "")} 時点）</small></p>
<ul>${link(`/random`, "完全ランダム")}${link(`/playlists?k=3`, "ランダム再生リスト", "3つの再生リストから")}</ul>
<h2>特にお気に入りのチャンネル</h2><ul>${tops || "<li><small>なし</small></li>"}</ul>
<h2>管理</h2><ul>${link(`/suggestions`, "追加の提案",
    `${(sug.groups || []).reduce((n, g) => n + g.items.length, 0)} 件`)}${link(`/hand`, "手で追加してほしい曲", `${handN} 曲`)}${link(`/review`, "要確認", `${reviewN} 件`)}</ul>
<p><small>リンクを開くたびに選び直します。Android でブラウザが開く場合は、URL に <code>?app=intent</code> を付けてください。</small></p>
<p><small><a href="/random?app=intent">完全ランダム（Android アプリを指定）</a></small></p>
</body></html>`;
}

const REVIEW_KINDS = {
  channel: {
    title: "チャンネル（ignore_channels）",
    about: "管理している再生リストに動画が入っていたものの、監視対象（3分類）にも ignore_channels にも書かれていなかったチャンネルです。"
      + "自動で ignore_channels（監視しない）に入れてあります。",
    todo: "監視したいなら、config.yaml の top_favorites・favorites・occasional のどれか（同じ人の channels に足す など）に移します。"
      + "監視しないままでよければ、ignore_channels のその行の「# 要確認…」のコメントを消します。",
    link: (id) => `https://www.youtube.com/channel/${encodeURIComponent(id)}`,
  },
  playlist: {
    title: "再生リスト（ignore_playlists）",
    about: "YouTube にあったものの、config.yaml のどこにも書かれていなかった再生リストです。自動で ignore_playlists（管理外）に入れてあります。",
    todo: "管理したいなら、config.yaml の正しい場所（3分類の playlist・specials・songs など）に移します。"
      + "管理外のままでよければ、ignore_playlists のその行の「# 要確認…」のコメントを消します。",
    link: (id) => `https://www.youtube.com/playlist?list=${encodeURIComponent(id)}`,
  },
};

/** config.yaml で「# 要確認」が付いている項目（同期が data/mix/review.json に書き出したもの）。 */
export function reviewPage(review) {
  const items = review.items || [];
  const body = Object.entries(REVIEW_KINDS).map(([kind, k]) => {
    const rows = items.filter((it) => it.kind === kind);
    if (!rows.length) return "";
    const li = rows.map((it) => `<li class="item"><div class="meta"><a href="${escapeHtml(k.link(it.id))}">${escapeHtml(it.name || it.id)}</a>
<br><small><code>${escapeHtml(it.id)}</code>${it.note ? `・${escapeHtml(it.note)}` : ""}</small></div></li>`).join("");
    return `<section class="group"><h2>${escapeHtml(k.title)}（${rows.length} 件）</h2>
<p><small>${escapeHtml(k.about)}</small></p><p><small><b>確認したら：</b>${escapeHtml(k.todo)}</small></p><ul>${li}</ul></section>`;
  }).join("");
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>要確認</title>${STYLE}</head><body>
<h1>要確認</h1>
<p><small>${items.length} 件（${escapeHtml(review.generated_at || "")} 時点）。config.yaml で「# 要確認」が付いている項目です。
コメントを消すか項目を移すと、次の同期のあとにこの一覧から消えます。</small></p>
${body || "<p>要確認の項目はありません。</p>"}<p><a href="/">← メニュー</a></p></body></html>`;
}
