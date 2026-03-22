const cp = require("child_process");
const fs = require("fs");
const path = require("path");
const vscode = require("vscode");

const DIAGNOSTIC_SOURCE = "djule";
const DIAGNOSTIC_DEBOUNCE_MS = 200;
const HTML_TAGS = [
  "div",
  "section",
  "span",
  "main",
  "header",
  "footer",
  "h1",
  "h2",
  "h3",
  "p",
  "button",
  "ul",
  "li",
];
const COMPONENT_DEF_RE = /^\s*def\s+([A-Z][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*:/gm;
const IMPORT_FROM_RE = /^\s*from\s+([.\w]+)\s+import\s+([A-Z][A-Za-z0-9_]*(?:\s*,\s*[A-Z][A-Za-z0-9_]*)*)\s*$/gm;
const IMPORT_MODULE_RE = /^\s*import\s+([.\w]+)(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?\s*$/gm;
const moduleSignatureCache = new Map();

function activate(context) {
  const diagnostics = vscode.languages.createDiagnosticCollection(DIAGNOSTIC_SOURCE);
  const pendingTimers = new Map();
  const validationVersions = new Map();

  context.subscriptions.push(diagnostics);
  context.subscriptions.push(
    vscode.languages.registerCompletionItemProvider(
      { language: "djule" },
      {
        provideCompletionItems(document, position) {
          const configuration = vscode.workspace.getConfiguration("djule", document);
          return provideDjuleCompletions(document, position, context, configuration);
        },
      },
      "<",
      ".",
      " "
    )
  );

  function clearPending(uriKey) {
    const timer = pendingTimers.get(uriKey);
    if (timer) {
      clearTimeout(timer);
      pendingTimers.delete(uriKey);
    }
  }

  function scheduleValidation(document, delay = DIAGNOSTIC_DEBOUNCE_MS) {
    if (!shouldValidate(document)) {
      return;
    }

    const uriKey = document.uri.toString();
    clearPending(uriKey);
    validationVersions.set(uriKey, document.version);

    const timer = setTimeout(() => {
      pendingTimers.delete(uriKey);
      validateDocument(document, validationVersions.get(uriKey));
    }, delay);

    pendingTimers.set(uriKey, timer);
  }

  function shouldValidate(document) {
    return (
      document &&
      document.languageId === "djule" &&
      vscode.workspace.getConfiguration("djule", document).get("liveSyntax", true)
    );
  }

  function validateDocument(document, expectedVersion) {
    if (!shouldValidate(document) || document.isClosed || document.version !== expectedVersion) {
      return;
    }

    const configuration = vscode.workspace.getConfiguration("djule", document);
    const pythonCommand = configuration.get("pythonCommand", "python3");
    const runtimeRoot = resolveRuntimeRoot(document, context, configuration);

    const child = cp.spawn(
      pythonCommand,
      ["-m", "djule.parser", "check-json", "-"],
      {
        cwd: runtimeRoot.cwd,
        env: {
          ...process.env,
          ...runtimeRoot.env,
          PYTHONDONTWRITEBYTECODE: "1",
        },
      }
    );

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });

    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });

    child.on("error", (error) => {
      if (document.isClosed || document.version !== expectedVersion) {
        return;
      }

      diagnostics.set(document.uri, [
        new vscode.Diagnostic(
          fallbackRange(document),
          `Djule syntax check failed to start: ${error.message}`,
          vscode.DiagnosticSeverity.Error
        ),
      ]);
    });

    child.on("close", (code) => {
      if (document.isClosed || document.version !== expectedVersion) {
        return;
      }

      const payload = safeParseJson(stdout);
      if (payload && Array.isArray(payload.diagnostics)) {
        diagnostics.set(document.uri, payload.diagnostics.map((item) => toDiagnostic(document, item)));
        return;
      }

      if (code === 0) {
        diagnostics.delete(document.uri);
        return;
      }

      diagnostics.set(document.uri, [
        new vscode.Diagnostic(
          fallbackRange(document),
          stderr.trim() || stdout.trim() || "Djule syntax check failed",
          vscode.DiagnosticSeverity.Error
        ),
      ]);
    });

    child.stdin.end(document.getText());
  }

  context.subscriptions.push(
    vscode.workspace.onDidOpenTextDocument((document) => {
      scheduleValidation(document, 0);
    }),
    vscode.workspace.onDidChangeTextDocument((event) => {
      scheduleValidation(event.document);
    }),
    vscode.workspace.onDidCloseTextDocument((document) => {
      const uriKey = document.uri.toString();
      clearPending(uriKey);
      validationVersions.delete(uriKey);
      diagnostics.delete(document.uri);
    })
  );

  for (const document of vscode.workspace.textDocuments) {
    scheduleValidation(document, 0);
  }
}

