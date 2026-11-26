import { defineHandler } from 'void';
import { handRoute } from '../../src/app.js';

// GET /hand  手で追加してほしい曲（クォータが足りない日の分）の手順と一覧
export const GET = defineHandler(handRoute);
