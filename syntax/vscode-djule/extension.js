const vscode = require("vscode");

const { provideDjuleCompletions } = require("./lib/completions");
const { provideDjuleDefinition } = require("./lib/definitions");
const { registerDiagnostics } = require("./lib/diagnostics");

function activate(context) {
  registerDiagnostics(context);

  context.subscriptions.push(
    vscode.languages.registerCompletionItemProvider(
      { language: "djule" },
      {
        async provideCompletionItems(document, position) {
          const configuration = vscode.workspace.getConfiguration("djule", document);
          return provideDjuleCompletions(document, position, context, configuration);
        },
      },
      "<",
      ".",
      " "
    )
  );

  context.subscriptions.push(
    vscode.languages.registerDefinitionProvider(
      { language: "djule" },
      {
        provideDefinition(document, position) {
          const configuration = vscode.workspace.getConfiguration("djule", document);
          return provideDjuleDefinition(document, position, context, configuration);
        },
      }
    )
  );
}

function deactivate() {}

module.exports = {
  activate,
  deactivate,
};
