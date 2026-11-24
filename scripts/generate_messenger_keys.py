"""Generate and optionally persist Messenger secrets without printing them."""

import argparse
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def escaped_pem(value: bytes) -> str:
    return value.decode("ascii").strip().replace("\n", "\\n")


def generate_values() -> dict[str, str]:
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return {
        "MESSENGER_DB_PASSWORD": secrets.token_urlsafe(36),
        "MESSENGER_JWT_PRIVATE_KEY": escaped_pem(private_pem),
        "MESSENGER_JWT_PUBLIC_KEY": escaped_pem(public_pem),
        "BACKUP_ENCRYPTION_PASSPHRASE": secrets.token_urlsafe(48),
    }


def write_env(path: Path, values: dict[str, str]) -> None:
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        example = path.with_name(".env.example")
        lines = example.read_text(encoding="utf-8").splitlines() if example.exists() else []

    remaining = dict(values)
    output: list[str] = []
    for line in lines:
        key, separator, _ = line.partition("=")
        if separator and key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    if remaining:
        output.append("")
        output.append("# Generated persistent Messenger secrets")
        output.extend(f"{key}={value}" for key, value in remaining.items())

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write-env",
        type=Path,
        metavar="PATH",
        help="write fresh secrets to an env file without displaying their values",
    )
    args = parser.parse_args()
    values = generate_values()
    if args.write_env:
        write_env(args.write_env.resolve(), values)
        print(
            "Updated persistent values: "
            + ", ".join(values)
        )
        return
    for key, value in values.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
