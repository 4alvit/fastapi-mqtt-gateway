"""Generate an API verifier without putting the password in command arguments."""

import getpass
import sys

from fastapi_mqtt_gateway.core.passwords import hash_password


def main() -> None:
    password = getpass.getpass("API password (16+ characters): ")
    confirmation = getpass.getpass("Confirm API password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    try:
        encoded = hash_password(password)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    sys.stdout.write(encoded + "\n")


if __name__ == "__main__":
    main()
