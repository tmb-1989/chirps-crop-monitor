import os, subprocess


def secret(service: str, account: str | None = None, env: str | None = None) -> str:
    """Env override for local dev, else the macOS Keychain of the running user."""
    if env and os.environ.get(env):
        return os.environ[env].strip()
    account = account or os.environ.get("USER", "")
    out = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()
