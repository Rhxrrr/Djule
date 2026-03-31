const fs = require("fs");
const path = require("path");
const vscode = require("vscode");

const LIKELY_DJULE_ROOT_NAMES = new Set(["frontend", "templates"]);
const LIKELY_DJULE_CHILD_NAMES = new Set([
  "components",
  "layouts",
  "pages",
  "partials",
  "sections",
  "shared",
]);
const DJANGO_FALLBACK_GLOBALS = {
  csrf_token: {
    detail: "Django CSRF token",
  },
  messages: {
    detail: "Django messages",
  },
  perms: {
    detail: "Django permissions",
  },
  request: {
    detail: "Django request",
    members: {
      path: "str",
      user: {
        detail: "Django user",
      },
    },
  },
  user: {
    detail: "Django user",
  },
};

async function resolvePythonCommand(document, configuration) {
  const configuredCommand = normalizePythonCommand(configuration.get("pythonCommand", ""));
  if (configuredCommand) {
    return configuredCommand;
  }

  const selectedInterpreter = await resolveSelectedPythonInterpreter(document);
  if (selectedInterpreter) {
    return selectedInterpreter;
  }

  const defaultInterpreterPath = normalizePythonCommand(
    vscode.workspace.getConfiguration("python", document).get("defaultInterpreterPath", "")
  );
  if (defaultInterpreterPath && fs.existsSync(defaultInterpreterPath)) {
    return defaultInterpreterPath;
  }

  for (const directory of listRuntimeCandidateDirectories(document)) {
    for (const interpreterPath of possibleInterpreterPaths(directory)) {
      if (fs.existsSync(interpreterPath)) {
        return interpreterPath;
      }
    }
  }

  return "python3";
}

function listDjuleModules(document, importRoots, modulePrefix) {
  if (!modulePrefix) {
    return [];
  }

  if (modulePrefix.startsWith(".")) {
    return listRelativeDjuleModules(document, modulePrefix);
  }

  return listAbsoluteDjuleModules(importRoots, modulePrefix);
}

function resolveImportedModulePath(document, moduleName, importRoots) {
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
  for (const importRoot of resolveImportRoots(importRoots)) {
    const fileCandidate = path.join(importRoot, ...moduleParts) + ".djule";
    const packageCandidate = path.join(importRoot, ...moduleParts, "__init__.djule");
    if (fs.existsSync(fileCandidate)) {
      return fileCandidate;
    }
    if (fs.existsSync(packageCandidate)) {
      return packageCandidate;
    }
  }
  return null;
}

