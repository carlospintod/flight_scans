// CI drift guard: re-compute Python's sampled-stays fixture through
// web/src/lib/stays.ts; ANY divergence fails. Run after
// `python scripts/gen_stays_fixture.py > web/stays-fixture.json`.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(join(here, "..", "stays-fixture.json"), "utf-8"));
const src = readFileSync(join(here, "..", "src", "lib", "stays.ts"), "utf-8");
const js = src
  .replace(/: number\[\]/g, "")
  .replace(/: number/g, "")
  .replace(/export function/g, "function");
const sampledStays = new Function(`${js}; return sampledStays;`)();

let failures = 0;
for (const { input, expected } of fixture.cases) {
  const got = sampledStays(input.min, input.max, input.step);
  if (JSON.stringify(got) !== JSON.stringify(expected)) {
    failures++;
    console.error(`DRIFT ${JSON.stringify(input)}: py=${JSON.stringify(expected)} ts=${JSON.stringify(got)}`);
  }
}
if (failures) {
  console.error(`sampled-stays drift: ${failures} mismatch(es)`);
  process.exit(1);
}
console.log(`sampled stays: ${fixture.cases.length} cases, TS matches Python`);
