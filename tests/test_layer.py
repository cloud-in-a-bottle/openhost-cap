from urllib.parse import urlparse

import requests
from openhost_test_harness import OpenhostStack

HTML = {"Accept": "text/html"}


def _refused_by_router(stack: OpenhostStack, response: requests.Response) -> bool:
    # The router answers an anonymous request to a private path with 401, or a redirect to its own /login (on the
    # zone, not the app's subdomain; Cap's own login page is /login on the app's subdomain).
    if response.status_code == 401:
        return True
    location = urlparse(response.headers.get("Location", ""))
    return response.is_redirect and location.path == "/login" and location.netloc == stack.zone_domain


def test_anonymous_access(stack: OpenhostStack) -> None:
    # public_paths lets anonymous visitors open share links and play videos; the dashboard stays owner-only.
    dashboard = requests.get(f"{stack.url}/dashboard", headers=HTML, allow_redirects=False, timeout=30)
    assert _refused_by_router(stack, dashboard), f"{dashboard.status_code} {dashboard.headers}: {dashboard.text[:500]}"

    # Video objects under /cap/ are served by the bundled MinIO through Caddy (an S3 XML answer).
    blob = requests.get(f"{stack.url}/cap/doesnotexist.mp4", allow_redirects=False, timeout=30)
    assert "<Error>" in blob.text, f"/cap/ isn't MinIO: {blob.status_code}: {blob.text[:500]}"

    # A share link reaches Cap, not the router's login. (Cap answers an unknown video id with a 500.)
    share = requests.get(f"{stack.url}/s/doesnotexist", headers=HTML, allow_redirects=False, timeout=30)
    assert not _refused_by_router(stack, share), f"share link refused: {share.status_code} {share.headers}"

    # Caddy answers @vercel/firewall's probe with a 404 so rate-limited actions (eg Loom import) fail open.
    probe = requests.get(f"{stack.url}/.well-known/vercel/probe", allow_redirects=False, timeout=30)
    assert probe.status_code == 404, f"{probe.status_code}: {probe.text[:500]}"


def test_public_url_from_zone(stack: OpenhostStack) -> None:
    # entrypoint.sh builds Cap's public URL (NEXTAUTH_URL, WEB_URL, the presigned S3 endpoint) from the app name, the
    # zone domain (with its port on a dev router) and the router's scheme. NextAuth builds its URLs from it.
    providers = stack.owner_session.get(f"{stack.url}/api/auth/providers", timeout=30)
    assert providers.status_code == 200, f"{providers.status_code}: {providers.text[:500]}"
    signin = providers.json()["trusted-proxy"]["signinUrl"]
    assert signin == f"{stack.url}/api/auth/signin/trusted-proxy", signin


def test_data_in_app_data(stack: OpenhostStack) -> None:
    # MySQL's data and the generated secrets live in the app data dir, so they're backed up and survive redeploys.
    assert (stack.data_dir / "mysql8").is_dir(), sorted(p.name for p in stack.data_dir.iterdir())
    assert (stack.data_dir / "secrets.env").is_file()
