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

gba-server signs its access tokens with RS256. `scripts/local-dev.sh init` generates the key pair (3072-bit RSA) when these files still hold placeholders, and `check` verifies that they match. To create it by hand:

    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out Security__JwtPrivateKey
    openssl pkey -in Security__JwtPrivateKey -pubout -out Security__JwtPublicKey

Only data-concord mounts `Security__JwtPrivateKey`. data-analytics and the shop
API mount `Security__JwtPublicKey`. `ShopAuth__JwtKey` is the shop's own HS256
key (at least 32 random bytes, different from `Security__JwtKey`).
