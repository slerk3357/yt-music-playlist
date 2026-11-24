// Worker の入口。ビルド時（build.mjs）に作った索引を組み込む。
import INDEX from "./generated/index-data.json" with { type: "json" };
import { createHandler } from "./index.js";

export default createHandler(INDEX);
