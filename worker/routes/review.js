import { defineHandler } from 'void';
import { reviewRoute } from '../src/app.js';

// GET /review  要確認（config.yaml で「# 要確認」が付いている項目）
export const GET = defineHandler(reviewRoute);
