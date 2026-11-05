import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const source = readFileSync(new URL("../src/lib/quotas.ts", import.meta.url), "utf8");
const output = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const sandbox = { exports: {} };
vm.runInNewContext(output, sandbox);
const { quotaPair, trafficPair, packageBytes } = sandbox.exports;
test("supplier balances distinguish unknown from exhausted and keep package GB", () => {
 assert.equal(quotaPair(0, 1000), "0 / 1,000");
 assert.equal(quotaPair(null, 1000), "待同步");
 assert.equal(trafficPair(249995000000, 250000000000), "249.995 / 250 GB");
 assert.equal(packageBytes(5000000), "5 MB");
 assert.equal(packageBytes(null), "—");
 assert.equal(trafficPair(0, 250000000000), "0 / 250 GB");
});
