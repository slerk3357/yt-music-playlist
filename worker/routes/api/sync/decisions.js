import { defineHandler } from 'void';
import { syncDecisionsRoute } from '../../../src/app.js';

// GET /api/sync/decisions  毎日の同期が決定を読む（同期用のサービストークンだけ）
export const GET = defineHandler(syncDecisionsRoute);