function resolveRuntimeRoot(document, context, configuration) {
  const configuredRoot = configuration.get("projectRoot", "").trim();
  const candidates = listRuntimeCandidateDirectories(document);
  const workspaceFolder = vscode.workspace.getWorkspaceFolder(document.uri);
  if (configuredRoot) {
    candidates.unshift(configuredRoot);
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

  const fallback =
    (workspaceFolder && workspaceFolder.uri.fsPath) ||
    (document.uri.scheme === "file" ? path.dirname(document.uri.fsPath) : process.cwd());
  return {
    cwd: fallback,
    env: {},
  };
}

function inferDocumentImportRoots(document, sourceText = "") {
  if (!document || document.uri?.scheme !== "file") {
    return [];
  }

  return dedupePaths([
    ...inferLikelyTemplateRoots(document.uri.fsPath),
    ...inferImportRootsFromSource(document.uri.fsPath, sourceText),
  ]);
}

function inferDjangoFallbackGlobals(document, configuration) {
  if (!looksLikeDjangoProject(document, configuration)) {
    return {};
  }

  return DJANGO_FALLBACK_GLOBALS;
}

function looksLikeDjangoProject(document, configuration) {
  const configuredSettingsModule =
    typeof configuration?.get === "function" ? configuration.get("djangoSettingsModule", "") : "";
  if (typeof configuredSettingsModule === "string" && configuredSettingsModule.trim()) {
    return true;
  }

  return Boolean(findNearestManagePy(document));
}

function listRuntimeCandidateDirectories(document) {
  const candidates = [];

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

  return dedupePaths(candidates);
}

function inferLikelyTemplateRoots(filePath) {
  const roots = [];

  for (const directory of ancestorDirectories(path.dirname(filePath))) {
    const baseName = path.basename(directory);
    if (LIKELY_DJULE_ROOT_NAMES.has(baseName) || looksLikeTemplateRoot(directory)) {
      roots.push(directory);
    }
  }

  return roots;
}

function looksLikeTemplateRoot(directory) {
  try {
    const entries = fs.readdirSync(directory, { withFileTypes: true });
    let hasDjuleFile = false;
    let hasLikelyChildDirectory = false;

    for (const entry of entries) {
      if (entry.name.startsWith(".")) {
        continue;
      }
      if (entry.isFile() && entry.name.endsWith(".djule")) {
        hasDjuleFile = true;
      }
      if (entry.isDirectory() && LIKELY_DJULE_CHILD_NAMES.has(entry.name)) {
        hasLikelyChildDirectory = true;
      }
      if (hasDjuleFile || hasLikelyChildDirectory) {
        return true;
      }
    }
  } catch (_error) {
    return false;
  }

  return false;
}

function inferImportRootsFromSource(filePath, sourceText) {
  const absoluteModules = extractAbsoluteImportModules(sourceText);
  if (!absoluteModules.length) {
    return [];
  }

  const roots = [];
  for (const directory of ancestorDirectories(path.dirname(filePath))) {
    for (const moduleName of absoluteModules) {
      if (moduleExistsUnderRoot(directory, moduleName) || topLevelImportPathExists(directory, moduleName)) {
        roots.push(directory);
        break;
      }
    }
  }

  return roots;
}

function extractAbsoluteImportModules(sourceText) {
  if (typeof sourceText !== "string" || !sourceText.trim()) {
    return [];
  }

  const modules = new Set();
  const fromImportPattern = /^\s*from\s+([A-Za-z_][\w.]*)\s+import\b/gm;
  const importModulePattern = /^\s*import\s+([A-Za-z_][\w.]*)\b/gm;

  for (const match of sourceText.matchAll(fromImportPattern)) {
    const moduleName = match[1];
    if (moduleName && moduleName !== "builtins") {
      modules.add(moduleName);
    }
  }

  for (const match of sourceText.matchAll(importModulePattern)) {
    const moduleName = match[1];
    if (moduleName && moduleName !== "builtins") {
      modules.add(moduleName);
    }
  }

  return Array.from(modules);
}

function moduleExistsUnderRoot(rootDir, moduleName) {
  const moduleParts = moduleName.split(".");
  const fileCandidate = path.join(rootDir, ...moduleParts) + ".djule";
  const packageCandidate = path.join(rootDir, ...moduleParts, "__init__.djule");
  return fs.existsSync(fileCandidate) || fs.existsSync(packageCandidate);
}

function topLevelImportPathExists(rootDir, moduleName) {
  const topLevel = moduleName.split(".")[0];
  if (!topLevel) {
    return false;
  }
  return fs.existsSync(path.join(rootDir, topLevel));
}

function listAbsoluteDjuleModules(importRoots, modulePrefix) {
  const modules = new Set();

  for (const importRoot of resolveImportRoots(importRoots)) {
    for (const moduleName of collectDjuleModulesUnderRoot(importRoot)) {
      modules.add(moduleName);
    }
  }

  return nextModuleSegments(Array.from(modules), modulePrefix);
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

function resolveImportRoots(importRoots) {
  if (Array.isArray(importRoots)) {
    return dedupePaths(importRoots);
  }

  if (typeof importRoots === "string") {
    return dedupePaths([importRoots]);
  }

  if (importRoots && typeof importRoots === "object") {
    return dedupePaths([
      ...normalizeSearchPaths(importRoots.searchPaths),
      importRoots.cwd,
    ]);
  }

  return [];
}

function normalizeSearchPaths(searchPaths) {
  if (!Array.isArray(searchPaths)) {
    return [];
  }

  return searchPaths.filter((searchPath) => typeof searchPath === "string" && searchPath.trim());
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

async function resolveSelectedPythonInterpreter(document) {
  const pythonExtension = vscode.extensions.getExtension("ms-python.python");
  if (!pythonExtension) {
    return "";
  }

  try {
    await pythonExtension.activate();
  } catch (_error) {
    // Fall back to the remaining interpreter detection paths.
  }

  const exportedCommand = normalizePythonCommand(
    pythonExtension.exports?.settings?.getExecutionDetails?.(document.uri)?.execCommand
  );
  if (exportedCommand && fs.existsSync(exportedCommand)) {
    return exportedCommand;
  }

  for (const args of [[document.uri], []]) {
    try {
      const commandValue = normalizePythonCommand(await vscode.commands.executeCommand("python.interpreterPath", ...args));
      if (commandValue && fs.existsSync(commandValue)) {
        return commandValue;
      }
    } catch (_error) {
      // Keep falling back if the command is unavailable.
    }
  }

  return "";
}

function normalizePythonCommand(value) {
  if (!value) {
    return "";
  }

  if (typeof value === "string") {
    return value.trim();
  }

  if (Array.isArray(value)) {
    return typeof value[0] === "string" ? value[0].trim() : "";
  }

  if (typeof value === "object") {
    if (typeof value.path === "string") {
      return value.path.trim();
    }
    if (typeof value.command === "string") {
      return value.command.trim();
    }
    if (Array.isArray(value.command) && typeof value.command[0] === "string") {
      return value.command[0].trim();
    }
  }

  return "";
}

function possibleInterpreterPaths(rootDir) {
  return [
    path.join(rootDir, ".venv", "bin", "python"),
    path.join(rootDir, "venv", "bin", "python"),
    path.join(rootDir, "env", "bin", "python"),
    path.join(rootDir, ".venv", "bin", "python3"),
    path.join(rootDir, "venv", "bin", "python3"),
    path.join(rootDir, "env", "bin", "python3"),
    path.join(rootDir, ".venv", "Scripts", "python.exe"),
    path.join(rootDir, "venv", "Scripts", "python.exe"),
    path.join(rootDir, "env", "Scripts", "python.exe"),
  ];
}

function dedupePaths(candidates) {
  const results = [];
  const seen = new Set();

  for (const candidate of candidates) {
    const resolved = safeResolve(candidate);
    if (!resolved || seen.has(resolved)) {
      continue;
    }
    seen.add(resolved);
    results.push(resolved);
  }

  return results;
}

function ancestorDirectories(startDir) {
  const results = [];
  let currentDir = startDir;

  while (true) {
    results.push(currentDir);
    const parentDir = path.dirname(currentDir);
    if (parentDir === currentDir) {
      break;
    }
    currentDir = parentDir;
  }

  return results;
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

function findNearestManagePy(document) {
  if (!document || document.uri?.scheme !== "file") {
    return "";
  }

  for (const directory of ancestorDirectories(path.dirname(document.uri.fsPath))) {
    const candidate = path.join(directory, "manage.py");
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  return "";
}

module.exports = {
  findNearestManagePy,
  inferDjangoFallbackGlobals,
  inferDocumentImportRoots,
  listDjuleModules,
  looksLikeDjangoProject,
  normalizeSearchPaths,
  resolveImportRoots,
  resolvePythonCommand,
  resolveImportedModulePath,
  resolveRuntimeRoot,
};
