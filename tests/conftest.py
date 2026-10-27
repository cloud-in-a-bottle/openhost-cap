import time
from collections.abc import Iterator
from http.cookiejar import Cookie

import pytest
import requests
from openhost_test_harness import OpenhostStack


@pytest.fixture(scope="session")
def stack() -> Iterator[OpenhostStack]:
    """Builds the Dockerfile and deploys the app through the real router; returns once the app is running."""
    # The image build pulls several large upstream images plus apt and npm installs (~2 min in CI).
    with OpenhostStack(deploy_timeout=900) as s:
        yield s


class _SecureOverHttpJar(requests.cookies.RequestsCookieJar):
    """Sends Secure cookies over http too, as browsers do for http://*.localhost (a secure context).

    Cap's session cookie is always Secure, and the harness serves the app over http. (A cookie policy whose
    return_ok_secure allows it isn't enough: the jar still never sends them.)
    """

    def set_cookie(self, cookie: Cookie, *args: object, **kwargs: object) -> None:
        cookie.secure = False
        super().set_cookie(cookie, *args, **kwargs)


@pytest.fixture(scope="session")
def cap_session(stack: OpenhostStack) -> requests.Session:
    """The owner signed in to Cap, the way Cap's login page does it in a browser.

    Cap's login page signs the owner in through NextAuth's "trusted-proxy" provider, which trusts the router's
    X-OpenHost-Is-Owner header and logs into the account entrypoint.sh seeds from the owner's username and the zone.
    The seed runs in the background after boot, so retry for a while.
    """
    # A copy of the owner's session (router cookie + the Fetch-Metadata headers its auth needs), as the cookies get
    # modified below.
    session = requests.Session()
    session.headers.update(stack.owner_session.headers)
    session.cookies = _SecureOverHttpJar()
    session.cookies.update(stack.owner_session.cookies)
    deadline = time.monotonic() + 180
    while True:
        user = None
        detail = ""
        try:
            csrf = session.get(f"{stack.url}/api/auth/csrf", timeout=30)
            detail = f"GET /api/auth/csrf returned {csrf.status_code}: {csrf.text[:500]}"
            if csrf.status_code == 200:
                session.post(
                    f"{stack.url}/api/auth/callback/trusted-proxy",
                    data={"csrfToken": csrf.json()["csrfToken"], "json": "true"},
                    allow_redirects=False,
                    timeout=30,
                )
                me = session.get(f"{stack.url}/api/auth/session", timeout=30)
                detail = f"GET /api/auth/session returned {me.status_code}: {me.text[:500]}"
                # NextAuth returns {} when signed out.
                user = me.json().get("user") if me.status_code == 200 else None
        except requests.ConnectionError as e:
            # The app or a kept-alive connection can drop while Cap finishes booting.
            detail = f"connection error: {e}"
        if user is not None or time.monotonic() > deadline:
            break
        time.sleep(5)
    assert user is not None, f"owner isn't signed in ({detail})\n\napp logs:\n{stack.app_logs()[-5000:]}"
    return session