function provideDjuleCompletions(document, position, context, configuration) {
  const linePrefix = document.lineAt(position.line).text.slice(0, position.character);
  const runtimeRoot = resolveRuntimeRoot(document, context, configuration);
  const symbols = collectDocumentSymbols(document, runtimeRoot.cwd);
  const importItems = buildImportCompletions(linePrefix, document, position, runtimeRoot.cwd);

  if (importItems) {
    return importItems;
  }

  if (isComponentAttributeContext(linePrefix)) {
    return buildAttributeCompletions(linePrefix, document, position, symbols);
  }

  if (isTagContext(linePrefix)) {
    return buildTagCompletions(linePrefix, document, position, symbols);
  }

  return buildKeywordAndSnippetCompletions(document, position);
}

function safeParseJson(value) {
  try {
    return JSON.parse(value);
  } catch (_error) {
    return null;
  }
}

function toDiagnostic(document, item) {
  const message = typeof item.message === "string" ? item.message : "Djule syntax error";
  const severity = toSeverity(item.severity);
  const range = diagnosticRange(document, item.line, item.column, item.endColumn);
  const diagnostic = new vscode.Diagnostic(range, message, severity);

  if (typeof item.code === "string") {
    diagnostic.code = item.code;
  }

  diagnostic.source = DIAGNOSTIC_SOURCE;
  return diagnostic;
}

function toSeverity(value) {
  if (value === "warning") {
    return vscode.DiagnosticSeverity.Warning;
  }
  if (value === "information") {
    return vscode.DiagnosticSeverity.Information;
  }
  if (value === "hint") {
    return vscode.DiagnosticSeverity.Hint;
  }
  return vscode.DiagnosticSeverity.Error;
}

function diagnosticRange(document, lineNumber, columnNumber, endColumnNumber) {
  if (document.lineCount === 0) {
    return new vscode.Range(0, 0, 0, 0);
  }

  const safeLine = Math.max(0, Math.min(document.lineCount - 1, (Number(lineNumber) || 1) - 1));
  const line = document.lineAt(safeLine);
  const safeColumn = Math.max(0, (Number(columnNumber) || 1) - 1);
  const startChar = Math.min(safeColumn, line.text.length);
  const requestedEndChar = Number.isFinite(Number(endColumnNumber))
    ? Math.max(startChar + 1, Number(endColumnNumber) - 1)
    : startChar + 1;
  const endChar = Math.min(line.text.length, requestedEndChar);

  return new vscode.Range(safeLine, startChar, safeLine, endChar);
}

function fallbackRange(document) {
  return diagnosticRange(document, 1, 1);
}

function isTagContext(linePrefix) {
  return /<\/?[A-Za-z0-9_.]*$/.test(linePrefix);
}

function isComponentAttributeContext(linePrefix) {
  return /<[A-Z][A-Za-z0-9_.]*[^>]*$/.test(linePrefix) && !/<\/[A-Z][A-Za-z0-9_.]*[^>]*$/.test(linePrefix);
}

