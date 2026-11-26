// サーバーで使う値。本番の値は Worker のシークレットとして入れる（`npx void secret put <名前>`、
// または Cloudflare のダッシュボードの Settings → Variables and Secrets で種類を「シークレット」にして追加）。
import { defineEnv, string } from 'void/env';

export default defineEnv({
  // 毎日の同期に使う Cloudflare Access のサービストークンの Client ID（/api/sync/… をこのトークンだけに許す）
  SYNC_CLIENT_ID: string().optional(),
  // 使ってよいアカウントのメールアドレス（カンマ区切り）。Access のポリシーに加えた二重の確認（任意）
  ALLOWED_EMAILS: string().optional(),
});
