import { contextBridge, ipcRenderer } from "electron";
import { IPC, type DesktopApi } from "../shared/contracts";

const api: DesktopApi = {
  server: {
    status: () => ipcRenderer.invoke(IPC.serverStatus),
    restart: () => ipcRenderer.invoke(IPC.serverRestart),
  },
  overview: () => ipcRenderer.invoke(IPC.overview),
  catalog: {
    listSources: () => ipcRenderer.invoke(IPC.sourceList),
    getSource: (id) => ipcRenderer.invoke(IPC.sourceGet, id),
    addSource: (input) => ipcRenderer.invoke(IPC.sourceAdd, input),
    reindexSource: (id) => ipcRenderer.invoke(IPC.sourceReindex, id),
    removeSource: (id) => ipcRenderer.invoke(IPC.sourceRemove, id),
    chooseSpecFile: () => ipcRenderer.invoke(IPC.sourceChooseFile),
    searchOperations: (input) => ipcRenderer.invoke(IPC.operationSearch, input),
    getOperation: (id) => ipcRenderer.invoke(IPC.operationGet, id),
  },
  credentials: {
    list: () => ipcRenderer.invoke(IPC.credentialList),
    create: (input) => ipcRenderer.invoke(IPC.credentialCreate, input),
    update: (input) => ipcRenderer.invoke(IPC.credentialUpdate, input),
    remove: (id) => ipcRenderer.invoke(IPC.credentialRemove, id),
  },
  authProfiles: {
    list: () => ipcRenderer.invoke(IPC.authProfileList),
    create: (input) => ipcRenderer.invoke(IPC.authProfileCreate, input),
    update: (name, input) => ipcRenderer.invoke(IPC.authProfileUpdate, name, input),
    remove: (name) => ipcRenderer.invoke(IPC.authProfileRemove, name),
  },
};

contextBridge.exposeInMainWorld("mneme", Object.freeze(api));
