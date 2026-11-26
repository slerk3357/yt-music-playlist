import { defineHandler } from 'void';
import { mixRoute } from '../../src/app.js';

// GET /top/<name>  特にお気に入りのチャンネル <name>（または番号）の再生リストから
export const GET = defineHandler((c) => mixRoute(c, 'top'));
