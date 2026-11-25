// Worker の入口。ビルド時（build.mjs）に作った索引と提案を組み込む。
import INDEX from "./generated/index-data.json" with { type: "json" };
import SUGGESTIONS from "./generated/suggestions-data.json" with { type: "json" };
import { createHandler } from "./index.js";

export default createHandler(INDEX, SUGGESTIONS);
