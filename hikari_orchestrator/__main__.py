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

from . import _service  # pyright: ignore[reportPrivateUsage]


@click.command()
@click.argument("address", default="localhost:0", envvar="ORCHESTRATOR_ADDRESS")
@click.option("--log-level", default="INFO", envvar="LOG_LEVEL")
@click.option("--token", envvar="DISCORD_TOKEN", required=True)
@click.option("--ca-cert", default=None, envvar="ORCHESTRATOR_CA_CERT", type=click.File("rb"))
@click.option("--private-key", default=None, envvar="ORCHESTRATOR_PRIVATE_KEY", type=click.File("rb"))
def main(address: str, token: str, ca_cert: io.BytesIO | None, log_level: str, private_key: io.BytesIO | None) -> None:
    logging.basicConfig(level=log_level.capitalize())
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

    _service.run_server(token, address, ca_cert=ca_cert_data, private_key=private_key_data)


if __name__ == "__main__":
    dotenv.load_dotenv()
    main()
