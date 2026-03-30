function parseConfiguredGlobals(configuration) {
  return parseGlobalSchema(configuration.get("globals", {}));
}

function parseGlobalSchema(rawValue) {
  return normalizeGlobalMap(rawValue);
}

function configuredGlobalNames(globalSymbols) {
  return Array.from(globalSymbols.keys()).sort();
}

function resolveConfiguredGlobalMembers(globalSymbols, pathParts) {
  if (!Array.isArray(pathParts) || pathParts.length === 0) {
    return null;
  }

  let currentNode = globalSymbols.get(pathParts[0]);
  if (!currentNode) {
    return null;
  }

  for (const part of pathParts.slice(1)) {
    currentNode = currentNode.members.get(part);
    if (!currentNode) {
      return null;
    }
  }

  return currentNode.members;
}

function mergeGlobalSymbols(primarySymbols, secondarySymbols) {
  const merged = new Map(primarySymbols);

  for (const [name, value] of secondarySymbols) {
    if (!merged.has(name)) {
      merged.set(name, value);
      continue;
    }

    merged.set(name, mergeGlobalNode(merged.get(name), value));
  }

  return merged;
}

function normalizeGlobalMap(rawValue) {
  if (!isPlainObject(rawValue)) {
    return new Map();
  }

  const globalSymbols = new Map();
  for (const [name, value] of Object.entries(rawValue)) {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
      continue;
    }
    globalSymbols.set(name, normalizeGlobalNode(value));
  }
  return globalSymbols;
}

function normalizeGlobalNode(value) {
  if (typeof value === "string") {
    return {
      detail: value,
      members: new Map(),
    };
  }

  if (!isPlainObject(value)) {
    return {
      detail: null,
      members: new Map(),
    };
  }

  const hasExplicitSchema =
    Object.prototype.hasOwnProperty.call(value, "detail") ||
    Object.prototype.hasOwnProperty.call(value, "members");
  const detail = typeof value.detail === "string" ? value.detail : null;
  const membersValue = hasExplicitSchema ? value.members : value;

  return {
    detail,
    members: normalizeGlobalMap(membersValue),
  };
}

function mergeGlobalNode(leftNode, rightNode) {
  const mergedMembers = mergeGlobalSymbols(leftNode.members, rightNode.members);
  return {
    detail: leftNode.detail || rightNode.detail,
    members: mergedMembers,
  };
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

module.exports = {
  configuredGlobalNames,
  parseConfiguredGlobals,
  parseGlobalSchema,
  mergeGlobalSymbols,
  resolveConfiguredGlobalMembers,
};
