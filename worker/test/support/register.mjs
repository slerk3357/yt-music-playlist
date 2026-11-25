// テスト用：Workers の組み込みモジュール（cloudflare:workers）を簡単な代用品に差し替える
import { register } from "node:module";

register("./hooks.mjs", import.meta.url);
