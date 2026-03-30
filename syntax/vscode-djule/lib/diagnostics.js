const vscode = require("vscode");

const { DIAGNOSTIC_DEBOUNCE_MS, DIAGNOSTIC_SOURCE } = require("./constants");
const { createDiagnosticsServerPool } = require("./diagnostics_server");
const {
  configuredGlobalNames,
  mergeGlobalSymbols,
  parseConfiguredGlobals,
  parseGlobalSchema,
} = require("./globals");
const { resolvePythonCommand, resolveRuntimeRoot } = require("./runtime");

function registerDiagnostics(context) {
  const diagnostics = vscode.languages.createDiagnosticCollection(DIAGNOSTIC_SOURCE);
  const pendingTimers = new Map();
  const validationVersions = new Map();
  const serverPool = createDiagnosticsServerPool();

  context.subscriptions.push(diagnostics);
  context.subscriptions.push(serverPool);

  function clearPending(uriKey) {
    const timer = pendingTimers.get(uriKey);
    if (timer) {
      clearTimeout(timer);
      pendingTimers.delete(uriKey);
    }
  }

  function shouldValidate(document) {
    return (
      document &&
      document.languageId === "djule" &&
      vscode.workspace.getConfiguration("djule", document).get("liveSyntax", true)
    );
  }

  async function validateDocument(document, expectedVersion) {
    if (!shouldValidate(document) || document.isClosed || document.version !== expectedVersion) {
      return;
    }

    const configuration = vscode.workspace.getConfiguration("djule", document);
    let payload;
    try {
      const pythonCommand = await resolvePythonCommand(document, configuration);
      const runtimeRoot = resolveRuntimeRoot(document, context, configuration);
      const server = serverPool.getServer(pythonCommand, runtimeRoot);
      const globalNames = await resolveGlobalNames(document, server, configuration);
      payload = await server.checkDocument(document, globalNames);
    } catch (error) {
      if (document.isClosed || document.version !== expectedVersion) {
        return;
      }

      diagnostics.set(document.uri, [
        new vscode.Diagnostic(
          fallbackRange(document),
          `Djule syntax server failed: ${error.message}`,
          vscode.DiagnosticSeverity.Error
        ),
      ]);
      return;
    }

    if (document.isClosed || document.version !== expectedVersion) {
      return;
    }

    if (payload && Array.isArray(payload.diagnostics)) {
      diagnostics.set(document.uri, payload.diagnostics.map((item) => toDiagnostic(document, item)));
      return;
    }

    diagnostics.set(document.uri, [
      new vscode.Diagnostic(
        fallbackRange(document),
        "Djule syntax server returned an invalid response",
        vscode.DiagnosticSeverity.Error
      ),
    ]);
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

async function resolveGlobalNames(document, server, configuration) {
  const configuredGlobals = parseConfiguredGlobals(configuration);

  try {
    const discoveredGlobals = await discoverDjangoGlobals(document, server, configuration);
    return configuredGlobalNames(mergeGlobalSymbols(configuredGlobals, discoveredGlobals));
  } catch (_error) {
    return configuredGlobalNames(configuredGlobals);
  }
}

async function discoverDjangoGlobals(document, server, configuration) {
  if (document.uri.scheme !== "file") {
    return new Map();
  }

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

module.exports = {
  registerDiagnostics,
};
