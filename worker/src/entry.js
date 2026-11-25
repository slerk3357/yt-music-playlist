// Worker の入口。ビルド時（build.mjs）に作った索引と提案を組み込む。
import { DurableObject } from "cloudflare:workers";
import INDEX from "./generated/index-data.json" with { type: "json" };
import SUGGESTIONS from "./generated/suggestions-data.json" with { type: "json" };
import { DecisionLog } from "./decisions.js";
import { createHandler } from "./index.js";

/**
 * 追加の提案に対する決定を保存する Durable Object（SQLite 方式。無料プランで使える）。
 * 1つだけ（名前 "main"）作って使う。Worker からは RPC で呼ぶ。
 */
export class DecisionStore extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.log = new DecisionLog(ctx.storage);
  }

  decide(target, video, action, info) {
    return this.log.decide(target, video, action, info);
  }

  list() {
    return this.log.list();
  }

  ack(results) {
    return this.log.ack(results);
  }
}

export default createHandler(INDEX, SUGGESTIONS);
