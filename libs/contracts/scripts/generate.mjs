// Generates Zod schemas + TypeScript types from the JSON Schemas in ../schemas.
// The JSON Schemas are the source of truth; Python services read them directly.
import { readdir, readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import $RefParser from "@apidevtools/json-schema-ref-parser";
import { jsonSchemaToZod } from "json-schema-to-zod";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const schemasDir = join(root, "schemas");
const outDir = join(root, "src", "generated");
await mkdir(outDir, { recursive: true });

const files = (await readdir(schemasDir)).filter((f) => f.endsWith(".json")).sort();
const exports = [];
for (const file of files) {
  const raw = JSON.parse(await readFile(join(schemasDir, file), "utf8"));
  const schema = await $RefParser.dereference(raw);
  const name = raw.title;
  const code = jsonSchemaToZod(schema, { name: `${name}Schema`, module: "esm", type: name });
  const out = `// Generated from schemas/${file} by scripts/generate.mjs. Do not edit.\n${code}\n`;
  const base = file.replace(/\.json$/, "");
  await writeFile(join(outDir, `${base}.ts`), out);
  exports.push(`export * from "./${base}.js";`);
}
await writeFile(join(outDir, "index.ts"), `// Generated. Do not edit.\n${exports.join("\n")}\n`);
console.log(`generated ${files.length} schemas`);
