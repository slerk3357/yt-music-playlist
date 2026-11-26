import { defineHandler } from 'void';
import { decideRoute } from '../../src/app.js';

// POST /api/decide  {target, video, action}  追加の提案への決定（add / misc / reject / none）。D1 に保存する
export const POST = defineHandler(decideRoute);
