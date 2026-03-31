const fs = require("fs");
const path = require("path");
const vscode = require("vscode");

const {
  inferDocumentImportRoots,
  resolveImportRoots,
  resolveImportedModulePath,
  resolveRuntimeRoot,
} = require("./runtime");
const { COMPONENT_DEF_RE, IMPORT_FROM_RE, IMPORT_MODULE_RE } = require("./constants");

function provideDjuleDefinition(document, position, context, configuration) {
  if (!document || document.languageId !== "djule" || document.uri?.scheme !== "file") {
    return null;
  }

  const source = document.getText();
  const runtimeRoot = resolveRuntimeRoot(document, context, configuration);
  const importRoots = resolveImportRoots([
    ...inferDocumentImportRoots(document, source),
    ...resolveImportRoots(runtimeRoot),
  ]);
  const resolved = resolveDjuleDefinitionTarget(document.uri.fsPath, source, document.offsetAt(position), importRoots);
  if (!resolved) {
    return null;
  }

  return new vscode.Location(
    vscode.Uri.file(resolved.filePath),
    new vscode.Position(resolved.line, resolved.character)
  );
}

function resolveDjuleDefinitionTarget(filePath, source, offset, importRoots = []) {
  const localDefinitions = extractComponentDefinitions(source, filePath);
  const currentIdentifier = identifierAtOffset(source, offset);
  if (!currentIdentifier) {
    return null;
  }

  const dottedReference = dottedReferenceAtOffset(source, offset);
  if (dottedReference) {
    const dottedTarget = resolveDottedReferenceTarget(
      dottedReference.text,
      source,
      filePath,
      importRoots,
      localDefinitions
    );
    if (dottedTarget) {
      return dottedTarget;
    }
  }

  const localTarget = localDefinitions.get(currentIdentifier);
  if (localTarget) {
    return localTarget;
  }

  const importedDirectTarget = resolveDirectImportTarget(currentIdentifier, source, filePath, importRoots);
  if (importedDirectTarget) {
    return importedDirectTarget;
  }

  return resolveModuleAliasTarget(currentIdentifier, source, filePath, importRoots);
}

function extractComponentDefinitions(source, filePath) {
  const definitions = new Map();
  for (const match of source.matchAll(COMPONENT_DEF_RE)) {
    const name = match[1];
    const matchIndex = match.index ?? 0;
    const nameOffset = source.indexOf(name, matchIndex);
    definitions.set(name, {
      character: columnFromOffset(source, nameOffset),
      filePath,
      line: lineFromOffset(source, nameOffset),
    });
  }
  return definitions;
}

function resolveDirectImportTarget(identifier, source, filePath, importRoots) {
  for (const imported of extractFromImports(source)) {
    if (imported.moduleName === "builtins" || !imported.importedNames.includes(identifier)) {
      continue;
    }
    const modulePath = resolveImportedModulePath(fakeDocument(filePath), imported.moduleName, importRoots);
    if (!modulePath) {
      continue;
    }
    const target = componentDefinitionFromFile(modulePath, identifier);
    if (target) {
      return target;
    }
  }
  return null;
}

function resolveModuleAliasTarget(identifier, source, filePath, importRoots) {
  for (const imported of extractModuleImports(source)) {
    if (imported.alias !== identifier) {
      continue;
    }
    if (imported.moduleName === "builtins") {
      return null;
    }
    const modulePath = resolveImportedModulePath(fakeDocument(filePath), imported.moduleName, importRoots);
    if (!modulePath) {
      continue;
    }
    return {
      character: 0,
      filePath: modulePath,
      line: 0,
    };
  }
  return null;
}

function resolveDottedReferenceTarget(reference, source, filePath, importRoots, localDefinitions) {
  const parts = reference.split(".");
  if (parts.length < 2) {
    return null;
  }

  const memberName = parts[parts.length - 1];
  const namespace = parts.slice(0, -1).join(".");

  const namespaceTarget = resolveModuleAliasTarget(namespace, source, filePath, importRoots);
  if (!namespaceTarget) {
    return null;
  }

  if (!memberName) {
    return namespaceTarget;
  }

  const componentTarget = componentDefinitionFromFile(namespaceTarget.filePath, memberName);
  if (componentTarget) {
    return componentTarget;
  }

  return localDefinitions.get(memberName) || null;
}

function componentDefinitionFromFile(modulePath, componentName) {
  if (!modulePath || !fs.existsSync(modulePath)) {
    return null;
  }

  const source = fs.readFileSync(modulePath, "utf8");
  return extractComponentDefinitions(source, path.resolve(modulePath)).get(componentName) || null;
}

function extractFromImports(source) {
  const results = [];
  for (const match of source.matchAll(IMPORT_FROM_RE)) {
    results.push({
      importedNames: match[2].split(",").map((name) => name.trim()).filter(Boolean),
      moduleName: match[1],
    });
  }
  return results;
}

function extractModuleImports(source) {
  const results = [];
  for (const match of source.matchAll(IMPORT_MODULE_RE)) {
    results.push({
      alias: match[2] || match[1].split(".")[0],
      moduleName: match[1],
    });
  }
  return results;
}

function identifierAtOffset(source, offset) {
  if (offset < 0 || offset > source.length) {
    return "";
  }

  let start = offset;
  let end = offset;

  while (start > 0 && /[A-Za-z0-9_]/.test(source[start - 1])) {
    start -= 1;
  }
  while (end < source.length && /[A-Za-z0-9_]/.test(source[end])) {
    end += 1;
  }

  const identifier = source.slice(start, end);
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(identifier) ? identifier : "";
}

function dottedReferenceAtOffset(source, offset) {
  if (offset < 0 || offset > source.length) {
    return null;
  }

  let start = offset;
  let end = offset;

  while (start > 0 && /[A-Za-z0-9_.]/.test(source[start - 1])) {
    start -= 1;
  }
  while (end < source.length && /[A-Za-z0-9_.]/.test(source[end])) {
    end += 1;
  }

  const text = source.slice(start, end);
  if (!/^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$/.test(text)) {
    return null;
  }

  return { start, end, text };
}

function lineFromOffset(source, offset) {
  return source.slice(0, offset).split("\n").length - 1;
}

function columnFromOffset(source, offset) {
  const lastNewline = source.lastIndexOf("\n", Math.max(0, offset - 1));
  return offset - (lastNewline + 1);
}

function fakeDocument(filePath) {
  return {
    uri: {
      fsPath: filePath,
      scheme: "file",
    },
  };
}

module.exports = {
  provideDjuleDefinition,
  resolveDjuleDefinitionTarget,
};
