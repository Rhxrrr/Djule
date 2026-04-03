const assert = require("assert");
const fs = require("fs");
const Module = require("module");
const os = require("os");
const path = require("path");

const originalLoad = Module._load;
Module._load = function patchedLoad(request, parent, isMain) {
  if (request === "vscode") {
    return {
      commands: {
        executeCommand: async () => "",
      },
      extensions: {
        getExtension: () => null,
      },
      workspace: {
        getWorkspaceFolder: () => null,
        getConfiguration: () => ({ get: () => "" }),
      },
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

const {
  inferDjangoFallbackGlobals,
  inferDocumentImportRoots,
  looksLikeDjangoProject,
  resolveRuntimeRoot,
} = require("../lib/runtime");

function withTempDir(run) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "djule-vscode-runtime-"));
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

function fakeDocument(filePath, source = "") {
  return {
    getText() {
      return source;
    },
    uri: {
      fsPath: filePath,
      scheme: "file",
    },
  };
}

function fakeConfiguration(values = {}) {
  return {
    get(name, defaultValue) {
      return Object.prototype.hasOwnProperty.call(values, name) ? values[name] : defaultValue;
    },
  };
}

withTempDir((tempDir) => {
  const projectRoot = path.join(tempDir, "project");
  const frontendRoot = path.join(projectRoot, "frontend");
  const documentPath = path.join(frontendRoot, "pages", "login.djule");
  const componentPath = path.join(frontendRoot, "components", "inputs", "inputErr.djule");

  createFile(path.join(projectRoot, "manage.py"), "print('manage')");
  createFile(componentPath, "def InputErr():\n    return (\n        <div></div>\n    )\n");
  createFile(documentPath, "");

  const source = "from components.inputs.inputErr import InputErr\n";
  const roots = inferDocumentImportRoots(fakeDocument(documentPath, source), source);

  assert(
    roots.includes(path.resolve(frontendRoot)),
    `Expected inferred import roots to include ${frontendRoot}, got: ${roots.join(", ")}`
  );
});

withTempDir((tempDir) => {
  const projectRoot = path.join(tempDir, "project");
  const documentPath = path.join(projectRoot, "frontend", "pages", "login.djule");

  createFile(path.join(projectRoot, "manage.py"), "print('manage')");
  createFile(documentPath, "");

  const document = fakeDocument(documentPath);
  const configuration = fakeConfiguration();

  assert.strictEqual(looksLikeDjangoProject(document, configuration), true);

  const globals = inferDjangoFallbackGlobals(document, configuration);
  assert(globals.csrf_token, "Expected Django fallback globals to expose csrf_token");
  assert(globals.request, "Expected Django fallback globals to expose request");
});

withTempDir((tempDir) => {
  const projectRoot = path.join(tempDir, "djule-project");
  const parserRoot = path.join(projectRoot, "src", "djule", "parser");
  const documentPath = path.join(projectRoot, "frontend", "pages", "login.djule");

  createFile(path.join(projectRoot, "src", "djule", "__init__.py"), "");
  createFile(path.join(parserRoot, "__main__.py"), "print('serve')");
  createFile(path.join(parserRoot, "parser.py"), "VALUE = 1\n");
  createFile(documentPath, "");

  const document = fakeDocument(documentPath);
  const configuration = fakeConfiguration({ projectRoot });
  const context = { extensionPath: tempDir };

  const first = resolveRuntimeRoot(document, context, configuration);
  assert(first.signature, "Expected runtime root to include a Djule runtime signature");

  const waitUntil = Date.now() + 1100;
  while (Date.now() < waitUntil) {
    // Let the signature TTL expire before rescanning.
  }

  createFile(path.join(parserRoot, "parser.py"), "VALUE = 2\n");
  const second = resolveRuntimeRoot(document, context, configuration);
  assert.notStrictEqual(
    second.signature,
    first.signature,
    "Expected runtime signature to change when Djule runtime files change"
  );
});

console.log("runtime tests passed");
