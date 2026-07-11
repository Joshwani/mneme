import type {
  AddSourceInput,
  AuthProfileCreateInput,
  CredentialCreateInput,
  CredentialUpdateInput,
  OperationSearchInput,
} from "./contracts";

function object(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} must be an object`);
  }
  return value as Record<string, unknown>;
}

export function requiredString(value: unknown, name: string, max = 2048): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${name} is required`);
  }
  const result = value.trim();
  if (result.length > max) throw new Error(`${name} is too long`);
  return result;
}

export function parseAddSource(value: unknown): AddSourceInput {
  const input = object(value, "source");
  if (!["url", "file", "domain"].includes(String(input.kind))) {
    throw new Error("Unsupported source kind");
  }
  return {
    kind: input.kind as AddSourceInput["kind"],
    value: requiredString(input.value, "Source", 8192),
  };
}

export function parseOperationSearch(value: unknown): OperationSearchInput {
  const input = object(value, "search");
  const limit = input.limit === undefined ? 30 : Number(input.limit);
  if (!Number.isInteger(limit) || limit < 1 || limit > 50) {
    throw new Error("Limit must be between 1 and 50");
  }
  return {
    query: typeof input.query === "string" ? input.query.trim().slice(0, 500) : "",
    method:
      typeof input.method === "string" && input.method ? input.method.slice(0, 12) : undefined,
    providerDomain:
      typeof input.providerDomain === "string" && input.providerDomain
        ? input.providerDomain.slice(0, 255)
        : undefined,
    limit,
  };
}

export function parseCredentialCreate(value: unknown): CredentialCreateInput {
  const input = object(value, "credential");
  return {
    label: requiredString(input.label, "Label", 120),
    value: requiredString(input.value, "Credential value", 16_384),
  };
}

export function parseCredentialUpdate(value: unknown): CredentialUpdateInput {
  const input = object(value, "credential");
  const result: CredentialUpdateInput = {
    id: requiredString(input.id, "Credential id", 100),
  };
  if (input.label !== undefined) result.label = requiredString(input.label, "Label", 120);
  if (input.value !== undefined) {
    result.value = requiredString(input.value, "Credential value", 16_384);
  }
  return result;
}

export function parseAuthProfileCreate(value: unknown): AuthProfileCreateInput {
  const input = object(value, "auth profile");
  if (input.authType !== "bearer" && input.authType !== "api_key") {
    throw new Error("Unsupported authentication type");
  }
  const methods = Array.isArray(input.allowMethods)
    ? input.allowMethods.map((method) => requiredString(method, "Method", 32).toUpperCase())
    : [];
  if (!methods.length) throw new Error("At least one allowed method is required");
  return {
    name: requiredString(input.name, "Profile name", 128),
    providerDomain: requiredString(input.providerDomain, "Provider domain", 253),
    baseUrl:
      typeof input.baseUrl === "string" && input.baseUrl.trim()
        ? requiredString(input.baseUrl, "Base URL", 2048)
        : undefined,
    authType: input.authType,
    credentialEnv: requiredString(input.credentialEnv, "Credential environment", 256),
    apiKeyName:
      input.authType === "api_key"
        ? requiredString(input.apiKeyName || "X-API-Key", "API key header", 256)
        : undefined,
    allowMethods: [...new Set(methods)],
  };
}
