// D1 のテーブル定義（Drizzle）。変えたら `npm run db:generate` で db/migrations/ に SQL を作ってコミットする。
import { primaryKey, sqliteTable, text } from 'void/schema-d1';

/** 追加の提案に対する決定（src/decisions.js の記録と同じ項目）。 */
export const decisions = sqliteTable(
  'decisions',
  {
    target: text('target').notNull(), // 提案先の再生リストID
    video: text('video').notNull(), // 動画ID
    targetName: text('target_name'),
    title: text('title'),
    channel: text('channel'),
    action: text('action').notNull(), // add / misc / reject / none
    decidedAt: text('decided_at').notNull(),
    by: text('by'),
    appliedAt: text('applied_at'),
    result: text('result'),
    error: text('error'),
  },
  (t) => [primaryKey({ columns: [t.target, t.video] })],
);
