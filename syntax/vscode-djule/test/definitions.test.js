const assert = require("assert");
const fs = require("fs");
const Module = require("module");
const os = require("os");
const path = require("path");

const originalLoad = Module._load;
Module._load = function patchedLoad(request, parent, isMain) {
  if (request === "vscode") {
    return {
      Position: class Position {
        constructor(line, character) {
          this.line = line;
          this.character = character;
        }
      },
      Uri: {
        file(filePath) {
          return { fsPath: filePath };
        },
      },
      Location: class Location {
        constructor(uri, position) {
          this.uri = uri;
          this.range = { start: position };
        }
      },
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

const { resolveDjuleDefinitionTarget } = require("../lib/definitions");

function withTempDir(run) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "djule-vscode-definitions-"));
  try {
    run(tempDir);
  } finally {
    fs.rmSync(tempDir, { force: true, recursive: true });
  }
}

function createFile(filePath, contents = "") {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, contents, "utf8");
}

function offsetFor(source, needle) {
  const index = source.indexOf(needle);
  if (index === -1) {
    throw new Error(`Could not find '${needle}' in source`);
  }
  return index;
}

withTempDir((tempDir) => {
  const filePath = path.join(tempDir, "page.djule");
  const source = `def Card():\n    return (\n        <div></div>\n    )\n\ndef Page():\n    return (\n        <Card></Card>\n    )\n`;
  createFile(filePath, source);

  const target = resolveDjuleDefinitionTarget(filePath, source, offsetFor(source, "<Card>") + 1, [tempDir]);
  assert(target, "Expected local component definition target");
  assert.strictEqual(target.filePath, filePath);
  assert.strictEqual(target.line, 0);
});

withTempDir((tempDir) => {
  const frontendRoot = path.join(tempDir, "frontend");
  const componentPath = path.join(frontendRoot, "components", "inputs", "inputErr.djule");
  const pagePath = path.join(frontendRoot, "pages", "login.djule");

  createFile(componentPath, `def InputErr():\n    return (\n        <div></div>\n    )\n`);
  const source = `from components.inputs.inputErr import InputErr\n\ndef Page():\n    return (\n        <InputErr></InputErr>\n    )\n`;
  createFile(pagePath, source);

  const target = resolveDjuleDefinitionTarget(
    pagePath,
    source,
    offsetFor(source, "<InputErr>") + 1,
    [frontendRoot]
  );
  assert(target, "Expected imported component definition target");
  assert.strictEqual(target.filePath, path.resolve(componentPath));
  assert.strictEqual(target.line, 0);
});

console.log("definition tests passed");
