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
const IMPORT_FROM_RE =
  /^\s*from\s+([.\w]+)\s+import\s+([A-Z][A-Za-z0-9_]*(?:\s*,\s*[A-Z][A-Za-z0-9_]*)*)\s*$/gm;
const IMPORT_MODULE_RE = /^\s*import\s+([.\w]+)(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?\s*$/gm;
const ASSIGNMENT_RE = /^\s*([a-z_][A-Za-z0-9_]*)\s*=/gm;
const FOR_TARGET_RE = /^\s*for\s+([a-z_][A-Za-z0-9_]*)\s+in\b/gm;
const DIAGNOSTIC_SOURCE = "djule";
const DIAGNOSTIC_DEBOUNCE_MS = 200;

module.exports = {
  ASSIGNMENT_RE,
  COMPONENT_DEF_RE,
  DIAGNOSTIC_DEBOUNCE_MS,
  DIAGNOSTIC_SOURCE,
  FOR_TARGET_RE,
  HTML_TAGS,
  IMPORT_FROM_RE,
  IMPORT_MODULE_RE,
};
