# Logout Refresh Revocation Strategy (Option B)

Date: 2026-02-06

## Choix implemente
- **Option B**: `users.token_version` + claim JWT `tv`.

## Regle
- Chaque refresh token embarque `tv` (token version au moment de l'emission).
- Au `POST /auth/refresh`, le backend compare:
  - `tv` du token,
  - `users.token_version` courant en base.
- Si mismatch: `401 token_revoked`.

## Effet securite
- `POST /auth/logout` incremente `users.token_version`:
  - tous les refresh tokens emis avant logout deviennent invalides.
- `POST /auth/logout-all` applique la meme regle explicitement.
- La rotation refresh continue de blacklister l'ancien refresh par `jti`.

## Preuves tests
- `tests/test_auth_logout_refresh_revoke.py`
  - login -> refresh OK
  - logout -> ancien refresh 401
  - rotation -> ancien refresh 401
  - logout all -> tous refresh 401
