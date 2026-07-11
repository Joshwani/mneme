import { dialog, ipcMain, type BrowserWindow } from "electron";
import { IPC } from "../shared/contracts";
import {
  parseAddSource,
  parseAuthProfileCreate,
  parseCredentialCreate,
  parseCredentialUpdate,
  parseOperationSearch,
  requiredString,
} from "../shared/validation";
import type { MnemeApi } from "./api-client";
import type { CredentialStore } from "./credential-store";
import type { ServerSupervisor } from "./server-supervisor";

export function registerIpc(
  window: BrowserWindow,
  server: ServerSupervisor,
  api: MnemeApi,
  credentials: CredentialStore,
): void {
  ipcMain.handle(IPC.serverStatus, () => server.status());
  ipcMain.handle(IPC.serverRestart, () => server.restart());
  ipcMain.handle(IPC.overview, () => api.overview());
  ipcMain.handle(IPC.sourceList, () => api.listSources());
  ipcMain.handle(IPC.sourceGet, (_event, id: unknown) =>
    api.getSource(requiredString(id, "Source id", 200)),
  );
  ipcMain.handle(IPC.sourceAdd, (_event, input: unknown) => api.addSource(parseAddSource(input)));
  ipcMain.handle(IPC.sourceReindex, (_event, id: unknown) =>
    api.reindexSource(requiredString(id, "Source id", 200)),
  );
  ipcMain.handle(IPC.sourceRemove, (_event, id: unknown) =>
    api.removeSource(requiredString(id, "Source id", 200)),
  );
  ipcMain.handle(IPC.sourceChooseFile, async () => {
    const result = await dialog.showOpenDialog(window, {
      title: "Choose an OpenAPI specification",
      properties: ["openFile"],
      filters: [
        { name: "OpenAPI", extensions: ["json", "yaml", "yml"] },
        { name: "All files", extensions: ["*"] },
      ],
    });
    return result.canceled ? null : (result.filePaths[0] ?? null);
  });
  ipcMain.handle(IPC.operationSearch, (_event, input: unknown) =>
    api.searchOperations(parseOperationSearch(input)),
  );
  ipcMain.handle(IPC.operationGet, (_event, id: unknown) =>
    api.getOperation(requiredString(id, "Operation id", 500)),
  );

  ipcMain.handle(IPC.credentialList, () => credentials.list());
  ipcMain.handle(IPC.credentialCreate, async (_event, input: unknown) => {
    const created = await credentials.create(parseCredentialCreate(input));
    void server.restart().catch((error) => console.error("Failed to restart Mneme:", error));
    return created;
  });
  ipcMain.handle(IPC.credentialUpdate, async (_event, input: unknown) => {
    const updated = await credentials.update(parseCredentialUpdate(input));
    void server.restart().catch((error) => console.error("Failed to restart Mneme:", error));
    return updated;
  });
  ipcMain.handle(IPC.credentialRemove, async (_event, id: unknown) => {
    await credentials.remove(requiredString(id, "Credential id", 100));
    void server.restart().catch((error) => console.error("Failed to restart Mneme:", error));
  });
  ipcMain.handle(IPC.authProfileList, () => api.listAuthProfiles());
  ipcMain.handle(IPC.authProfileCreate, (_event, input: unknown) =>
    api.createAuthProfile(parseAuthProfileCreate(input)),
  );
  ipcMain.handle(IPC.authProfileUpdate, (_event, name: unknown, input: unknown) =>
    api.updateAuthProfile(
      requiredString(name, "Profile name", 128),
      parseAuthProfileCreate(input),
    ),
  );
  ipcMain.handle(IPC.authProfileRemove, async (_event, name: unknown) => {
    await api.removeAuthProfile(requiredString(name, "Profile name", 128));
  });
}
