import { defineHandler } from 'void';
import { mixRoute } from '../src/app.js';

// GET /random  完全ランダム（n=曲数, open=list, app=intent, format=json）
export const GET = defineHandler((c) => mixRoute(c, 'random'));
