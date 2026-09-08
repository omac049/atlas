// Node harness: node run.js <schedule.json> '<inputs json>' -> result json. Used by the tests.
const fs = require("fs");
const path = require("path");
const engine = require(path.join(__dirname, "fees.js"));
const FV = engine.FeeVerified || engine;
const schedule = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const inputs = JSON.parse(process.argv[3] || "{}");
process.stdout.write(JSON.stringify(FV.computeFees(schedule, inputs)));