function buildImportCompletions(linePrefix, document, position, runtimeRoot) {
  const fromImportNamesMatch = linePrefix.match(/^\s*from\s+([.\w]+)\s+import\s+([A-Za-z0-9_,\s]*)$/);
  if (fromImportNamesMatch) {
    const moduleName = fromImportNamesMatch[1];
    const modulePath = resolveImportedModulePath(document, moduleName, runtimeRoot);
    if (!modulePath) {
      return [];
    }
    const importFragmentInfo = importNameFragmentRange(linePrefix, position);
    const signatures = loadModuleComponentSignatures(modulePath);
    return Array.from(signatures.keys()).map((name) => {
      const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Class);
      item.insertText = name;
      item.detail = `Djule component from ${moduleName}`;
      item.range = importFragmentInfo.range;
      item.filterText = name;
      return item;
    });
  }

  const fromModuleMatch = linePrefix.match(/^\s*from\s+([.\w]*)$/);
  if (fromModuleMatch) {
    const modulePrefix = fromModuleMatch[1] || "";
    return buildModulePathCompletions(document, position, runtimeRoot, modulePrefix);
  }

  const bareImportMatch = linePrefix.match(/^\s*import\s+([.\w]*)$/);
  if (bareImportMatch) {
    const modulePrefix = bareImportMatch[1] || "";
    return buildModulePathCompletions(document, position, runtimeRoot, modulePrefix);
  }

  return null;
}

function buildModulePathCompletions(document, position, runtimeRoot, modulePrefix) {
  const moduleNames = listDjuleModules(document, runtimeRoot, modulePrefix);
  const range = moduleFragmentRange(position, modulePrefix);
  return moduleNames.map((moduleName) => {
    const item = new vscode.CompletionItem(moduleName, vscode.CompletionItemKind.Module);
    item.insertText = moduleName;
    item.detail = "Djule module segment";
    item.range = range;
    item.filterText = moduleName;
    return item;
  });
}

function buildTagCompletions(linePrefix, document, position, symbols) {
  const items = [];
  const namespaceMatch = linePrefix.match(/<\/?([A-Za-z_][A-Za-z0-9_.]*)\.([A-Za-z0-9_]*)$/);

  if (namespaceMatch) {
    const namespace = namespaceMatch[1];
    const partial = namespaceMatch[2] || "";
    const range = replaceTailRange(position, partial.length);
    const moduleComponents = symbols.namespacedModules.get(namespace);
    if (moduleComponents) {
      for (const [name] of moduleComponents) {
        items.push(componentCompletionItem(name, range, namespace));
      }
    }
    return items;
  }

  const tagMatch = linePrefix.match(/<\/?([A-Za-z0-9_.]*)$/);
  const partial = tagMatch ? tagMatch[1] : "";
  const range = replaceTailRange(position, partial.length);

  for (const tag of HTML_TAGS) {
    const item = new vscode.CompletionItem(tag, vscode.CompletionItemKind.Keyword);
    item.insertText = tag;
    item.detail = "HTML tag";
    item.range = range;
    item.filterText = tag;
    items.push(item);
  }

  for (const [name] of symbols.components) {
    items.push(componentCompletionItem(name, range));
  }

  for (const namespace of symbols.namespacedModules.keys()) {
    const item = new vscode.CompletionItem(namespace, vscode.CompletionItemKind.Module);
    item.insertText = namespace;
    item.detail = "Djule module namespace";
    item.range = range;
    item.filterText = namespace;
    items.push(item);
  }

  return items;
}

function buildAttributeCompletions(linePrefix, document, position, symbols) {
  const match = linePrefix.match(/<([A-Z][A-Za-z0-9_.]*)(?:\s+[^>]*)?$/);
  if (!match) {
    return [];
  }

  const componentName = match[1];
  const signature = lookupComponentSignature(componentName, symbols);
  if (!signature) {
    return [];
  }

  const attributeMatch = linePrefix.match(/\s+([A-Za-z_][A-Za-z0-9_]*)?$/);
  const partial = attributeMatch ? (attributeMatch[1] || "") : "";
  const range = replaceTailRange(position, partial.length);

  return signature
    .filter((param) => param && param !== "children")
    .map((param) => {
      const item = new vscode.CompletionItem(param, vscode.CompletionItemKind.Property);
      item.insertText = new vscode.SnippetString(`${param}={$1}`);
      item.detail = `Prop on ${componentName}`;
      item.range = range;
      item.filterText = param;
      return item;
    });
}

