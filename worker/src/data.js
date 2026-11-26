// ビルド時に組み込むデータ（vite.config.ts が非公開リポジトリの data/mix/ から src/generated/ に書き出す）。
import INDEX from './generated/index-data.json';
import REVIEW from './generated/review-data.json';
import SUGGESTIONS from './generated/suggestions-data.json';
import { buildData } from './app.js';

export const DATA = buildData(INDEX, SUGGESTIONS, REVIEW);
