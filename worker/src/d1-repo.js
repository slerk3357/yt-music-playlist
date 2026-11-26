// 決定の保存先：D1（void/db）。src/decisions.js の DecisionLog に渡す repo。
import { and, asc, db, eq } from 'void/db';
import { decisions } from '../db/schema';

const FIELDS = [
  ['target', 'target'], ['video', 'video'], ['target_name', 'targetName'], ['title', 'title'], ['channel', 'channel'],
  ['action', 'action'], ['decided_at', 'decidedAt'], ['by', 'by'], ['applied_at', 'appliedAt'], ['result', 'result'],
  ['error', 'error'],
];

function toRecord(row) {
  if (!row) return null;
  const rec = {};
  for (const [k, col] of FIELDS) if (row[col] !== null && row[col] !== undefined) rec[k] = row[col];
  return rec;
}

function toRow(rec) {
  const row = {};
  for (const [k, col] of FIELDS) row[col] = rec[k] ?? null;
  return row;
}

export function d1Repo() {
  return {
    async get(target, video) {
      const rows = await db.select().from(decisions)
        .where(and(eq(decisions.target, target), eq(decisions.video, video))).limit(1);
      return toRecord(rows[0]);
    },
    async put(rec) {
      const row = toRow(rec);
      await db.insert(decisions).values(row)
        .onConflictDoUpdate({ target: [decisions.target, decisions.video], set: row });
    },
    async list() {
      const rows = await db.select().from(decisions).orderBy(asc(decisions.target), asc(decisions.video));
      return rows.map(toRecord);
    },
  };
}
