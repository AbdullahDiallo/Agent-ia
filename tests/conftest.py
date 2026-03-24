import os


def _set_default_env(var_name: str, value: str) -> None:
    if not os.environ.get(var_name):
        os.environ[var_name] = value


_set_default_env("ENV", "test")
_set_default_env("DATABASE_URL", "sqlite:///./test.db")
_set_default_env(
    "JWT_PUBLIC_KEY",
    "-----BEGIN PUBLIC KEY-----\nTEST_PUBLIC_KEY\n-----END PUBLIC KEY-----",
)
_set_default_env(
    "JWT_PRIVATE_KEY",
    "-----BEGIN PRIVATE KEY-----\nTEST_PRIVATE_KEY\n-----END PRIVATE KEY-----",
)
_set_default_env("JWT_AUDIENCE", "agentia-tests")
_set_default_env("JWT_ISSUER", "agentia-tests")
_set_default_env("WEBHOOK_FAIL_CLOSED", "false")
_set_default_env("AUTH_SECURITY_FAIL_CLOSED", "false")
