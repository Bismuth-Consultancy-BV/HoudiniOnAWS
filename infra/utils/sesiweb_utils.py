import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import sesiweb.webapi
from sesiweb import SesiWeb
from sesiweb.model.service import ProductBuild

from .aws_utils import get_aws_secrets


def _create_compatible_session() -> requests.Session:
    """Create a requests session with retry strategy compatible with urllib3 2.x.

    sesiweb 0.1.1 uses the deprecated 'method_whitelist' parameter in
    urllib3.util.retry.Retry, which was removed in urllib3 2.0.
    This function replaces sesiweb's get_session() with a compatible version.
    """
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# Patch sesiweb's broken get_session before any SesiWeb instances are created
sesiweb.webapi.get_session = _create_compatible_session


def get_houdini_download_info(
    houdini_version, aws_region: str, sidefx_secrets_name: str
) -> dict:
    """Get download URL and hash for Houdini version"""
    try:
        # Get credentials
        all_sidefx_secrets = get_aws_secrets(aws_region, sidefx_secrets_name)
        sidefx_client = all_sidefx_secrets.get("sidefx_client")
        sidefx_secret = all_sidefx_secrets.get("sidefx_secret")

        # Create SesiWeb service (uses patched get_session)
        service = SesiWeb(client_secret=sidefx_secret, client_id=sidefx_client)

        product_build = {
            "product": "houdini",
            "platform": "linux",
            "version": houdini_version[:-4],  # Remove build number
        }
        build_filter = {"status": "good", "release": "gold"}

        print(f"Fetching latest builds for Houdini {houdini_version}...")
        latest_builds = service.get_latest_builds(
            prodinfo=product_build, prodfilter=build_filter
        )

        for build in latest_builds:
            if build.build == houdini_version[5:]:  # Remove "houdini" prefix
                print(f"Found matching build: {build.build}")
                build_info = service.get_build_download(
                    prodinfo=ProductBuild(**build.model_dump())
                )
                dl_request = build_info.model_dump()

                return {
                    "download_url": dl_request["download_url"],
                    "filename": dl_request["filename"],
                    "hash": dl_request["hash"],
                }

        raise Exception(f"No matching Houdini build found for {houdini_version}")

    except Exception as e:
        print(f"Error getting Houdini download info: {e}")
        raise
