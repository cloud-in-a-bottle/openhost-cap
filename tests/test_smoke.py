import time

from openhost_test_harness import OpenhostStack


def test_app_serves_owner(stack: OpenhostStack) -> None:
    # The app is marked running once its health check passes; "/" can take a little longer to come up.
    deadline = time.monotonic() + 60
    while True:
        # Accept like a browser navigation: some apps only redirect "/" to their UI for HTML requests (eg pihole).
        response = stack.owner_session.get(f"{stack.url}/", headers={"Accept": "text/html"}, timeout=30)
        if response.status_code < 400 or time.monotonic() > deadline:
            break
        time.sleep(2)
    assert response.status_code < 400, (
        f"GET / returned {response.status_code}: {response.text[:500]}\n\napp logs:\n{stack.app_logs()[-5000:]}"
    )


def test_owner_signs_in_via_trusted_proxy(stack: OpenhostStack) -> None:
    # Cap's login page signs the owner in through NextAuth's "trusted-proxy" provider, which trusts the router's
    # X-OpenHost-Is-Owner header and logs into the account entrypoint.sh seeds from the owner's username and the zone.
    # Do the same requests the login page makes, then check the session is the owner's. The seed runs in the
    # background after boot, so retry for a while.
    session = stack.owner_session
    deadline = time.monotonic() + 180
    while True:
        csrf = session.get(f"{stack.url}/api/auth/csrf", timeout=30)
        user = None
        if csrf.status_code == 200:
            session.post(
                f"{stack.url}/api/auth/callback/trusted-proxy",
                data={"csrfToken": csrf.json()["csrfToken"], "json": "true"},
                allow_redirects=False,
                timeout=30,
            )
            me = session.get(f"{stack.url}/api/auth/session", timeout=30)
            # NextAuth returns {} when signed out.
            user = me.json().get("user") if me.status_code == 200 else None
        if user is not None or time.monotonic() > deadline:
            break
        time.sleep(5)
    assert user is not None, (
        f"owner isn't signed in (GET /api/auth/csrf returned {csrf.status_code}: {csrf.text[:500]})"
        f"\n\napp logs:\n{stack.app_logs()[-5000:]}"
    )
    assert user["email"].startswith(f"{stack.owner_username}@"), user
