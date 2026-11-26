import { defineHandler } from 'void';
import { syncAckRoute } from '../../../src/app.js';

// POST /api/sync/ack  毎日の同期が反映結果を返す {results: [...]}（同期用のサービストークンだけ）
export const POST = defineHandler(syncAckRoute);
