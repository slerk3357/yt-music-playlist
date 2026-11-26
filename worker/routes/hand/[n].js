import { defineHandler } from 'void';
import { handOpenRoute } from '../../src/app.js';

// GET /hand/<n>?p=0  n 番目の再生リストに手で追加してほしい曲を、50曲ずつ YouTube アプリの一時リストで開く
export const GET = defineHandler(handOpenRoute);
