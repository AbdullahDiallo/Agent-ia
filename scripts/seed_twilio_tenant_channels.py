from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy import create_engine, text


TWILIO_WEBHOOK_PROVIDERS: tuple[str, ...] = (
    "twilio_voice",
    "twilio_events",
    "twilio_recording",
)


@dataclass
class UpsertResult:
    provider: str
    action: str  # created | updated | unchanged | deactivated


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_uuid(value: str) -> UUID:
    return UUID(str(value).strip())


@dataclass
class TenantRow:
    id: str
    slug: str | None
    name: str | None
    is_active: bool | None


def _load_dotenv() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ[key] = value


def _get_required_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise ValueError(f"missing_env:{name}")
    return value


def _resolve_tenant(conn, *, tenant_id: str | None, tenant_slug: str | None) -> TenantRow:
    if tenant_id:
        normalized = str(_normalize_uuid(tenant_id))
        row = conn.execute(
            text(
                """
                select cast(id as text) as id, slug, name, is_active
                from tenants
                where id = cast(:tenant_id as uuid)
                limit 1
                """
            ),
            {"tenant_id": normalized},
        ).mappings().first()
    elif tenant_slug:
        row = conn.execute(
            text(
                """
                select cast(id as text) as id, slug, name, is_active
                from tenants
                where slug = :tenant_slug
                limit 1
                """
            ),
            {"tenant_slug": tenant_slug.strip()},
        ).mappings().first()
    else:
        row = None
    if not row:
        raise ValueError("tenant_not_found")
    return TenantRow(
        id=str(row["id"]),
        slug=row.get("slug"),
        name=row.get("name"),
        is_active=row.get("is_active"),
    )


def _default_provider_key(tenant: TenantRow) -> str:
    slug = (tenant.slug or "").strip()
    if slug:
        return f"twilio-{slug}"
    return f"twilio-{str(tenant.id)[:8]}"


def _upsert_channels(
    conn,
    *,
    tenant: TenantRow,
    provider_key: str,
    token_hash: str,
    deactivate_other_keys: bool,
) -> list[UpsertResult]:
    results: list[UpsertResult] = []

    for provider in TWILIO_WEBHOOK_PROVIDERS:
        row = conn.execute(
            text(
                """
                select cast(id as text) as id, cast(tenant_id as text) as tenant_id, provider, provider_key, token_hash, is_active
                from tenant_channels
                where provider = :provider and provider_key = :provider_key
                limit 1
                """
            ),
            {"provider": provider, "provider_key": provider_key},
        ).mappings().first()

        if row:
            if str(row["tenant_id"]) != str(tenant.id):
                raise ValueError(
                    f"provider_key_conflict: provider={provider} provider_key={provider_key} "
                    f"belongs_to_tenant={row['tenant_id']}"
                )

            changed = False
            current_hash = str(row.get("token_hash") or "").strip().lower()
            current_active = bool(row.get("is_active"))
            if current_hash != token_hash.lower():
                changed = True
            if not current_active:
                changed = True

            if changed:
                conn.execute(
                    text(
                        """
                        update tenant_channels
                        set token_hash = :token_hash,
                            is_active = true,
                            updated_at = now()
                        where id = cast(:id as uuid)
                        """
                    ),
                    {"token_hash": token_hash, "id": row["id"]},
                )
            results.append(UpsertResult(provider=provider, action=("updated" if changed else "unchanged")))
            continue

        conn.execute(
            text(
                """
                insert into tenant_channels (id, tenant_id, provider, provider_key, token_hash, is_active)
                values (cast(:id as uuid), cast(:tenant_id as uuid), :provider, :provider_key, :token_hash, true)
                """
            ),
            {
                "id": str(UUID(bytes=secrets.token_bytes(16))),
                "tenant_id": str(tenant.id),
                "provider": provider,
                "provider_key": provider_key,
                "token_hash": token_hash,
            },
        )
        results.append(UpsertResult(provider=provider, action="created"))

    if deactivate_other_keys:
        rows = conn.execute(
            text(
                """
                select cast(id as text) as id, provider
                from tenant_channels
                where tenant_id = cast(:tenant_id as uuid)
                  and provider in ('twilio_voice', 'twilio_events', 'twilio_recording')
                  and provider_key != :provider_key
                  and is_active = true
                """
            ),
            {"tenant_id": str(tenant.id), "provider_key": provider_key},
        ).mappings().all()
        for row in rows:
            conn.execute(
                text(
                    """
                    update tenant_channels
                    set is_active = false, updated_at = now()
                    where id = cast(:id as uuid)
                    """
                ),
                {"id": row["id"]},
            )
            results.append(UpsertResult(provider=row["provider"], action="deactivated"))

    return results