function buildKeywordAndSnippetCompletions(document, position) {
  const items = [];
  const wordRange = currentWordRange(document, position);
  const snippets = [
    {
      label: "def component",
      insertText: new vscode.SnippetString("def ${1:Page}(${2:props}):\n    return (\n        $0\n    )"),
      detail: "Djule component definition",
    },
    {
      label: "if block",
      insertText: new vscode.SnippetString("if ${1:condition}:\n    $0"),
      detail: "Python if block",
    },
    {
      label: "for block",
      insertText: new vscode.SnippetString("for ${1:item} in ${2:items}:\n    $0"),
      detail: "Python for block",
    },
    {
      label: "return markup",
      insertText: new vscode.SnippetString("return (\n    $0\n)"),
      detail: "Djule return block",
    },
  ];

  for (const snippet of snippets) {
    const item = new vscode.CompletionItem(snippet.label, vscode.CompletionItemKind.Snippet);
    item.insertText = snippet.insertText;
    item.detail = snippet.detail;
    if (wordRange) {
      item.range = wordRange;
    }
    items.push(item);
  }

  for (const keyword of ["from", "import", "as", "def", "return", "if", "else", "for", "in"]) {
    const item = new vscode.CompletionItem(keyword, vscode.CompletionItemKind.Keyword);
    item.insertText = keyword;
    if (wordRange) {
      item.range = wordRange;
    }
    items.push(item);
  }

  return items;
}

function componentCompletionItem(name, range, namespace = null) {
  const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Class);
  item.insertText = name;
  item.detail = namespace ? `Djule component from ${namespace}` : "Djule component";
  item.range = range;
  item.filterText = name;
  return item;
}

function currentWordRange(document, position) {
  return document.getWordRangeAtPosition(position, /[A-Za-z_][A-Za-z0-9_]*/);
}

function replaceTailRange(position, typedLength) {
  const safeLength = Math.max(0, typedLength);
  const startCharacter = Math.max(0, position.character - safeLength);
  return new vscode.Range(position.line, startCharacter, position.line, position.character);
}

function moduleFragmentRange(position, modulePrefix) {
  const fragment = modulePrefix.includes(".")
    ? modulePrefix.slice(modulePrefix.lastIndexOf(".") + 1)
    : modulePrefix;
  return replaceTailRange(position, fragment.length);
}

function importNameFragmentRange(linePrefix, position) {
  const importKeywordIndex = linePrefix.lastIndexOf(" import ");
  const commaIndex = linePrefix.lastIndexOf(",");
  const startBase = commaIndex > importKeywordIndex ? commaIndex + 1 : importKeywordIndex + " import ".length;
  let startCharacter = startBase;
  while (startCharacter < linePrefix.length && /\s/.test(linePrefix[startCharacter])) {
    startCharacter += 1;
  }
  return {
    range: new vscode.Range(position.line, startCharacter, position.line, position.character),
  };
}

function lookupComponentSignature(componentName, symbols) {
  if (componentName.includes(".")) {
    const parts = componentName.split(".");
    const name = parts.pop();
    const namespace = parts.join(".");
    const moduleComponents = symbols.namespacedModules.get(namespace);
    return moduleComponents ? moduleComponents.get(name) : null;
  }
  return symbols.components.get(componentName) || null;
}

function collectDocumentSymbols(document, runtimeRoot) {
  const source = document.getText();
  const components = extractComponentSignatures(source);
  const importedComponents = extractImportedComponents(document, source, runtimeRoot);

  for (const [name, params] of importedComponents.directComponents) {
    components.set(name, params);
  }

  return {
    components,
    namespacedModules: importedComponents.namespacedModules,
  };
}

function extractComponentSignatures(source) {
  const components = new Map();
  for (const match of source.matchAll(COMPONENT_DEF_RE)) {
    const name = match[1];
    const params = match[2]
      .split(",")
      .map((param) => param.trim())
      .filter(Boolean);
    components.set(name, params);
  }
  return components;
}

function extractImportedComponents(document, source, runtimeRoot) {
  const directComponents = new Map();
  const namespacedModules = new Map();

  for (const match of source.matchAll(IMPORT_FROM_RE)) {
    const moduleName = match[1];
    const importedNames = match[2].split(",").map((name) => name.trim()).filter(Boolean);
    const modulePath = resolveImportedModulePath(document, moduleName, runtimeRoot);
    if (!modulePath) {
      continue;
    }
    const signatures = loadModuleComponentSignatures(modulePath);
    for (const importedName of importedNames) {
      if (signatures.has(importedName)) {
        directComponents.set(importedName, signatures.get(importedName));
      }
    }
  }

  for (const match of source.matchAll(IMPORT_MODULE_RE)) {
    const moduleName = match[1];
    const alias = match[2] || moduleName;
    const modulePath = resolveImportedModulePath(document, moduleName, runtimeRoot);
    if (!modulePath) {
      continue;
    }
    namespacedModules.set(alias, loadModuleComponentSignatures(modulePath));
  }

  return { directComponents, namespacedModules };
}

