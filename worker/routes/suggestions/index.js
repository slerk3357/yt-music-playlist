import { defineHandler } from 'void';
import { suggestionsRoute } from '../../src/app.js';

// GET /suggestions  追加の提案（提案先ごと。埋め込み再生と追加・却下のボタン）
export const GET = defineHandler(suggestionsRoute);
