import { defineHandler } from 'void';
import { menuRoute } from '../src/app.js';

// GET /  メニュー
export const GET = defineHandler(menuRoute);