function listDjuleModules(document, runtimeRoot, modulePrefix) {
  if (!modulePrefix) {
    return [];
  }

  if (modulePrefix.startsWith(".")) {
    return listRelativeDjuleModules(document, modulePrefix);
  }

  return listAbsoluteDjuleModules(runtimeRoot, modulePrefix);
}

function listAbsoluteDjuleModules(runtimeRoot, modulePrefix) {
  const modules = collectDjuleModulesUnderRoot(runtimeRoot);
  return nextModuleSegments(modules, modulePrefix);
}

function listRelativeDjuleModules(document, modulePrefix) {
  if (document.uri.scheme !== "file") {
    return [];
  }

  const leadingDotsMatch = modulePrefix.match(/^\.+/);
  if (!leadingDotsMatch) {
    return [];
  }

  const leadingDots = leadingDotsMatch[0].length;
  const remainder = modulePrefix.slice(leadingDots);
  let baseDir = path.dirname(document.uri.fsPath);
  for (let index = 1; index < leadingDots; index += 1) {
    baseDir = path.dirname(baseDir);
  }

  const modules = collectDjuleModulesUnderRoot(baseDir);
  return nextModuleSegments(modules, remainder);
}

function collectDjuleModulesUnderRoot(rootDir) {
  const resolvedRoot = safeResolve(rootDir);
  if (!resolvedRoot || !fs.existsSync(resolvedRoot) || !fs.statSync(resolvedRoot).isDirectory()) {
    return [];
  }

  const results = new Set();

  function walk(currentDir) {
    for (const entry of fs.readdirSync(currentDir, { withFileTypes: true })) {
      if (entry.name.startsWith(".") || entry.name === "__pycache__") {
        continue;
      }

      const entryPath = path.join(currentDir, entry.name);
      if (entry.isDirectory()) {
        walk(entryPath);
        continue;
      }

      if (!entry.isFile() || !entry.name.endsWith(".djule")) {
        continue;
      }

      const relativePath = path.relative(resolvedRoot, entryPath);
      if (!relativePath || relativePath.startsWith("..")) {
        continue;
      }

      if (relativePath.endsWith(`${path.sep}__init__.djule`)) {
        const moduleName = relativePath
          .slice(0, -`${path.sep}__init__.djule`.length)
          .split(path.sep)
          .join(".");
        if (moduleName) {
          results.add(moduleName);
        }
        continue;
      }

      const moduleName = relativePath
        .slice(0, -".djule".length)
        .split(path.sep)
        .join(".");
      if (moduleName) {
        results.add(moduleName);
      }
    }
  }

  walk(resolvedRoot);
  return Array.from(results).sort();
}

function nextModuleSegments(modules, modulePrefix) {
  const normalizedPrefix = modulePrefix || "";
  const endsWithDot = normalizedPrefix.endsWith(".");
  const prefixParts = normalizedPrefix.split(".");
  const completedParts = endsWithDot
    ? prefixParts.filter(Boolean)
    : prefixParts.slice(0, -1).filter(Boolean);
  const partial = endsWithDot ? "" : (prefixParts[prefixParts.length - 1] || "");
  const suggestions = new Set();

  for (const moduleName of modules) {
    const moduleParts = moduleName.split(".");
    if (moduleParts.length <= completedParts.length) {
      continue;
    }

    let matches = true;
    for (let index = 0; index < completedParts.length; index += 1) {
      if (moduleParts[index] !== completedParts[index]) {
        matches = false;
        break;
      }
    }

    if (!matches) {
      continue;
    }

    const nextSegment = moduleParts[completedParts.length];
    if (!partial || nextSegment.startsWith(partial)) {
      suggestions.add(nextSegment);
    }
  }

  return Array.from(suggestions).sort();
}