def _upsert_widget_embed_channel(
    conn,
    *,
    tenant: TenantRow,
    allowed_origins: str = "",
) -> UpsertResult:
    """Create or update a widget_embed channel for the given tenant.

    The embed_key is a public, non-secret identifier (safe to put in HTML).
    It is derived from the tenant slug for readability.
    No token_hash is needed — authentication is via short-lived JWT session tokens.
    """
    slug = (tenant.slug or "").strip()
    embed_key = f"wgt-{slug}" if slug else f"wgt-{str(tenant.id)[:12]}"

    row = conn.execute(
        text(
            """
            select cast(id as text) as id, provider_key, allowed_origins, is_active
            from tenant_channels
            where provider = 'widget_embed'
              and tenant_id = cast(:tenant_id as uuid)
            limit 1
            """
        ),
        {"tenant_id": str(tenant.id)},
    ).mappings().first()

    if row:
        changed = (
            str(row.get("provider_key") or "") != embed_key
            or str(row.get("allowed_origins") or "") != allowed_origins
            or not row.get("is_active")
        )
        if changed:
            conn.execute(
                text(
                    """
                    update tenant_channels
                    set provider_key = :embed_key,
                        allowed_origins = :allowed_origins,
                        is_active = true,
                        updated_at = now()
                    where id = cast(:id as uuid)
                    """
                ),
                {"embed_key": embed_key, "allowed_origins": allowed_origins or None, "id": row["id"]},
            )
        return UpsertResult(provider="widget_embed", action=("updated" if changed else "unchanged"))

    conn.execute(
        text(
            """
            insert into tenant_channels (id, tenant_id, provider, provider_key, token_hash, is_active, allowed_origins)
            values (cast(:id as uuid), cast(:tenant_id as uuid), 'widget_embed', :embed_key, '', true, :allowed_origins)
            """
        ),
        {
            "id": str(UUID(bytes=secrets.token_bytes(16))),
            "tenant_id": str(tenant.id),
            "embed_key": embed_key,
            "allowed_origins": allowed_origins or None,
        },
    )
    return UpsertResult(provider="widget_embed", action="created")


def _build_url(base_url: str | None, path: str, provider_key: str, tenant_token: str) -> str:
    base = (base_url or "").rstrip("/")
    query = urlencode({"provider_key": provider_key, "tenant_token": tenant_token})
    if not base:
        return f"{path}?{query}"
    return f"{base}{path}?{query}"


