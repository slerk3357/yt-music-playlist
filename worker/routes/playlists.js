import { defineHandler } from 'void';
import { mixRoute } from '../src/app.js';

// GET /playlists?k=3  ランダムに選んだ k 個の再生リストから
export const GET = defineHandler((c) => mixRoute(c, 'playlists'));