function loadModuleComponentSignatures(modulePath) {
  const resolved = safeResolve(modulePath);
  if (!resolved || !fs.existsSync(resolved)) {
    return new Map();
  }

  const stats = fs.statSync(resolved);
  const cached = moduleSignatureCache.get(resolved);
  if (cached && cached.mtimeMs === stats.mtimeMs && cached.size === stats.size) {
    return cached.signatures;
  }

  const signatures = extractComponentSignatures(fs.readFileSync(resolved, "utf8"));
  moduleSignatureCache.set(resolved, {
    mtimeMs: stats.mtimeMs,
    size: stats.size,
    signatures,
  });
  return signatures;
}

function resolveImportedModulePath(document, moduleName, runtimeRoot) {
  if (moduleName.startsWith(".")) {
    if (document.uri.scheme !== "file") {
      return null;
    }
    const leadingDots = moduleName.match(/^\.+/)[0].length;
    const remainder = moduleName.slice(leadingDots);
    const moduleParts = remainder ? remainder.split(".") : [];
    let baseDir = path.dirname(document.uri.fsPath);
    for (let index = 1; index < leadingDots; index += 1) {
      baseDir = path.dirname(baseDir);
    }
    const fileCandidate = moduleParts.length
      ? path.join(baseDir, ...moduleParts) + ".djule"
      : path.join(baseDir, "__init__.djule");
    const packageCandidate = moduleParts.length
      ? path.join(baseDir, ...moduleParts, "__init__.djule")
      : null;
    if (fs.existsSync(fileCandidate)) {
      return fileCandidate;
    }
    if (packageCandidate && fs.existsSync(packageCandidate)) {
      return packageCandidate;
    }
    return null;
  }

  const moduleParts = moduleName.split(".");
  const fileCandidate = path.join(runtimeRoot, ...moduleParts) + ".djule";
  const packageCandidate = path.join(runtimeRoot, ...moduleParts, "__init__.djule");
  if (fs.existsSync(fileCandidate)) {
    return fileCandidate;
  }
  if (fs.existsSync(packageCandidate)) {
    return packageCandidate;
  }
  return null;
}

function resolveRuntimeRoot(document, context, configuration) {
  const configuredRoot = configuration.get("projectRoot", "").trim();
  const candidates = [];

  if (configuredRoot) {
    candidates.push(configuredRoot);
  }

  if (document.uri.scheme === "file") {
    let currentDir = path.dirname(document.uri.fsPath);
    while (true) {
      candidates.push(currentDir);
      const parent = path.dirname(currentDir);
      if (parent === currentDir) {
        break;
      }
      currentDir = parent;
    }
  }

  const workspaceFolder = vscode.workspace.getWorkspaceFolder(document.uri);
  if (workspaceFolder) {
    candidates.push(workspaceFolder.uri.fsPath);
  }

  candidates.push(path.resolve(context.extensionPath, "..", ".."));
  candidates.push(process.cwd());

  const seen = new Set();
  for (const candidate of candidates) {
    const resolved = safeResolve(candidate);
    if (!resolved || seen.has(resolved)) {
      continue;
    }
    seen.add(resolved);

    if (looksLikeDjuleProjectRoot(resolved)) {
      return {
        cwd: resolved,
        env: {
          PYTHONPATH: withPythonPathPrepended(resolved),
        },
      };
    }
  }

  const fallback = workspaceFolder ? workspaceFolder.uri.fsPath : process.cwd();
  return {
    cwd: fallback,
    env: {},
  };
}

function looksLikeDjuleProjectRoot(candidate) {
  return (
    fs.existsSync(path.join(candidate, "djule", "__init__.py")) &&
    fs.existsSync(path.join(candidate, "djule", "parser", "__main__.py"))
  );
}

function withPythonPathPrepended(root) {
  const existing = process.env.PYTHONPATH;
  if (!existing) {
    return root;
  }
  return `${root}${path.delimiter}${existing}`;
}

function safeResolve(candidate) {
  try {
    return path.resolve(candidate);
  } catch (_error) {
    return null;
  }
}

function deactivate() {}

module.exports = {
  activate,
  deactivate,
};
