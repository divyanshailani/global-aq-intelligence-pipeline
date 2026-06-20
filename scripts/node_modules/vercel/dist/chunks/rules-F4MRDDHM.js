import { createRequire as __createRequire } from 'node:module';
import { fileURLToPath as __fileURLToPath } from 'node:url';
import { dirname as __dirname_ } from 'node:path';
const require = __createRequire(import.meta.url);
const __filename = __fileURLToPath(import.meta.url);
const __dirname = __dirname_(__filename);
import {
  getSubcommand
} from "./chunk-YPQSDAEW.js";
import {
  AGENT_REASON
} from "./chunk-LJ5WXXG6.js";
import {
  buildCommandWithGlobalFlags,
  outputAgentError
} from "./chunk-NHGCQRK5.js";
import "./chunk-LN6B7ZI3.js";
import "./chunk-P4QNYOFB.js";
import {
  output_manager_default
} from "./chunk-Z5SBJH6L.js";
import "./chunk-S7KYDPEM.js";
import "./chunk-TZ2YI2VH.js";

// src/commands/alerts/rules/index.ts
var RULES_CONFIG = {
  ls: ["ls", "list"],
  add: ["add", "create"],
  inspect: ["inspect", "get"],
  rm: ["rm", "remove", "delete"],
  update: ["update", "patch"]
};
async function rules(client, argv) {
  if (argv.length === 0) {
    const lsFn = (await import("./ls-PBSOEXFG.js")).default;
    return lsFn(client, []);
  }
  const { subcommand, args, subcommandOriginal } = getSubcommand(
    argv,
    RULES_CONFIG
  );
  if (subcommand == null) {
    outputAgentError(
      client,
      {
        status: "error",
        reason: AGENT_REASON.INVALID_ARGUMENTS,
        message: `Unknown "alerts rules" subcommand "${argv[0]}".`,
        next: [
          {
            command: buildCommandWithGlobalFlags(
              client.argv,
              "alerts rules --help"
            ),
            when: "Show valid rules subcommands"
          }
        ]
      },
      1
    );
    output_manager_default.error(
      `Unknown "alerts rules" subcommand "${argv[0]}". Run \`vercel alerts rules --help\`.`
    );
    return 1;
  }
  switch (subcommand) {
    case "ls":
      return (await import("./ls-PBSOEXFG.js")).default(client, args);
    case "add":
      return (await import("./add-PKT6ULVJ.js")).default(client, args);
    case "inspect":
      return (await import("./rule-inspect-3YK2XSL3.js")).default(client, args);
    case "rm":
      return (await import("./rm-MCSGTU4J.js")).default(client, args);
    case "update":
      return (await import("./update-XB4WSRGY.js")).default(client, args);
    default:
      output_manager_default.error(`Unhandled rules subcommand: ${String(subcommandOriginal)}`);
      return 1;
  }
}
export {
  rules as default
};
