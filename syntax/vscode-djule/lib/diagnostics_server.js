const cp = require("child_process");

let sharedServerPool = null;

function createDiagnosticsServerPool() {
  if (!sharedServerPool || sharedServerPool.disposed) {
    sharedServerPool = new DjuleDiagnosticsServerPool();
  }
  return sharedServerPool;
}

class DjuleDiagnosticsServerPool {
  constructor() {
    this.servers = new Map();
    this.disposed = false;
  }

  getServer(pythonCommand, runtimeRoot) {
    if (this.disposed) {
      throw new Error("Djule syntax server pool has been disposed");
    }

    const key = JSON.stringify({
      cwd: runtimeRoot.cwd,
      pythonCommand,
      pythonPath: runtimeRoot.env?.PYTHONPATH || "",
    });

    let server = this.servers.get(key);
    if (!server) {
      server = new DjuleDiagnosticsServer(pythonCommand, runtimeRoot);
      this.servers.set(key, server);
    }
    return server;
  }

  dispose() {
    if (this.disposed) {
      return;
    }
    this.disposed = true;
    for (const server of this.servers.values()) {
      server.dispose();
    }
    this.servers.clear();
  }
}

class DjuleDiagnosticsServer {
  constructor(pythonCommand, runtimeRoot) {
    this.pythonCommand = pythonCommand;
    this.runtimeRoot = runtimeRoot;
    this.child = null;
    this.closed = false;
    this.discoveryCache = new Map();
    this.nextRequestId = 1;
    this.pending = new Map();
    this.stdoutBuffer = "";
    this.stderrBuffer = "";
  }

  async checkDocument(document, globalNames = []) {
    const request = {
      command: "check",
      documentPath: document.uri.scheme === "file" ? document.uri.fsPath : undefined,
      globals: globalNames,
      source: document.getText(),
    };
    return this.request(request);
  }

  async discoverDjangoGlobals(document, options = {}) {
    const documentPath = document.uri.scheme === "file" ? document.uri.fsPath : undefined;
    const request = {
      command: "discover-django",
      documentPath,
    };

    if (typeof options.settingsModule === "string" && options.settingsModule.trim()) {
      request.settingsModule = options.settingsModule.trim();
    }
    if (typeof options.workspacePath === "string" && options.workspacePath.trim()) {
      request.workspacePath = options.workspacePath.trim();
    }

    const cacheKey = JSON.stringify(request);
    if (this.discoveryCache.has(cacheKey)) {
      return this.discoveryCache.get(cacheKey);
    }

    const pendingRequest = this.request(request).catch((error) => {
      this.discoveryCache.delete(cacheKey);
      throw error;
    });

    this.discoveryCache.set(cacheKey, pendingRequest);
    return pendingRequest;
  }

  request(payload) {
    if (this.closed) {
      return Promise.reject(new Error("Djule syntax server is not available"));
    }

    this.ensureStarted();
    const requestId = this.nextRequestId;
    this.nextRequestId += 1;
    const message = JSON.stringify({ ...payload, id: requestId }) + "\n";

    return new Promise((resolve, reject) => {
      this.pending.set(requestId, { reject, resolve });

      try {
        this.child.stdin.write(message, "utf8");
      } catch (error) {
        this.pending.delete(requestId);
        reject(error);
      }
    });
  }

  ensureStarted() {
    if (this.child && !this.child.killed) {
      return;
    }

    this.stdoutBuffer = "";
    this.stderrBuffer = "";

    const child = cp.spawn(
      this.pythonCommand,
      ["-m", "djule.parser", "serve-json"],
      {
        cwd: this.runtimeRoot.cwd,
        env: {
          ...process.env,
          ...this.runtimeRoot.env,
          PYTHONDONTWRITEBYTECODE: "1",
        },
      }
    );

    this.child = child;
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");

    child.stdout.on("data", (chunk) => {
      this.handleStdout(chunk);
    });

    child.stderr.on("data", (chunk) => {
      this.stderrBuffer += chunk;
      if (this.stderrBuffer.length > 8000) {
        this.stderrBuffer = this.stderrBuffer.slice(-8000);
      }
    });

    child.on("error", (error) => {
      this.child = null;
      this.discoveryCache.clear();
      this.rejectAll(new Error(`Djule syntax server failed to start: ${error.message}`));
    });

    child.on("close", (code) => {
      const wasClosed = this.closed;
      this.child = null;
      this.discoveryCache.clear();
      this.stdoutBuffer = "";
      if (!wasClosed) {
        const message = this.stderrBuffer.trim() || `Djule syntax server exited with code ${code}`;
        this.rejectAll(new Error(message));
      }
    });
  }

  handleStdout(chunk) {
    this.stdoutBuffer += chunk;

    while (true) {
      const newlineIndex = this.stdoutBuffer.indexOf("\n");
      if (newlineIndex === -1) {
        break;
      }

      const rawLine = this.stdoutBuffer.slice(0, newlineIndex);
      this.stdoutBuffer = this.stdoutBuffer.slice(newlineIndex + 1);
      const line = rawLine.trim();
      if (!line) {
        continue;
      }

      let response;
      try {
        response = JSON.parse(line);
      } catch (error) {
        this.rejectAll(new Error(`Djule syntax server returned invalid JSON: ${error.message}`));
        if (this.child) {
          this.child.kill();
        }
        return;
      }

      const requestId = response.id;
      const pending = this.pending.get(requestId);
      if (!pending) {
        continue;
      }

      this.pending.delete(requestId);
      pending.resolve(response);
    }
  }

  rejectAll(error) {
    for (const pending of this.pending.values()) {
      pending.reject(error);
    }
    this.pending.clear();
  }

  dispose() {
    if (this.closed) {
      return;
    }

    this.closed = true;
    this.discoveryCache.clear();
    this.rejectAll(new Error("Djule syntax server stopped"));

    if (!this.child) {
      return;
    }

    try {
      this.child.stdin.end(JSON.stringify({ command: "shutdown", id: "shutdown" }) + "\n");
    } catch (_error) {
      this.child.kill();
    }
  }
}

module.exports = {
  createDiagnosticsServerPool,
};
