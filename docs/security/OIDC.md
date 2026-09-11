# OpenID Connect single sign-on

GaleQEA supports SSO with any OpenID Connect provider. Set the issuer plus a client
id/secret and it discovers the endpoints from `/.well-known/openid-configuration`;
the Login screen then shows **Sign in with SSO**. Users are created just-in-time on
first login, and their GaleQEA role is mapped from a groups/roles claim.

## Configuration

| Variable | Purpose |
| --- | --- |
| `GALEQEA_AUTH_OIDC_ISSUER` | Issuer URL, e.g. `https://idp.example/realms/galeqea` |
| `GALEQEA_AUTH_OIDC_CLIENT_ID` / `..._CLIENT_SECRET` | The confidential client's credentials |
| `GALEQEA_AUTH_OIDC_SCOPES` | Default `openid email profile` |
| `GALEQEA_AUTH_OIDC_GROUPS_CLAIM` | Claim holding the user's groups (default `groups`) |
| `GALEQEA_AUTH_OIDC_ROLE_MAP` | JSON, e.g. `{"galeqea-admins":"admin","galeqea-approvers":"approver"}` |
| `GALEQEA_AUTH_OIDC_DEFAULT_ROLE` | Role for a user in no mapped group (default `author`) |
| `GALEQEA_AUTH_OIDC_REDIRECT_BASE` | Public base URL for the callback (behind a proxy) |

Register the redirect URI `<public-url>/api/auth/oidc/callback` with your provider.
A user in several mapped groups gets the most-privileged role. GaleQEA roles, least
to most: `viewer` < `author` < `approver` < `admin` < `owner`.

## Providers

- **Keycloak**: a ready-to-run dev stack is at `deploy/compose/oidc-keycloak.yml`
  (`docker compose -f deploy/compose/oidc-keycloak.yml up`): it imports the `galeqea`
  realm with a `galeqea-admins` group and a test user `tester` / `tester-pass`, and
  wires GaleQEA to it on **:8098** (so it never collides with a dev server on :8080).
  Run it with `--build` so a clean checkout gets current code:
  `docker compose -f deploy/compose/oidc-keycloak.yml up --build`, then open
  http://localhost:8098. The bundled realm already includes a group-membership mapper
  named `groups`.
- **Okta**: create an OIDC Web app; issuer is `https://<org>.okta.com`; add a
  "Groups" claim to the ID token and set `GROUPS_CLAIM=groups`.
- **Microsoft Entra ID**: register an app; issuer
  `https://login.microsoftonline.com/<tenant>/v2.0`; emit group claims (or app roles)
  and point `GROUPS_CLAIM` at `groups` (or `roles`).
- **Authentik / Google / any OIDC**: same pattern, issuer + client id/secret + a
  groups claim.

## SAML / LDAP

GaleQEA speaks OIDC, not SAML or LDAP directly. Front it with **Keycloak** or
**Authentik** as an identity broker: they federate your SAML IdP or LDAP directory
and present OIDC to GaleQEA. This keeps one code path (OIDC) while supporting the
enterprise protocols through a permissively-licensed broker.
