import { app, safeStorage } from "electron";
import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import type {
  CredentialCreateInput,
  CredentialMetadata,
  CredentialUpdateInput,
} from "../shared/contracts";

interface StoredCredential extends CredentialMetadata {
  encryptedValue: string;
}

function metadata(item: StoredCredential): CredentialMetadata {
  const { encryptedValue: _, ...safe } = item;
  return safe;
}

export class CredentialStore {
  private readonly filePath = path.join(app.getPath("userData"), "credentials.v1.json");

  private assertAvailable(): void {
    if (!safeStorage.isEncryptionAvailable()) {
      throw new Error("The operating system credential encryption service is unavailable");
    }
  }

  private async read(): Promise<StoredCredential[]> {
    try {
      const data = JSON.parse(await readFile(this.filePath, "utf8")) as unknown;
      if (!Array.isArray(data)) throw new Error("Credential store is malformed");
      return data as StoredCredential[];
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
  }

  private async write(items: StoredCredential[]): Promise<void> {
    await mkdir(path.dirname(this.filePath), { recursive: true, mode: 0o700 });
    const temporary = `${this.filePath}.${process.pid}.tmp`;
    await writeFile(temporary, JSON.stringify(items, null, 2), { mode: 0o600 });
    await rename(temporary, this.filePath);
  }

  async list(): Promise<CredentialMetadata[]> {
    return (await this.read()).map(metadata);
  }

  async create(input: CredentialCreateInput): Promise<CredentialMetadata> {
    this.assertAvailable();
    const items = await this.read();
    const id = randomUUID();
    const now = new Date().toISOString();
    const item: StoredCredential = {
      id,
      label: input.label,
      envName: `MNEME_DESKTOP_SECRET_${id.replaceAll("-", "").toUpperCase()}`,
      encryptedValue: safeStorage.encryptString(input.value).toString("base64"),
      createdAt: now,
      updatedAt: now,
    };
    items.push(item);
    await this.write(items);
    return metadata(item);
  }

  async update(input: CredentialUpdateInput): Promise<CredentialMetadata> {
    this.assertAvailable();
    const items = await this.read();
    const index = items.findIndex((item) => item.id === input.id);
    if (index < 0) throw new Error("Credential not found");
    const item = items[index];
    if (input.label !== undefined) item.label = input.label;
    if (input.value !== undefined) {
      item.encryptedValue = safeStorage.encryptString(input.value).toString("base64");
    }
    item.updatedAt = new Date().toISOString();
    await this.write(items);
    return metadata(item);
  }

  async remove(id: string): Promise<void> {
    const items = await this.read();
    const next = items.filter((item) => item.id !== id);
    if (next.length === items.length) throw new Error("Credential not found");
    await this.write(next);
  }

  async environment(): Promise<Record<string, string>> {
    this.assertAvailable();
    const result: Record<string, string> = {};
    for (const item of await this.read()) {
      result[item.envName] = safeStorage.decryptString(
        Buffer.from(item.encryptedValue, "base64"),
      );
    }
    return result;
  }
}
