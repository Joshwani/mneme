import { app } from "electron";
import { randomBytes } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { access } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import type { ServerStatus } from "../shared/contracts";
import type { CredentialStore } from "./credential-store";

const READY_TIMEOUT_MS = 20_000;

async function ephemeralPort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

async function firstExisting(paths: string[]): Promise<string> {
  for (const candidate of paths) {
    try {
      await access(candidate);
      return candidate;
    } catch {
      // Continue to the next explicit executable candidate.
    }
  }
  throw new Error(`Mneme Python executable not found. Tried: ${paths.join(", ")}`);
}

export class ServerSupervisor {
  private child?: ChildProcess;
  private token = "";
  private statusValue: ServerStatus = { state: "stopped" };
  private operation: Promise<ServerStatus> | undefined;

  constructor(private readonly credentials: CredentialStore) {}

  status(): ServerStatus {
    return { ...this.statusValue };
  }

  async start(): Promise<ServerStatus> {
    if (this.operation) return this.operation;
    if (this.child && this.statusValue.state === "ready") return this.status();
    this.operation = this.startInternal().finally(() => {
      this.operation = undefined;
    });
    return this.operation;
  }

  async restart(): Promise<ServerStatus> {
    if (this.operation) await this.operation.catch(() => undefined);
    await this.stop();
    return this.start();
  }

  async stop(): Promise<void> {
    const child = this.child;
    this.child = undefined;
    this.statusValue = { state: "stopped" };
    if (!child || child.killed) return;
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        child.kill("SIGKILL");
        resolve();
      }, 3_000);
      child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      child.kill("SIGTERM");
    });
  }

  async request<T>(route: string, init: RequestInit = {}): Promise<T> {
    if (this.statusValue.state !== "ready" || !this.statusValue.port) {
      await this.start();
    }
    const response = await fetch(`http://127.0.0.1:${this.statusValue.port}${route}`, {
      ...init,
      headers: {
        accept: "application/json",
        authorization: `Bearer ${this.token}`,
        "x-mneme-management-token": this.token,
        "content-type": "application/json",
        ...init.headers,
      },
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok) {
      const body = await response.text();
      let message = body;
      try {
        const parsed = JSON.parse(body) as { detail?: string; message?: string };
        message = parsed.detail ?? parsed.message ?? body;
      } catch {
        // Keep non-JSON response text.
      }
      throw new Error(message || `Mneme API returned ${response.status}`);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  private async startInternal(): Promise<ServerStatus> {
    const port = await ephemeralPort();
    this.token = randomBytes(32).toString("base64url");
    this.statusValue = { state: "starting", port };

    try {
      const { command, args } = await this.command(port);
      const env = {
        ...process.env,
        ...(await this.credentials.environment()),
        MNEME_MANAGEMENT_TOKEN: this.token,
        PYTHONUNBUFFERED: "1",
      };
      const child = spawn(command, args, {
        env,
        cwd: app.isPackaged ? process.resourcesPath : path.resolve(app.getAppPath(), ".."),
        stdio: ["ignore", "pipe", "pipe"],
      });
      this.child = child;
      child.stdout?.on("data", (chunk) => console.info(`[mneme] ${String(chunk).trimEnd()}`));
      child.stderr?.on("data", (chunk) => console.error(`[mneme] ${String(chunk).trimEnd()}`));
      child.once("exit", (code, signal) => {
        if (this.child !== child) return;
        this.child = undefined;
        this.statusValue = {
          state: code === 0 ? "stopped" : "error",
          message: `Mneme server exited (${signal ?? code ?? "unknown"})`,
        };
      });
      child.once("error", (error) => {
        this.statusValue = { state: "error", message: error.message };
      });
      await this.waitUntilReady(port, child);
      this.statusValue = {
        state: "ready",
        port,
        startedAt: new Date().toISOString(),
      };
      return this.status();
    } catch (error) {
      await this.stop();
      this.statusValue = {
        state: "error",
        message: error instanceof Error ? error.message : String(error),
      };
      throw error;
    }
  }

  private async command(port: number): Promise<{ command: string; args: string[] }> {
    if (app.isPackaged) {
      const override = process.env.MNEME_DESKTOP_SIDECAR;
      const executable = override
        ? path.resolve(override)
        : path.join(
            process.resourcesPath,
            "sidecar",
            process.platform === "win32" ? "mneme-sidecar.exe" : "mneme-sidecar",
          );
      await access(executable);
      return {
        command: executable,
        args: ["serve", "--host", "127.0.0.1", "--port", String(port)],
      };
    }

    const repository = path.resolve(app.getAppPath(), "..");
    const python = await firstExisting([
      ...(process.env.MNEME_DESKTOP_PYTHON ? [process.env.MNEME_DESKTOP_PYTHON] : []),
      path.join(repository, ".venv", "bin", "python"),
      path.join(repository, ".venv", "Scripts", "python.exe"),
    ]);
    return {
      command: python,
      args: ["-m", "mneme.cli", "serve", "--host", "127.0.0.1", "--port", String(port)],
    };
  }

  private async waitUntilReady(
    port: number,
    child: ChildProcess,
  ): Promise<void> {
    const deadline = Date.now() + READY_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (child.exitCode !== null) throw new Error(`Mneme server exited with ${child.exitCode}`);
      try {
        const response = await fetch(`http://127.0.0.1:${port}/diagnostics`, {
          headers: {
            authorization: `Bearer ${this.token}`,
            "x-mneme-management-token": this.token,
          },
          signal: AbortSignal.timeout(750),
        });
        if (response.ok) return;
        if (response.status === 401 || response.status === 403) {
          throw new Error("Mneme server rejected its per-launch API token");
        }
      } catch (error) {
        if (
          error instanceof Error &&
          error.message === "Mneme server rejected its per-launch API token"
        ) {
          throw error;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
    throw new Error("Timed out waiting for the Mneme server to become ready");
  }
}
