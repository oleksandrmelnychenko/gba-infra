# DEV secret templates

These files define the complete secret-file contract used by the DEV Compose
overlay. Values are intentionally invalid placeholders.

Run `scripts/local-dev.sh init`, replace every `CHANGE_ME` value in the created
`secrets/dev` directory, and keep the real directory outside Git. The SQL SA
password embedded in local connection strings must match `SQL_SA_PASSWORD` in
`.env.dev`.

For a team handoff, transfer the populated `secrets/dev` directory and
`.env.dev` over an authenticated encrypted channel such as SSH. Never paste
their contents into chat, tickets, documentation, or source control. Keep files
mode `600`; a root-owned file may be `640` only when its group is the dedicated
container runtime group and has no write permission.
