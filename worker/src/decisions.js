// 追加の提案に対する決定（追加・却下）の記録。
//
// 保存先は D1（src/d1-repo.js。void/db）。ここは保存先に依存しない部分で、
// get(target, video) / put(record) / list() を持つ repo を受け取る（テストでは memoryRepo を使う）。
//
// 1件の記録（キーは 提案先の再生リストID と 動画ID の組）
//   target, target_name, video, title, channel
//   action      : "add"（提案先に追加する）/ "misc"（提案先ではなく「その他の曲」に追加する）/ "reject"（却下）/ "none"（取り消した）
//   decided_at  : 決めた日時（ISO 8601）。決め直すたびに更新する
//   by          : 決めた人のメールアドレス（分かる場合）
//   applied_at  : 毎日の同期が反映した日時（未反映ならなし）
//   result      : 反映の結果 "added" / "already"（すでに入っていた）/ "recorded"（却下を記録）/ "failed" / "invalid"
//   error       : 失敗の理由
//
// 「追加」は毎日の同期が次の実行で再生リストに入れ、結果を ack で返す。反映済みの「追加」は取り消せない
// （このシステムは再生リストから曲を消さないため）。「却下」はいつでも取り消せる。

export const ACTIONS = ["add", "misc", "reject", "none"];
/** 再生リストに入れる決定（反映済みなら取り消せない）。 */
export const ADD_ACTIONS = ["add", "misc"];

export function isFixed(rec) {
  return !!(rec && ADD_ACTIONS.includes(rec.action) && rec.applied_at && ["added", "already"].includes(rec.result));
}
/** テスト用：メモリに保存する repo。 */
export function memoryRepo() {
  const map = new Map();
  return {
    map,
    async get(target, video) { return structuredClone(map.get(`${target}:${video}`) ?? null); },
    async put(rec) { map.set(`${rec.target}:${rec.video}`, structuredClone(rec)); },
    async list() { return [...map.entries()].sort(([a], [b]) => (a < b ? -1 : 1)).map(([, v]) => structuredClone(v)); },
  };
}

const ID_RE = /^[0-9A-Za-z_-]{1,64}$/;

export function validIds(target, video) {
  return ID_RE.test(String(target || "")) && /^[0-9A-Za-z_-]{11}$/.test(String(video || ""));
}

export class DecisionLog {
  constructor(repo, now = () => new Date().toISOString()) {
    this.repo = repo;
    this.now = now;
  }

  async get(target, video) {
    return (await this.repo.get(target, video)) ?? null;
  }

  /** すべての記録（提案先・動画の順）。 */
  async list() {
    return this.repo.list();
  }

  /**
   * 決定を記録する。info は {target_name, title, channel, by}。
   * 戻り値は {ok, record} または {ok:false, status, error}。
   */
  async decide(target, video, action, info = {}) {
    if (!validIds(target, video)) return { ok: false, status: 400, error: "再生リストIDまたは動画IDが正しくありません" };
    if (!ACTIONS.includes(action)) return { ok: false, status: 400, error: "action は add / misc / reject / none のどれかです" };
    const prev = await this.get(target, video);
    if (isFixed(prev)) {
      return { ok: false, status: 409, error: "すでに再生リストに追加済みです（このシステムは曲を消せません）", record: prev };
    }
    if (!prev && action === "none") return { ok: true, record: null };
    const rec = {
      target, video,
      target_name: info.target_name ?? prev?.target_name ?? null,
      title: info.title ?? prev?.title ?? null,
      channel: info.channel ?? prev?.channel ?? null,
      action,
      decided_at: this.now(),
      by: info.by ?? null,
    };
    await this.repo.put(rec);
    return { ok: true, record: rec };
  }

  /**
   * 同期が反映した結果を記録する。results は [{target, video, decided_at, result, error?}]。
   * decided_at が今の記録と違う（同期が読んだあとに決め直した）ものは無視する。
   */
  async ack(results) {
    let n = 0;
    for (const r of results || []) {
      if (!validIds(r.target, r.video)) continue;
      const rec = await this.get(r.target, r.video);
      if (!rec || rec.decided_at !== r.decided_at) continue;
      rec.applied_at = r.applied_at || this.now();
      rec.result = String(r.result || "");
      if (r.error) rec.error = String(r.error);
      else delete rec.error;
      await this.repo.put(rec);
      n++;
    }
    return n;
  }
}

/** 画面に出す状態の名前。 */
export function stateLabel(rec) {
  if (!rec || rec.action === "none") return "";
  if (ADD_ACTIONS.includes(rec.action)) {
    const where = rec.action === "misc" ? "「その他の曲」に" : "";
    if (!rec.applied_at) return `${where}追加予定（次回の同期で反映）`;
    if (rec.result === "added") return `${where}追加済み`;
    if (rec.result === "already") return `${where}追加済み（すでに入っていました）`;
    return `${where}追加できませんでした（${rec.error || rec.result}）`;
  }
  return "却下";
}
