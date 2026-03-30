const vscode = require("vscode");

const { HTML_TAGS } = require("./constants");
const { createDiagnosticsServerPool } = require("./diagnostics_server");
const {
  mergeGlobalSymbols,
  parseConfiguredGlobals,
  parseGlobalSchema,
  resolveConfiguredGlobalMembers,
} = require("./globals");
const {
  listDjuleModules,
  resolveImportedModulePath,
  resolvePythonCommand,
  resolveRuntimeRoot,
} = require("./runtime");
const {
  collectCodeNames,
  collectDocumentSymbols,
  loadModuleComponentSignatures,
  lookupComponentSignature,
} = require("./symbols");

async function provideDjuleCompletions(document, position, context, configuration) {
  try {
    const linePrefix = document.lineAt(position.line).text.slice(0, position.character);
    const runtimeRoot = resolveRuntimeRoot(document, context, configuration);
    const symbols = collectDocumentSymbols(document, runtimeRoot.cwd);
    const globalSymbols = await resolveGlobalSymbols(document, context, configuration, runtimeRoot);
    const importItems = buildImportCompletions(linePrefix, document, position, runtimeRoot.cwd);

    if (importItems !== null) {
      return importItems;
    }

    if (isComponentAttributeContext(linePrefix)) {
      return buildAttributeCompletions(linePrefix, position, symbols);
    }

    if (isTagContext(linePrefix)) {
      return buildTagCompletions(linePrefix, position, symbols);
    }

    if (isMemberAccessContext(linePrefix)) {
      return buildMemberAccessCompletions(linePrefix, position, symbols, globalSymbols);
    }

    const codeItems = buildCodeCompletions(document, position, symbols, globalSymbols);
    if (codeItems !== null) {
      return codeItems;
    }
  } catch (_error) {
    // Fall back to generic Djule keywords/snippets instead of failing silently.
  }

  return buildKeywordAndSnippetCompletions(document, position);
}

async function resolveGlobalSymbols(document, context, configuration, runtimeRoot) {
  const configuredGlobals = parseConfiguredGlobals(configuration);

  try {
    const discoveredGlobals = await discoverDjangoGlobals(document, context, configuration, runtimeRoot);
    return mergeGlobalSymbols(configuredGlobals, discoveredGlobals);
  } catch (_error) {
    return configuredGlobals;
  }
}

async function discoverDjangoGlobals(document, context, configuration, runtimeRoot) {
  if (document.uri.scheme !== "file") {
    return new Map();
  }

  const pythonCommand = await resolvePythonCommand(document, configuration);
  const serverPool = createDiagnosticsServerPool();
  const server = serverPool.getServer(pythonCommand, runtimeRoot);
  const workspaceFolder = vscode.workspace.getWorkspaceFolder(document.uri);
  const payload = await server.discoverDjangoGlobals(document, {
    settingsModule: normalizeConfiguredString(configuration.get("djangoSettingsModule", "")),
    workspacePath: workspaceFolder ? workspaceFolder.uri.fsPath : "",
  });

  if (!payload || !payload.ok || typeof payload.globals !== "object" || payload.globals === null) {
    return new Map();
  }

  return parseGlobalSchema(payload.globals);
}

function normalizeConfiguredString(value) {
  return typeof value === "string" ? value.trim() : "";
}

function isTagContext(linePrefix) {
  return /<\/?[A-Za-z0-9_.]*$/.test(linePrefix);
}

function isComponentAttributeContext(linePrefix) {
  return /<[A-Z][A-Za-z0-9_.]*[^>]*$/.test(linePrefix) && !/<\/[A-Z][A-Za-z0-9_.]*[^>]*$/.test(linePrefix);
}

function isMemberAccessContext(linePrefix) {
  return /\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\.[A-Za-z_0-9]*$/.test(linePrefix);
}

