// Void の設定。デプロイは GitHub Actions の `void deploy`（.github/workflows/deploy-worker.yml）。
// Worker 名を変えると、別の Worker として新しく作られる。
import { defineConfig } from 'void/config';

export default defineConfig({
  worker: {
    compatibility_date: '2026-11-01',
  },
  cloudflare: {
    name: 'yt-music-playlist-mix',
    workers_dev: true,
  },
});
