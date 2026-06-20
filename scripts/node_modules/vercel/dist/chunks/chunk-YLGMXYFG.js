import { createRequire as __createRequire } from 'node:module';
import { fileURLToPath as __fileURLToPath } from 'node:url';
import { dirname as __dirname_ } from 'node:path';
const require = __createRequire(import.meta.url);
const __filename = __fileURLToPath(import.meta.url);
const __dirname = __dirname_(__filename);
import {
  printProjectNotFoundError,
  setupAndLink
} from "./chunk-372SHB43.js";
import {
  getLinkedProject,
  param,
  resolveProjectCwd
} from "./chunk-LQR3CHMH.js";
import {
  buildCommandWithYes,
  outputActionRequired
} from "./chunk-NHGCQRK5.js";
import {
  getCommandName,
  getCommandNamePlain
} from "./chunk-LN6B7ZI3.js";
import {
  output_manager_default
} from "./chunk-Z5SBJH6L.js";

// src/util/link/ensure-link.ts
async function ensureLink(commandName, client, cwd, opts = {}) {
  cwd = await resolveProjectCwd(cwd);
  let { link } = opts;
  const nonInteractive = opts.nonInteractive ?? client.nonInteractive ?? false;
  opts.nonInteractive = nonInteractive;
  if (!link) {
    if (opts.forceDelete) {
      link = { status: "not_linked", org: null, project: null };
    } else {
      link = await getLinkedProject(
        client,
        cwd,
        opts.projectName,
        opts.failIfNotFound
      );
    }
    opts.link = link;
  }
  if (link.status === "linked" && opts.forceDelete || link.status === "not_linked") {
    if (link.status === "not_linked" && opts.failIfNotFound && opts.projectName) {
      await printProjectNotFoundError(client, opts.projectName, commandName);
      return 1;
    }
    link = await setupAndLink(client, cwd, opts);
    if (link.status === "not_linked") {
      return 0;
    }
  }
  if (link.status === "error") {
    if (link.reason === "HEADLESS") {
      if (nonInteractive) {
        outputActionRequired(
          client,
          {
            status: "action_required",
            reason: "confirmation_required",
            message: `Command ${getCommandNamePlain(commandName)} requires confirmation. Use option --yes to confirm.`,
            next: [
              {
                command: buildCommandWithYes(client.argv),
                when: "Confirm and run"
              }
            ]
          },
          link.exitCode
        );
      } else {
        output_manager_default.error(
          `Command ${getCommandName(
            commandName
          )} requires confirmation. Use option ${param("--yes")} to confirm.`
        );
      }
    }
    if (nonInteractive) {
      process.exit(link.exitCode);
    }
    return link.exitCode;
  }
  return link;
}

export {
  ensureLink
};
