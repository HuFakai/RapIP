import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const source = readFileSync(new URL("../src/lib/utils.ts", import.meta.url), "utf8").replace(/^import .*$/gm, "");
const output = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const sandbox = { exports: {} };
vm.runInNewContext(output, sandbox);
const { bytes, trafficScale } = sandbox.exports;
test("traffic cards format fractional bytes and choose readable binary units", () => {
  assert.equal(bytes(363.5761576862315), "363.6 B");
  assert.equal(bytes(13567985.4), "12.94 MB");
  assert.equal(bytes(415486173), "396.24 MB");
  assert.equal(bytes(0), "0 B");
  assert.equal(bytes(Infinity), "—");
});
test("traffic chart scales all series together and preserves rate versus volume", () => {
  assert.equal(trafficScale([null, 1024, 13567985.4], true).unit, "MB/s");
  assert.equal(trafficScale([null, 1024, 13567985.4], true).divisor, 1024 ** 2);
  assert.equal(trafficScale([415486173, 127382753]).unit, "MB");
  assert.equal(trafficScale([0, null], true).unit, "B/s");
  assert.equal(trafficScale([1024 ** 4]).unit, "TB");
});
