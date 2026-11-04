import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import vm from "node:vm";
import ts from "typescript";

function load(file, globals) {
  const source = readFileSync(
    new URL(`../src/lib/${file}.ts`, import.meta.url),
    "utf8",
  );
  const output = ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
    },
  }).outputText;
  const sandbox = { exports: {}, ...globals };
  vm.runInNewContext(output, sandbox);
  return sandbox.exports;
}

test("session and idempotency identifiers work without secure-context randomUUID", () => {
  const { randomId } = load("browser", {
    crypto: { getRandomValues: (value) => webcrypto.getRandomValues(value) },
  });
  const one = randomId(),
    two = randomId();
  assert.match(one, /^[a-f0-9]{32}$/);
  assert.notEqual(one, two);
});

function clipboardEnvironment(nativeClipboard, success = true) {
  let current,
    copied = "",
    removed = false,
    restored = false;
  class Element {
    focus() {
      restored = true;
    }
  }
  const parent = {
    append(value) {
      current = value;
    },
  };
  const globals = {
    HTMLElement: Element,
    navigator: nativeClipboard ? { clipboard: nativeClipboard } : {},
    document: {
      activeElement: new Element(),
      querySelector: () => parent,
      body: parent,
      createElement: () => ({
        value: "",
        style: {},
        setAttribute() {},
        select() {},
        remove() {
          removed = true;
        },
      }),
      execCommand: (command) => {
        assert.equal(command, "copy");
        copied = current.value;
        return success;
      },
    },
  };
  return { globals, result: () => ({ copied, removed, restored }) };
}

test("HTTP deployment copies within a dialog without navigator.clipboard", async () => {
  const environment = clipboardEnvironment(null);
  await load("browser", environment.globals).copyText(
    "socks5://owner:encoded%40password@example.com:1080",
  );
  assert.equal(
    environment.result().copied,
    "socks5://owner:encoded%40password@example.com:1080",
  );
  assert.ok(environment.result().removed && environment.result().restored);
});

test("denied clipboard access falls back to the user initiated copy command", async () => {
  const environment = clipboardEnvironment({
    writeText: async () => {
      throw new Error("not allowed");
    },
  });
  await load("browser", environment.globals).copyText(
    "connection configuration",
  );
  assert.equal(environment.result().copied, "connection configuration");
});

test("failed copy still removes temporary content and restores focus", async () => {
  const environment = clipboardEnvironment(null, false);
  await assert.rejects(
    load("browser", environment.globals).copyText("private"),
  );
  assert.ok(environment.result().removed && environment.result().restored);
});

test("theme persists without affecting proxy credentials", () => {
  const values = new Map();
  const root = { dataset: {} };
  const globals = {
    document: { documentElement: root, querySelector: () => null },
    localStorage: {
      getItem: (key) => values.get(key),
      setItem: (key, value) => values.set(key, value),
    },
  };
  const { readTheme, applyTheme } = load("theme", globals);
  assert.equal(readTheme(), "blue");
  applyTheme("rose");
  assert.equal(root.dataset.theme, "rose");
  assert.equal(readTheme(), "rose");
  assert.deepEqual([...values.keys()], ["rapip.theme"]);
});

test("blocked localStorage leaves a working blue default and live theme selection", () => {
  const root = { dataset: {} };
  const { readTheme, applyTheme } = load("theme", {
    document: { documentElement: root, querySelector: () => null },
    localStorage: {
      getItem() {
        throw new Error("blocked");
      },
      setItem() {
        throw new Error("blocked");
      },
    },
  });
  assert.equal(readTheme(), "blue");
  applyTheme("violet");
  assert.equal(root.dataset.theme, "violet");
});
