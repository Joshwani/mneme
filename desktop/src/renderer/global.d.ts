import type { DesktopApi } from "../shared/contracts";

declare global {
  interface Window {
    mneme: DesktopApi;
  }
}

export {};