def _parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create/update tenant_channels for Twilio webhooks "
            "(twilio_voice, twilio_events, twilio_recording)."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tenant-id", help="Tenant UUID")
    group.add_argument("--tenant-slug", help="Tenant slug")
    parser.add_argument(
        "--provider-key",
        help="Public provider_key to use. Default: twilio-<tenant-slug>",
    )
    parser.add_argument(
        "--tenant-token",
        help="Shared secret (cleartext). Default: generated URL-safe token",
    )
    parser.add_argument(
        "--deactivate-other-keys",
        action="store_true",
        help="Deactivate other active Twilio webhook keys for the same tenant.",
    )
    parser.add_argument(
        "--allowed-origins",
        default="",
        help="Comma-separated allowed origins for the widget_embed channel (e.g. https://school.com,https://www.school.com).",
    )
    parser.add_argument(
        "--skip-widget-embed",
        action="store_true",
        help="Do not create/update the widget_embed channel.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without committing.",
    )
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    _load_dotenv()

    try:
        database_url = _get_required_env("DATABASE_URL")
        public_base_url = (os.environ.get("PUBLIC_BASE_URL") or "").strip() or None
        engine = create_engine(database_url, pool_pre_ping=True)
    except ValueError as exc:
        message = str(exc)
        if message.startswith("missing_env:"):
            env_name = message.split(":", 1)[1]
            print(f"Erreur: variable d'environnement manquante: {env_name}", file=sys.stderr)
            return 2
        print(f"Erreur: {message}", file=sys.stderr)
        return 2

    try:
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                tenant = _resolve_tenant(conn, tenant_id=args.tenant_id, tenant_slug=args.tenant_slug)
                provider_key = (args.provider_key or "").strip() or _default_provider_key(tenant)
                tenant_token = (args.tenant_token or "").strip() or secrets.token_urlsafe(24)
                token_hash = _sha256_hex(tenant_token)

                results = _upsert_channels(
                    conn,
                    tenant=tenant,
                    provider_key=provider_key,
                    token_hash=token_hash,
                    deactivate_other_keys=bool(args.deactivate_other_keys),
                )

                # Also create/update the widget_embed channel (public embed_key for SaaS widgets)
                widget_result = None
                if not args.skip_widget_embed:
                    widget_result = _upsert_widget_embed_channel(
                        conn,
                        tenant=tenant,
                        allowed_origins=(args.allowed_origins or "").strip(),
                    )
                    results.append(widget_result)

                if args.dry_run:
                    tx.rollback()
                else:
                    tx.commit()
            except Exception:
                tx.rollback()
                raise

            print("")
            print("Twilio tenant_channels seed")
            print("-------------------------")
            print(f"mode: {'DRY-RUN' if args.dry_run else 'COMMIT'}")
            print(f"tenant_id: {tenant.id}")
            print(f"tenant_slug: {tenant.slug}")
            print(f"provider_key: {provider_key}")
            print(f"tenant_token: {tenant_token}")
            print("")
            print("Actions:")
            for item in results:
                print(f"  - {item.provider}: {item.action}")

            print("")
            print("Twilio URLs (copy/paste):")
            print(
                "  Voice Request URL:\n"
                f"    {_build_url(public_base_url, '/voice/incoming', provider_key, tenant_token)}"
            )
            print(
                "  Status Callback URL:\n"
                f"    {_build_url(public_base_url, '/events/call-status', provider_key, tenant_token)}"
            )
            print(
                "  Recording Status Callback URL:\n"
                f"    {_build_url(public_base_url, '/voice/recording-status', provider_key, tenant_token)}"
            )
            # Widget embed info
            if widget_result:
                slug = (tenant.slug or "").strip()
                embed_key = f"wgt-{slug}" if slug else f"wgt-{str(tenant.id)[:12]}"
                print("")
                print("Widget Embed (SaaS — no secrets in HTML):")
                print(f"  embed_key: {embed_key}")
                print(f"  Usage:     <script>window.AELIXORIATechWidgetVoiceConfig = {{ embedKey: '{embed_key}' }};</script>")
                if args.allowed_origins:
                    print(f"  allowed_origins: {args.allowed_origins}")

            print("")
            print("Note: conserve tenant_token en lieu sûr (seul son hash est stocké en base).")
            print("Note: embed_key est public et peut être mis dans le HTML (pas de secret).")
            return 0
    except ValueError as exc:
        message = str(exc)
        if message == "tenant_not_found":
            print("Erreur: tenant introuvable. Utilise --tenant-id ou --tenant-slug valide.", file=sys.stderr)
        else:
            print(f"Erreur: {message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Erreur inattendue: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
