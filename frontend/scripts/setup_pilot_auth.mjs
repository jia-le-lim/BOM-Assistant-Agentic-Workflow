/** Enable local sign-in from existing pilot hashes, without changing Docker or passwords. */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = process.argv[2] ?? "C:/ProgramData/BOM-Supabase/pilot-accounts.local.json";
const destination = join(frontend, ".local", "pilot-auth.json");

try {
  const original = JSON.parse(await readFile(source, "utf8"));
  if (!original || Array.isArray(original) || typeof original !== "object") {
    throw new Error("Expected the existing pilot account dictionary.");
  }
  const identifiers = new Set();
  const accounts = Object.entries(original).map(([user, account]) => {
    if (!/^[a-z0-9][a-z0-9-]{0,39}$/.test(user) || !account
        || typeof account.hash !== "string"
        || !/^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$/.test(account.hash)) {
      throw new Error("Each pilot account must have a valid username and an existing bcrypt hash.");
    }
    // Explicit allowlist: plaintext passwords and other private fields stay in the source.
    const result = { user, hash: account.hash };
    if (account.email) {
      if (typeof account.email !== "string" || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(account.email.trim())) {
        throw new Error("Invalid email alias in the pilot account file.");
      }
      result.email = account.email.trim().toLowerCase();
    }
    for (const identifier of [user, result.email].filter(Boolean)) {
      if (identifiers.has(identifier)) throw new Error("Duplicate pilot username or email alias.");
      identifiers.add(identifier);
    }
    return result;
  });
  if (!accounts.length) throw new Error("No pilot accounts were found.");

  const envFile = join(frontend, ".env.local");
  let environment = "";
  try { environment = await readFile(envFile, "utf8"); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
  const newline = environment.includes("\r\n") ? "\r\n" : "\n";
  const lines = environment.split(/\r?\n/).filter((line) =>
    !/^\s*(?:export\s+)?PILOT_AUTH_(?:REQUIRED|FILE)\s*=/.test(line));
  while (lines.at(-1) === "") lines.pop();
  lines.push("PILOT_AUTH_REQUIRED=1", "PILOT_AUTH_FILE=.local/pilot-auth.json", "");

  await mkdir(dirname(destination), { recursive: true, mode: 0o700 });
  await writeFile(destination, JSON.stringify(accounts, null, 2) + "\n", { mode: 0o600 });
  await writeFile(envFile, lines.join(newline), { mode: 0o600 });
  console.log(`Local sign-in configured for ${accounts.length} existing pilot accounts.`);
  console.log("Only password hashes were copied. Local files are excluded from Git and Docker builds.");
  console.log("The Next.js dev server reloads .env.local; if already running, refresh /login.");
} catch (error) {
  // Do not print source contents or credential-bearing objects on failure.
  console.error(`Could not configure local sign-in: ${error.code ?? (error instanceof SyntaxError ? "Invalid JSON" : error.message)}`);
  process.exitCode = 1;
}
