// Cloudflare Access による認証（src/middleware.js）。
import { defineMiddleware } from 'void';
import { env } from 'void/env';
import { accessMiddleware } from '../src/middleware.js';

export default defineMiddleware(
  accessMiddleware(() => ({ SYNC_CLIENT_ID: env.SYNC_CLIENT_ID, ALLOWED_EMAILS: env.ALLOWED_EMAILS })),
);
