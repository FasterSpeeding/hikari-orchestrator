# -*- coding: utf-8 -*-
# BSD 3-Clause License
#
# Copyright (c) 2023, Faster Speeding
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
from __future__ import annotations

import io
import logging

import click
import dotenv
import hikari

from . import _service  # pyright: ignore[reportPrivateUsage]


def _cast_token(value: str, /) -> hikari.Intents:
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
            raise ValueError(f"{name!r} is not a valid intent")

    return intents


_HELP = """
Run a Hikari Orchestrator server instance.
The `ADDRESS` for this server will default to TCP if no scheme is included and
the valid schemes can be found at
https://github.com/grpc/grpc/blob/master/doc/naming.md
"""


@click.command(help=_HELP)
@click.argument("address", default="localhost:0", envvar="ORCHESTRATOR_ADDRESS")
@click.option("--token", envvar="DISCORD_TOKEN", help="Discord token for the bot to orchestrate.", required=True)
@click.option(
    "--intents",
    default=hikari.Intents.ALL_UNPRIVILEGED,
    envvar="ORCHESTRATOR_INTENTS",
    help="Gateway intents the bot should use. Defaults to ALL_UNPRIVILEGED",
    type=_cast_token,
)
@click.option("--log-level", default="INFO", envvar="LOG_LEVEL", help="A Python logging level name. Defaults to INFO.")
@click.option(
    "--ca-cert",
    default=None,
    envvar="ORCHESTRATOR_CA_CERT",
    help="Path to an unencrypted PEM certificate authority to use for encrypting TCP connections.",
    type=click.File("rb"),
)
@click.option(
    "--private-key",
    default=None,
    help="Path to an unencrypted PEM private key to use for authenticating TCP connections.",
    type=click.File("rb"),
)
def main(
    address: str,
    token: str,
    ca_cert: io.BytesIO | None,
    intents: hikari.Intents,
    log_level: str,
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

    _service.run_server(token, address, ca_cert=ca_cert_data, private_key=private_key_data, intents=intents)


if __name__ == "__main__":
    dotenv.load_dotenv()
    main()
