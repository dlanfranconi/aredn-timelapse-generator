import argparse
import errno
import getpass
import hmac
import os
from datetime import datetime, timezone

import yaml
from werkzeug.security import check_password_hash, generate_password_hash

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"


def _load_raw_config(config_file_path: str) -> dict:
    if not os.path.exists(config_file_path):
        raise FileNotFoundError(f"Configuration file not found: {config_file_path}")
    with open(config_file_path, "r") as f:
        return yaml.safe_load(f) or {}


def _get_effective_config(raw_config: dict) -> dict:
    if isinstance(raw_config, dict) and isinstance(raw_config.get("config"), dict):
        return raw_config["config"]
    return raw_config


def _merge_effective_config(raw_config: dict, effective_config: dict) -> dict:
    if isinstance(raw_config, dict) and isinstance(raw_config.get("config"), dict):
        updated = dict(raw_config)
        updated["config"] = effective_config
        return updated
    return effective_config


def _write_yaml(config_file_path: str, raw_config: dict) -> None:
    tmp_path = f"{config_file_path}.tmp"
    with open(tmp_path, "w") as f:
        yaml.safe_dump(raw_config, f, sort_keys=False)
    try:
        os.replace(tmp_path, config_file_path)
    except OSError as exc:
        if exc.errno not in {errno.EBUSY, errno.EXDEV}:
            raise
        # Single-file Docker bind mounts can reject atomic replace. Fall back to
        # updating the mounted file in place so first-run admin bootstrap works.
        with open(config_file_path, "w") as f:
            yaml.safe_dump(raw_config, f, sort_keys=False)
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def user_has_password(user: dict) -> bool:
    return bool(user.get("password_hash") or user.get("password"))


def verify_password(user: dict, password: str) -> bool:
    password_hash = user.get("password_hash")
    if password_hash:
        return check_password_hash(str(password_hash), password)
    legacy_password = user.get("password")
    if legacy_password is not None:
        return hmac.compare_digest(str(legacy_password), password)
    return False


def ensure_default_admin_user(config_file_path: str) -> bool:
    """Create admin/admin only for configs that do not have a users block yet."""
    raw_config = _load_raw_config(config_file_path)
    config = _get_effective_config(raw_config)
    if "users" in config:
        return False

    config["users"] = {
        DEFAULT_ADMIN_USERNAME: {
            "role": "admin",
            "disabled": False,
            "ptz_access": "admin",
            "ptz_cameras": [],
            "password_hash": hash_password(DEFAULT_ADMIN_PASSWORD),
            "created_by": "default-bootstrap",
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    }
    _write_yaml(config_file_path, _merge_effective_config(raw_config, config))
    return True


def authenticate_config_user(
    config_file_path: str, username: str, password: str
) -> bool:
    ensure_default_admin_user(config_file_path)
    raw_config = _load_raw_config(config_file_path)
    config = _get_effective_config(raw_config)
    users = config.get("users") or {}
    user = users.get(username)
    if not isinstance(user, dict):
        return False
    if user.get("disabled", False):
        return False
    if user.get("role", "viewer") != "admin":
        return False
    return verify_password(user, password)


def reset_admin_user(config_file_path: str, password: str) -> None:
    raw_config = _load_raw_config(config_file_path)
    config = _get_effective_config(raw_config)
    users = config.setdefault("users", {})
    existing = dict(users.get(DEFAULT_ADMIN_USERNAME) or {})
    existing.update(
        {
            "role": "admin",
            "disabled": False,
            "ptz_access": "admin",
            "ptz_cameras": existing.get("ptz_cameras", []),
            "password_hash": hash_password(password),
        }
    )
    existing.pop("password", None)
    users[DEFAULT_ADMIN_USERNAME] = existing
    _write_yaml(config_file_path, _merge_effective_config(raw_config, config))


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Fenetre admin users.")
    parser.add_argument("--config", default="/srv/fenetre/config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    reset_parser = subparsers.add_parser("reset-admin")
    reset_parser.add_argument("--password")

    args = parser.parse_args()
    if args.command == "reset-admin":
        password = args.password
        if not password:
            password = getpass.getpass("New admin password: ")
        if not password:
            raise SystemExit("Password cannot be blank.")
        reset_admin_user(args.config, password)
        print(f"Reset {DEFAULT_ADMIN_USERNAME!r} admin password in {args.config}.")
