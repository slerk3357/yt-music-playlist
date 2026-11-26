import { voidPlugin } from 'void';
import { defineConfig } from 'vite';
import { embedData } from './embed-data.mjs';

// data/mix/ の索引・提案・要確認を組み込む（dev・build・void deploy のどれでも、設定を読むときに実行される）
embedData();

export default defineConfig({
  plugins: [voidPlugin()],
});
