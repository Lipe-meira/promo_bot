// Fails when src/lib/api/schema.d.ts is not exactly what ../api/openapi.json generates.
// The contract is the single source of the HTTP types; run `npm run gen:api` to refresh.
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dir = mkdtempSync(join(tmpdir(), "promo-api-types-"));
const generated = join(dir, "schema.d.ts");
try {
  const run = spawnSync(
    process.execPath,
    ["node_modules/openapi-typescript/bin/cli.js", "../api/openapi.json", "-o", generated],
    { encoding: "utf8" },
  );
  if (run.status !== 0) {
    console.error(run.stdout, run.stderr);
    process.exit(2);
  }
  const normalize = (text) => text.replace(/\r\n/g, "\n");
  const current = normalize(readFileSync("src/lib/api/schema.d.ts", "utf8"));
  if (current !== normalize(readFileSync(generated, "utf8"))) {
    console.error("src/lib/api/schema.d.ts is out of date: run `npm run gen:api`");
    process.exit(1);
  }
  console.log("api types are in sync with openapi.json");
} finally {
  rmSync(dir, { recursive: true, force: true });
}
