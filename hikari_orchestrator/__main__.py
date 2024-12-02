# BSD 3-Clause License
#
# Copyright (c) 2023-2024, Faster Speeding
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""An GRPC powered service for orchestrating Hikari clusters between processes and machines."""
from __future__ import annotations

import importlib
import logging
import sys
import typing

import click
import dotenv
import hikari

from . import _service  # pyright: ignore[reportPrivateUsage]

if typing.TYPE_CHECKING:
    import io
    from collections import abc as collections

_ENV_PREFIX = "ORCHESTRATOR_"


def _env_name(name: str) -> str:
    return f"{_ENV_PREFIX}{name}"


def _cast_intents(value: str, /) -> hikari.Intents:
    try:
        int_value = int(value)
    except ValueError:
        pass

    else:
        return hikari.Intents(int_value)

    intents = hikari.Intents.NONE
    for name in value.upper().split("|"):
        try:
            intents |= hikari.Intents[name.strip()]

        except KeyError:
            error_message = f"{name!r} is not a valid intent"
            raise ValueError(error_message) from None

    return intents


_HELP = """
Command line entry points for Hikari Orchestrator: a tool for managing Hikari shard clusters."""


@click.group(help=_HELP, invoke_without_command=True)
def _cli_entry() -> None: ...


_RUN_HELP = """
Run a standalone multi-process Hikari bot on the current system.

Both `--entrypoint` and `--token` must be passed (either directly or as env variables).
"""


@_cli_entry.command(name="run", help=_RUN_HELP)
@click.option(
    "--entrypoint",
    "-ep",
    envvar=_env_name("ENTRYPOINT"),
    show_envvar=True,
    help=(
        "Path to the function which will be called with each initialised bot object. "
        "This must be in the format '{module_path}:{function_name}'."
    ),
    required=True,
)
@click.option("--token", envvar="DISCORD_TOKEN", help="Discord token for the bot to orchestrate.", required=True)
@click.option(
    "--intents",
    default=hikari.Intents.ALL_UNPRIVILEGED,
    envvar=_env_name("INTENTS"),
    show_envvar=True,
    show_default=True,
    help="Gateway intents the bot should use.",
    type=_cast_intents,
)
@click.option(
    "--shard-count",
    default=None,
    envvar=_env_name("SHARD_COUNT"),
    show_envvar=True,
    help="The amount of shards to run for the bot. Defaults to Discord's recommended amount.",
    type=int,
)
@click.option("--log-level", default="INFO", envvar="LOG_LEVEL", help="A Python logging level name.", show_default=True)
@click.option(
    "--process-count",
    default=_service.DEFAULT_SUBPROCESS_COUNT,
    envvar=_env_name("PROCESS_COUNT"),
    show_envvar=True,
    help="The amount of child processes to spawn.",
    show_default=True,
    type=int,
)
@click.option(
    "--entrypoint-dir",
    default=".",
    envvar=_env_name("ENTRYPOINT_DIR"),
    show_envvar=True,
    help="Look for the entrypoint's module in the specified directory.",
    show_default=True,
)
def _run_cmd(  # pyright: ignore[reportUnusedFunction]
    entrypoint: str,
    token: str,
    intents: hikari.Intents,
    shard_count: int | None,
    log_level: str,
    process_count: int,
    entrypoint_dir: str,
) -> None:
    sys.path.insert(0, entrypoint_dir)
    logging.basicConfig(level=log_level.upper())
    module_path, callback_name = entrypoint.split(":", 1)
    callback = getattr(importlib.import_module(module_path), callback_name, "<NOT FOUND>")

    if not callable(callback):
        error_message = f"{entrypoint!r} ({callback!r}) is not a function"
        raise TypeError(error_message)

    callback = typing.cast("collections.Callable[..., typing.Any]", callback)
    _service.run_subprocesses(
        token, callback=callback, intents=intents, shard_count=shard_count, subprocess_count=process_count
    )


_SERVER_HELP = """
Run a Hikari Orchestrator server instance.

The `ADDRESS` for this server will default to TCP if no scheme is included and
the valid schemes can be found at
https://github.com/grpc/grpc/blob/master/doc/naming.md
"""


@_cli_entry.command(name="server", help=_SERVER_HELP)
@click.argument("address", default="localhost:0", envvar=_env_name("ADDRESS"))
@click.option("--token", envvar="DISCORD_TOKEN", help="Discord token for the bot to orchestrate.", required=True)
@click.option(
    "--intents",
    default=hikari.Intents.ALL_UNPRIVILEGED,
    envvar=_env_name("INTENTS"),
    show_envvar=True,
    help="Gateway intents the bot should use.",
    show_default=True,
    type=_cast_intents,
)
@click.option(
    "--shard-count",
    default=None,
    envvar=_env_name("SHARD_COUNT"),
    show_envvar=True,
    help="The amount of shards to run for the bot. Defaults to Discord's recommended amount.",
    type=int,
)
@click.option("--log-level", default="INFO", envvar="LOG_LEVEL", help="A Python logging level name.", show_default=True)
@click.option(
    "--ca-cert",
    default=None,
    envvar=_env_name("CA_CERT"),
    show_envvar=True,
    help="System path to an unencrypted PEM certificate authority to use for encrypting TCP connections.",
    type=click.File("rb"),
)
@click.option(
    "--private-key",
    default=None,
    envvar=_env_name("PRIVATE_KEY"),
    show_envvar=True,
    help="System path to an unencrypted PEM private key to use for authenticating TCP connections.",
    type=click.File("rb"),
)
def _server_cmd(  # pyright: ignore[reportUnusedFunction]
    address: str,
    token: str,
    intents: hikari.Intents,
    shard_count: int | None,
    log_level: str,
    ca_cert: io.BytesIO | None,
    private_key: io.BytesIO | None,
) -> None:
    logging.basicConfig(level=log_level.upper())

    if ca_cert:
        ca_cert_data = ca_cert.read()
        ca_cert.close()

    else:
        ca_cert_data = None

    if private_key:
        private_key_data = private_key.read()
        private_key.close()

    else:
        private_key_data = None

    _service.run_server(
        token, address, ca_cert=ca_cert_data, private_key=private_key_data, intents=intents, shard_count=shard_count
    )


def main() -> None:
    """Hikari Orchestrator entry point."""
    dotenv.load_dotenv()
    _cli_entry()


if __name__ == "__main__":
    main()
