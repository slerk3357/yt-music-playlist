import { defineHandler } from 'void';
import { suggestionsOpenRoute } from '../../src/app.js';

// GET /suggestions/<n>?p=0  n 番目の提案先のまだ決めていない曲を、50曲ずつ一時リストで開く（to=music で YouTube Music）
export const GET = defineHandler(suggestionsOpenRoute);
