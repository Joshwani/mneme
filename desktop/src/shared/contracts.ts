export type ServerState = "starting" | "ready" | "stopped" | "error";

export interface ServerStatus {
  state: ServerState;
  port?: number;
  message?: string;
  startedAt?: string;
}

export interface Overview {
  healthy: boolean;
  version: string;
  stats: Record<string, number | string | null>;
  server: ServerStatus;
}

export type SourceKind = "url" | "file" | "domain";

export interface CatalogSource {
  id: string;
  name: string;
  kind: string;
  location: string;
  operationCount: number;
  indexedAt?: string;
  status?: string;
}

export interface CatalogSourceDetail extends CatalogSource {
  documentation: unknown;
  raw: Record<string, unknown>;
}

export interface AddSourceInput {
  kind: SourceKind;
  value: string;
}

export interface OperationSummary {
  id: string;
  method: string;
  path: string;
  summary: string;
  providerDomain?: string;
  authRequired?: boolean;
  score?: number;
}

export interface OperationDetail extends OperationSummary {
  description?: string;
  specSlice: unknown;
  documentation?: string;
  raw: Record<string, unknown>;
}

export interface OperationSearchInput {
  query: string;
  method?: string;
  providerDomain?: string;
  limit?: number;
}

export interface CredentialMetadata {
  id: string;
  label: string;
  envName: string;
  createdAt: string;
  updatedAt: string;
}

export interface CredentialCreateInput {
  label: string;
  value: string;
}

export interface CredentialUpdateInput {
  id: string;
  label?: string;
  value?: string;
}

export interface AuthProfile {
  name: string;
  providerDomain?: string;
  baseUrl?: string;
  authType?: string;
  credentialEnv?: string;
  apiKeyName?: string;
  allowMethods: string[];
}

export interface AuthProfileCreateInput {
  name: string;
  providerDomain: string;
  baseUrl?: string;
  authType: "bearer" | "api_key";
  credentialEnv: string;
  apiKeyName?: string;
  allowMethods: string[];
}

export interface DesktopApi {
  server: {
    status(): Promise<ServerStatus>;
    restart(): Promise<ServerStatus>;
  };
  overview(): Promise<Overview>;
  catalog: {
    listSources(): Promise<CatalogSource[]>;
    getSource(id: string): Promise<CatalogSourceDetail>;
    addSource(input: AddSourceInput): Promise<CatalogSource>;
    reindexSource(id: string): Promise<CatalogSource>;
    removeSource(id: string): Promise<void>;
    chooseSpecFile(): Promise<string | null>;
    searchOperations(input: OperationSearchInput): Promise<OperationSummary[]>;
    getOperation(id: string): Promise<OperationDetail>;
  };
  credentials: {
    list(): Promise<CredentialMetadata[]>;
    create(input: CredentialCreateInput): Promise<CredentialMetadata>;
    update(input: CredentialUpdateInput): Promise<CredentialMetadata>;
    remove(id: string): Promise<void>;
  };
  authProfiles: {
    list(): Promise<AuthProfile[]>;
    create(input: AuthProfileCreateInput): Promise<AuthProfile>;
    update(name: string, input: AuthProfileCreateInput): Promise<AuthProfile>;
    remove(name: string): Promise<void>;
  };
}

export const IPC = {
  serverStatus: "mneme:server:status",
  serverRestart: "mneme:server:restart",
  overview: "mneme:overview",
  sourceList: "mneme:catalog:sources",
  sourceGet: "mneme:catalog:source",
  sourceAdd: "mneme:catalog:add",
  sourceReindex: "mneme:catalog:reindex",
  sourceRemove: "mneme:catalog:remove",
  sourceChooseFile: "mneme:catalog:choose-file",
  operationSearch: "mneme:catalog:operations",
  operationGet: "mneme:catalog:operation",
  credentialList: "mneme:credentials:list",
  credentialCreate: "mneme:credentials:create",
  credentialUpdate: "mneme:credentials:update",
  credentialRemove: "mneme:credentials:remove",
  authProfileList: "mneme:auth-profiles:list",
  authProfileCreate: "mneme:auth-profiles:create",
  authProfileUpdate: "mneme:auth-profiles:update",
  authProfileRemove: "mneme:auth-profiles:remove",
} as const;
