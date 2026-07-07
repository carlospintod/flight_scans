// CI drift guard: re-compute the Python-generated estimator fixture in
// TS-land; ANY divergence fails. Run: node web/scripts/check-estimator.mjs
// (tests.yml regenerates the fixture from Python first, so drift on
// EITHER side turns CI red.)
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(join(here, "..", "estimator-fixture.json"), "utf-8"),
);

// predict.ts is TS; re-implementing here would defeat the guard, so we
// load the compiled logic by transpiling the tiny file on the fly.
const src = readFileSync(
  join(here, "..", "src", "lib", "predict.ts"), "utf-8",
);
const js = src
  .replace(/export interface [^}]+}/g, "")
  .replace(/: PredictInput|: UpperBounds|: number|: string/g, "")
  .replace(/export function/g, "function");
const predictUpperBounds = new Function(`${js}; return predictUpperBounds;`)();

let failures = 0;
for (const { input, expected } of fixture.cases) {
  const got = predictUpperBounds(input);
  for (const key of Object.keys(expected)) {
    if (got[key] !== expected[key]) {
      failures++;
      console.error(
        `DRIFT ${JSON.stringify(input)} ${key}: py=${expected[key]} ts=${got[key]}`,
      );
    }
  }
}
if (failures) {
  console.error(`estimator drift: ${failures} mismatch(es)`);
  process.exit(1);
}
console.log(`estimator fixture: ${fixture.cases.length} cases, 0 drift`);
