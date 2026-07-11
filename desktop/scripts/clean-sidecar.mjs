import { rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
for (const target of [".sidecar-build", ".sidecar-venv", "resources/sidecar"]) {
  rmSync(path.join(desktop, target), { force: true, recursive: true });
}