function buildImportCompletions(linePrefix, document, position, runtimeRoot) {
  if (/^\s*from\s*$/.test(linePrefix) || /^\s*import\s*$/.test(linePrefix)) {
    return [];
  }

  if (/^\s*from\b/.test(linePrefix) || /^\s*import\b/.test(linePrefix)) {
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
      return buildModulePathCompletions(document, position, runtimeRoot, fromModuleMatch[1] || "");
    }

    const bareImportMatch = linePrefix.match(/^\s*import\s+([.\w]*)$/);
    if (bareImportMatch) {
      return buildModulePathCompletions(document, position, runtimeRoot, bareImportMatch[1] || "");
    }

    return [];
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

function buildTagCompletions(linePrefix, position, symbols) {
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

  for (const namespace of symbols.namespacedModules.keys()) {
    if (partial && !namespace.startsWith(partial)) {
      continue;
    }
    const item = new vscode.CompletionItem(namespace, vscode.CompletionItemKind.Module);
    item.insertText = `${namespace}.`;
    item.detail = "Djule module namespace";
    item.range = range;
    item.filterText = namespace;
    item.sortText = `0-${namespace}`;
    item.command = {
      title: "Trigger Suggestions",
      command: "editor.action.triggerSuggest",
    };
    items.push(item);
  }

  for (const [name] of symbols.components) {
    if (partial && !name.startsWith(partial)) {
      continue;
    }
    const item = componentCompletionItem(name, range);
    item.sortText = `1-${name}`;
    items.push(item);
  }

  for (const tag of HTML_TAGS) {
    if (partial && !tag.startsWith(partial)) {
      continue;
    }
    const item = new vscode.CompletionItem(tag, vscode.CompletionItemKind.Keyword);
    item.insertText = tag;
    item.detail = "HTML tag";
    item.range = range;
    item.filterText = tag;
    item.sortText = `2-${tag}`;
    items.push(item);
  }

  return items;
}

function buildCodeCompletions(document, position, symbols, globalSymbols) {
  const wordRange = currentWordRange(document, position);
  if (!wordRange) {
    return null;
  }

  const currentPrefix = document.getText(wordRange);
  if (!currentPrefix) {
    return null;
  }

  const availableNames = collectCodeNames(document, position, symbols);
  const items = Array.from(globalSymbols.entries())
    .filter(([name]) => name.startsWith(currentPrefix) && name !== currentPrefix)
    .sort(([leftName], [rightName]) => leftName.localeCompare(rightName))
    .map(([name, globalInfo]) => {
      const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Constant);
      item.insertText = name;
      item.range = wordRange;
      item.filterText = name;
      item.detail = globalInfo.detail || "Djule configured global";
      if (globalInfo.detail) {
        item.documentation = globalInfo.detail;
      }
      item.sortText = `0-${name}`;
      return item;
    });

  items.push(...Array.from(availableNames)
    .filter((name) => name.startsWith(currentPrefix) && name !== currentPrefix)
    .filter((name) => !globalSymbols.has(name))
    .sort()
    .map((name) => {
      const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Variable);
      item.insertText = name;
      item.range = wordRange;
      item.filterText = name;
      item.detail = "Djule local name";
      item.sortText = `1-${name}`;
      return item;
    }));

  return items.length ? items : null;
}

function buildMemberAccessCompletions(linePrefix, position, symbols, globalSymbols) {
  const match = linePrefix.match(/\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\.([A-Za-z0-9_]*)$/);
  if (!match) {
    return [];
  }

  const namespace = match[1];
  const partial = match[2] || "";
  const range = replaceTailRange(position, partial.length);
  const moduleComponents = symbols.namespacedModules.get(namespace);
  if (!moduleComponents) {
    const globalMembers = resolveConfiguredGlobalMembers(globalSymbols, namespace.split("."));
    if (!globalMembers) {
      return [];
    }

    return Array.from(globalMembers.entries())
      .filter(([name]) => !partial || name.startsWith(partial))
      .sort(([leftName], [rightName]) => leftName.localeCompare(rightName))
      .map(([name, memberInfo]) => {
        const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Property);
        item.insertText = name;
        item.detail = memberInfo.detail || `Member on ${namespace}`;
        item.range = range;
        item.filterText = name;
        return item;
      });
  }

  return Array.from(moduleComponents.keys())
    .filter((name) => !partial || name.startsWith(partial))
    .sort()
    .map((name) => {
      const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Class);
      item.insertText = name;
      item.detail = `Djule component from ${namespace}`;
      item.range = range;
      item.filterText = name;
      return item;
    });
}

function buildAttributeCompletions(linePrefix, position, symbols) {
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
  const partial = attributeMatch ? attributeMatch[1] || "" : "";
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

module.exports = {
  provideDjuleCompletions,
};
