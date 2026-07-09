"""Image entry point: ``python -m lore``.

The wheel-only container has no source tree for ``fastmcp run`` to point at,
so the image launches the server through this module. Dev and the repo keep
``fastmcp run`` (via ``fastmcp.json``). Both spellings import the same
``server()`` factory and defer to ``run_async``, which reads transport and
banner from ``FASTMCP_*`` env, so the two are runtime-equivalent.
"""

from lore.server import server


def main() -> None:
    server().run()


if __name__ == "__main__":
    main()
