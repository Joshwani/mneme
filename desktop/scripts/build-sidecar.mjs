import { spawnSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repository = path.resolve(desktop, "..");
const environment = path.join(desktop, ".sidecar-venv");
const build = path.join(desktop, ".sidecar-build");
const output = path.join(desktop, "resources", "sidecar");
const windows = process.platform === "win32";
const bootstrapPython = process.env.MNEME_SIDECAR_PYTHON || (windows ? "python" : "python3");
const python = path.join(environment, windows ? "Scripts/python.exe" : "bin/python");

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: desktop,
    env: process.env,
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} exited with status ${result.status}`);
  }
}

mkdirSync(output, { recursive: true });
run(bootstrapPython, ["-m", "venv", environment]);
run(python, [
  "-m",
  "pip",
  "install",
  "--disable-pip-version-check",
  "--upgrade",
  repository,
  "pyinstaller",
]);
run(python, [
  "-m",
  "PyInstaller",
  path.join(desktop, "scripts", "sidecar-entry.py"),
  "--name",
  "mneme-sidecar",
  "--onefile",
  "--noconfirm",
  "--clean",
  "--distpath",
  output,
  "--workpath",
  path.join(build, "work"),
  "--specpath",
  build,
  "--collect-all",
  "mneme",
  "--collect-all",
  "uvicorn",
  "--copy-metadata",
  "mneme-server",
]);
