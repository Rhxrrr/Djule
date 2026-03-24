const fs = require("fs");
const path = require("path");
const vscode = require("vscode");

function listDjuleModules(document, runtimeRoot, modulePrefix) {
  if (!modulePrefix) {
    return [];
  }

  if (modulePrefix.startsWith(".")) {
    return listRelativeDjuleModules(document, modulePrefix);
  }

  return listAbsoluteDjuleModules(runtimeRoot, modulePrefix);
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
  const partial = endsWithDot ? "" : prefixParts[prefixParts.length - 1] || "";
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

function looksLikeDjuleProjectRoot(candidate) {
  return (
    (fs.existsSync(path.join(candidate, "src", "djule", "__init__.py")) &&
      fs.existsSync(path.join(candidate, "src", "djule", "parser", "__main__.py"))) ||
    (fs.existsSync(path.join(candidate, "djule", "__init__.py")) &&
      fs.existsSync(path.join(candidate, "djule", "parser", "__main__.py"))) ||
    (fs.existsSync(path.join(candidate, "src", "__init__.py")) &&
      fs.existsSync(path.join(candidate, "src", "parser", "__main__.py")))
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

module.exports = {
  listDjuleModules,
  resolveImportedModulePath,
  resolveRuntimeRoot,
};
