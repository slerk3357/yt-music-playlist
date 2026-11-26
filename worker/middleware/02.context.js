// ビルド時に組み込んだデータと、決定の保存先（D1）を用意する（src/middleware.js）。
import { defineMiddleware } from 'void';
import { DATA } from '../src/data.js';
import { d1Repo } from '../src/d1-repo.js';
import { DecisionLog } from '../src/decisions.js';
import { contextMiddleware } from '../src/middleware.js';

export default defineMiddleware(contextMiddleware(DATA, () => new DecisionLog(d1Repo())));
