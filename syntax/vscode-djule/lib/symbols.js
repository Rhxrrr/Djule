const fs = require("fs");

const {
  ASSIGNMENT_RE,
  COMPONENT_DEF_RE,
  FOR_TARGET_RE,
  IMPORT_FROM_RE,
  IMPORT_MODULE_RE,
} = require("./constants");
const { resolveImportedModulePath } = require("./runtime");

const moduleSignatureCache = new Map();

function collectDocumentSymbols(document, importRoots) {
  const source = document.getText();
  const components = extractComponentSignatures(source);
  const importedComponents = extractImportedComponents(document, source, importRoots);
  const importedNames = extractImportedNames(source);

  for (const [name, params] of importedComponents.directComponents) {
    components.set(name, params);
  }

  return {
      components,
      importedNames,
      namespacedModules: importedComponents.namespacedModules,
  };
}

function collectCodeNames(document, position, symbols) {
  const names = new Set();
  const source = document.getText();
  const componentContext = componentContextAtPosition(document, position, source);

  if (componentContext) {
    for (const param of componentContext.params) {
      names.add(param);
    }

    const componentSourcePrefix = source.slice(componentContext.bodyStartOffset, document.offsetAt(position));
    for (const match of componentSourcePrefix.matchAll(ASSIGNMENT_RE)) {
      names.add(match[1]);
    }
    for (const match of componentSourcePrefix.matchAll(FOR_TARGET_RE)) {
      names.add(match[1]);
    }
  }

  for (const componentName of symbols.components.keys()) {
    names.add(componentName);
  }

  for (const importedName of symbols.importedNames || []) {
    names.add(importedName);
  }

  for (const namespace of symbols.namespacedModules.keys()) {
    names.add(namespace);
  }

  return names;
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

function extractComponentSignatures(source) {
  const components = new Map();
  for (const match of source.matchAll(COMPONENT_DEF_RE)) {
    const name = match[1];
    const params = parseComponentParamNames(match[2]);
    components.set(name, params);
  }
  return components;
}

function loadModuleComponentSignatures(modulePath) {
  const resolved = modulePath ? require("path").resolve(modulePath) : null;
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

function componentContextAtPosition(document, position, source) {
  const currentOffset = document.offsetAt(position);
  const componentMatches = Array.from(source.matchAll(COMPONENT_DEF_RE));

  for (let index = 0; index < componentMatches.length; index += 1) {
    const match = componentMatches[index];
    const startOffset = match.index ?? 0;
    const endOffset = index + 1 < componentMatches.length ? componentMatches[index + 1].index ?? source.length : source.length;

    if (currentOffset < startOffset || currentOffset > endOffset) {
      continue;
    }

    return {
      name: match[1],
      params: parseComponentParamNames(match[2]),
      bodyStartOffset: startOffset,
      endOffset,
    };
  }

  return null;
}

function parseComponentParamNames(rawParams) {
  const params = [];

  for (const entry of splitTopLevelParams(rawParams)) {
    const name = extractParamName(entry);
    if (name) {
      params.push(name);
    }
  }

  return params;
}

function splitTopLevelParams(rawParams) {
  const entries = [];
  let current = "";
  let parenDepth = 0;
  let bracketDepth = 0;
  let braceDepth = 0;
  let quote = "";
  let escaped = false;

  for (const ch of rawParams) {
    current += ch;

    if (quote) {
      if (escaped) {
        escaped = false;
        continue;
      }
      if (ch === "\\") {
        escaped = true;
        continue;
      }
      if (ch === quote) {
        quote = "";
      }
      continue;
    }

    if (ch === "'" || ch === '"') {
      quote = ch;
      continue;
    }

    if (ch === "(") {
      parenDepth += 1;
      continue;
    }
    if (ch === ")") {
      parenDepth = Math.max(0, parenDepth - 1);
      continue;
    }
    if (ch === "[") {
      bracketDepth += 1;
      continue;
    }
    if (ch === "]") {
      bracketDepth = Math.max(0, bracketDepth - 1);
      continue;
    }
    if (ch === "{") {
      braceDepth += 1;
      continue;
    }
    if (ch === "}") {
      braceDepth = Math.max(0, braceDepth - 1);
      continue;
    }

    if (ch === "," && parenDepth === 0 && bracketDepth === 0 && braceDepth === 0) {
      entries.push(current.slice(0, -1).trim());
      current = "";
    }
  }

  if (current.trim()) {
    entries.push(current.trim());
  }

  return entries.filter(Boolean);
}

function extractParamName(entry) {
  const trimmed = entry.trim();
  if (!trimmed) {
    return "";
  }

  const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)/);
  return match ? match[1] : "";
}

function extractImportedComponents(document, source, importRoots) {
  const directComponents = new Map();
  const namespacedModules = new Map();

  for (const match of source.matchAll(IMPORT_FROM_RE)) {
    const moduleName = match[1];
    const importedNames = match[2].split(",").map((name) => name.trim()).filter(Boolean);
    const modulePath = resolveImportedModulePath(document, moduleName, importRoots);
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
    const modulePath = resolveImportedModulePath(document, moduleName, importRoots);
    if (!modulePath) {
      continue;
    }
    namespacedModules.set(alias, loadModuleComponentSignatures(modulePath));
  }

  return { directComponents, namespacedModules };
}

function extractImportedNames(source) {
  const importedNames = new Set();

  for (const match of source.matchAll(IMPORT_FROM_RE)) {
    for (const importedName of match[2].split(",").map((name) => name.trim()).filter(Boolean)) {
      importedNames.add(importedName);
    }
  }

  for (const match of source.matchAll(IMPORT_MODULE_RE)) {
    importedNames.add(match[2] || match[1].split(".")[0]);
  }

  return importedNames;
}

module.exports = {
  collectCodeNames,
  collectDocumentSymbols,
  extractComponentSignatures,
  loadModuleComponentSignatures,
  lookupComponentSignature,
};
