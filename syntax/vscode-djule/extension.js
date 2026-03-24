const vscode = require("vscode");

const { provideDjuleCompletions } = require("./lib/completions");
const { registerDiagnostics } = require("./lib/diagnostics");

function activate(context) {
  registerDiagnostics(context);

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
}

function deactivate() {}

module.exports = {
  activate,
  deactivate,
};
