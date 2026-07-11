import { app } from "electron";
import { fileURLToPath } from "node:url";
import type {
  AddSourceInput,
  AuthProfile,
  AuthProfileCreateInput,
  CatalogSource,
  CatalogSourceDetail,
  OperationDetail,
  OperationSearchInput,
  OperationSummary,
  Overview,
} from "../shared/contracts";
import type { ServerSupervisor } from "./server-supervisor";

type JsonObject = Record<string, unknown>;

function record(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function number(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function stats(value: unknown): Record<string, number | string | null> {
  return Object.fromEntries(
    Object.entries(record(value)).filter(
      (entry): entry is [string, number | string | null] =>
        entry[1] === null || typeof entry[1] === "number" || typeof entry[1] === "string",
    ),
  );
}

function normalizeSource(value: unknown, index: number): CatalogSource {
  const item = record(value);
  const location = text(item.location ?? item.url ?? item.path ?? item.source_url, "");
  return {
    id: text(item.id ?? item.spec_id ?? item.source_id, `source-${index}`),
    name: text(item.name ?? item.title ?? item.provider_domain, "Untitled source"),
    kind: text(
      item.kind ?? item.source_kind,
      location.startsWith("file:") ? "file" : "url",
    ),
    location,
    operationCount: number(item.operationCount ?? item.operation_count ?? item.operations),
    indexedAt:
      text(item.indexedAt ?? item.indexed_at ?? item.updated_at ?? item.fetched_at) ||
      undefined,
    status: text(item.status) || undefined,
  };
}

function normalizeOperation(value: unknown): OperationSummary {
  const item = record(value);
  return {
    id: text(item.id ?? item.operation_id ?? item.operationId),
    method: text(item.method, "GET").toUpperCase(),
    path: text(item.path ?? item.path_template ?? item.url, "/"),
    summary: text(item.summary ?? item.name ?? item.description, "Untitled operation"),
    providerDomain: text(item.providerDomain ?? item.provider_domain) || undefined,
    authRequired:
      typeof (item.authRequired ?? item.auth_required) === "boolean"
        ? Boolean(item.authRequired ?? item.auth_required)
        : undefined,
    score:
      typeof (item.score ?? item.quality_score) === "number"
        ? Number(item.score ?? item.quality_score)
        : undefined,
  };
}

function arrayFrom(value: unknown, keys: string[]): unknown[] {
  if (Array.isArray(value)) return value;
  const item = record(value);
  for (const key of keys) {
    if (Array.isArray(item[key])) return item[key] as unknown[];
  }
  return [];
}

export class MnemeApi {
  constructor(private readonly server: ServerSupervisor) {}

  async overview(): Promise<Overview> {
    const [health, version] = await Promise.all([
      this.server.request<JsonObject>("/health"),
      this.server
        .request<JsonObject>("/version")
        .catch(() => ({ version: app.getVersion() })),
    ]);
    return {
      healthy: health.ok === true || text(health.status).toLowerCase() === "ok",
      version: text(version.version, app.getVersion()),
      stats: stats(health.stats),
      server: this.server.status(),
    };
  }

  async listSources(): Promise<CatalogSource[]> {
    const response = await this.server.request<unknown>("/specs?limit=200");
    return arrayFrom(response, ["sources", "items", "results"]).map(normalizeSource);
  }

  async getSource(id: string): Promise<CatalogSourceDetail> {
    const response = await this.server.request<JsonObject>(`/specs/${encodeURIComponent(id)}`);
    return {
      ...normalizeSource(response, 0),
      documentation: response.documentation ?? {},
      raw: response,
    };
  }

  async addSource(input: AddSourceInput): Promise<CatalogSource> {
    const route =
      input.kind === "url"
        ? "/specs/ingest-url"
        : input.kind === "file"
          ? "/specs/ingest-file"
          : "/specs/discover";
    const field = input.kind === "url" ? "url" : input.kind === "file" ? "path" : "domain";
    const response = await this.server.request<unknown>(route, {
      method: "POST",
      body: JSON.stringify({ [field]: input.value }),
    });
    const body = record(response);
    const directId = text(body.spec_id);
    const discovered = arrayFrom(response, ["results"])
      .map(record)
      .find((item) => text(item.spec_id));
    const specId = directId || text(discovered?.spec_id);
    if (!specId) {
      throw new Error(
        input.kind === "domain"
          ? "No OpenAPI specification was discovered for this domain"
          : "Mneme indexed the source but did not return its identifier",
      );
    }
    return this.getSource(specId);
  }

  async reindexSource(id: string): Promise<CatalogSource> {
    const source = await this.server.request<JsonObject>(`/specs/${encodeURIComponent(id)}`);
    const location = text(source.source_url);
    if (!location) throw new Error("This source does not have a reindexable location");
    return this.addSource({
      kind: location.startsWith("file:") ? "file" : "url",
      value: location.startsWith("file:") ? fileURLToPath(location) : location,
    });
  }

  async removeSource(id: string): Promise<void> {
    await this.server.request(`/specs/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  }

  async searchOperations(input: OperationSearchInput): Promise<OperationSummary[]> {
    const params = new URLSearchParams({ limit: String(input.limit ?? 30) });
    if (input.query) params.set("q", input.query);
    if (input.method) params.set("method", input.method);
    if (input.providerDomain) params.set("provider_domain", input.providerDomain);
    const response = await this.server.request<unknown>(`/operations?${params}`);
    return arrayFrom(response, ["operations", "results", "items"])
      .map(normalizeOperation)
      .filter((item) => item.id);
  }

  async getOperation(id: string): Promise<OperationDetail> {
    const encoded = encodeURIComponent(id);
    const [raw, specSlice, docsResponse] = await Promise.all([
      this.server.request<JsonObject>(`/operations/${encoded}`),
      this.server.request<unknown>(`/operations/${encoded}/spec-slice`),
      this.server
        .request<JsonObject>(`/operations/${encoded}/docs`)
        .catch(() => ({})),
    ]);
    const docs = record(docsResponse);
    return {
      ...normalizeOperation(raw),
      description: text(raw.description) || undefined,
      specSlice,
      documentation: text(docs.markdown ?? docs.documentation ?? raw.documentation) || undefined,
      raw,
    };
  }

  async listAuthProfiles(): Promise<AuthProfile[]> {
    const response = await this.server.request<unknown>("/auth/profiles");
    return arrayFrom(response, ["profiles", "items"]).map((value) => {
      const item = record(value);
      const auth = record(item.auth);
      return {
        name: text(item.name),
        providerDomain: text(item.provider_domain) || undefined,
        baseUrl: text(item.base_url) || undefined,
        authType: text(auth.type) || undefined,
        credentialEnv:
          text(
            auth.token_env ??
              auth.api_key_env ??
              auth.value_env ??
              auth.env ??
              auth.username_env,
          ) || undefined,
        apiKeyName: text(auth.name) || undefined,
        allowMethods: Array.isArray(item.allow_methods)
          ? item.allow_methods.filter((method): method is string => typeof method === "string")
          : [],
      };
    });
  }

  async createAuthProfile(input: AuthProfileCreateInput): Promise<AuthProfile> {
    const auth = this.authMetadata(input);
    await this.server.request<unknown>("/auth/profiles", {
      method: "POST",
      body: JSON.stringify({
        name: input.name,
        provider_domain: input.providerDomain,
        ...(input.baseUrl ? { base_url: input.baseUrl } : {}),
        auth,
        allow_methods: input.allowMethods,
      }),
    });
    return {
      name: input.name,
      providerDomain: input.providerDomain,
      baseUrl: input.baseUrl,
      authType: input.authType,
      credentialEnv: input.credentialEnv,
      apiKeyName: input.apiKeyName,
      allowMethods: input.allowMethods,
    };
  }

  async updateAuthProfile(
    name: string,
    input: AuthProfileCreateInput,
  ): Promise<AuthProfile> {
    await this.server.request<unknown>(`/auth/profiles/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({
        provider_domain: input.providerDomain,
        ...(input.baseUrl ? { base_url: input.baseUrl } : {}),
        auth: this.authMetadata(input),
        allow_methods: input.allowMethods,
      }),
    });
    return {
      name,
      providerDomain: input.providerDomain,
      baseUrl: input.baseUrl,
      authType: input.authType,
      credentialEnv: input.credentialEnv,
      apiKeyName: input.apiKeyName,
      allowMethods: input.allowMethods,
    };
  }

  async removeAuthProfile(name: string): Promise<void> {
    await this.server.request(`/auth/profiles/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
  }

  private authMetadata(input: AuthProfileCreateInput): JsonObject {
    return input.authType === "bearer"
      ? { type: "bearer", token_env: input.credentialEnv }
      : {
          type: "api_key",
          in: "header",
          name: input.apiKeyName ?? "X-API-Key",
          api_key_env: input.credentialEnv,
        };
  }
}
