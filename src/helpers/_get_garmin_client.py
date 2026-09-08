import contextlib
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from garminconnect import Garmin


@dataclass(frozen=True)
class GarminConfiguration:
    activity_fetch_limit: int


def get_garmin_client() -> tuple[Garmin, GarminConfiguration]:
    load_dotenv()

    print("Initializing Garmin client...")

    garmin_client = _get_garmin_client()
    garmin_configuration = _get_garmin_configuration()

    print("Garmin client authenticated successfully.")

    return garmin_client, garmin_configuration


def _get_garmin_client() -> Garmin:
    garmin_auth_token = os.getenv("GARMIN_AUTH_TOKEN")

    if not garmin_auth_token:
        raise ValueError(
            "GARMIN_AUTH_TOKEN is required. "
            "See README_AUTH_SETUP.md for instructions on generating a token."
        )

    # GARMIN_AUTH_TOKEN is passed as an inline JSON string (>512 chars), so the
    # library treats it as token data rather than a file path. This means the
    # access token is refreshed in memory on each run via diauth.garmin.com
    # (standard OAuth2 refresh — separate from the SSO endpoints that are
    # rate-limited).
    #
    # Garmin now rotates the underlying refresh token every few days, which
    # used to make every run after that fail with a 401. To fix this for
    # good, we write the freshly-refreshed token out to a file (path given by
    # GARMIN_TOKEN_OUTPUT_PATH) after every successful login. A separate
    # workflow step then saves that file back into the GARMIN_AUTH_TOKEN
    # secret via `gh secret set`, so the next run always starts current.
    garmin_client = Garmin()
    garmin_client.login(tokenstore=garmin_auth_token)

    token_output_path = os.getenv("GARMIN_TOKEN_OUTPUT_PATH")
    if token_output_path:
        with contextlib.suppress(Exception):
            with open(token_output_path, "w") as f:
                f.write(garmin_client.client.dumps())

    return garmin_client


def _get_garmin_configuration():
    return GarminConfiguration(
        activity_fetch_limit=int(os.getenv("GARMIN_ACTIVITIES_FETCH_LIMIT", "10")),
    )
