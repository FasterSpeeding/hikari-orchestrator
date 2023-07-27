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

import asyncio
import concurrent.futures
import datetime
import hashlib
import logging
import math
import os
import secrets
import socket
import typing
import uuid
from collections import abc as collections

import grpc.aio  # type: ignore
import hikari
from google.protobuf import timestamp_pb2

from . import _bot  # pyright: ignore[reportPrivateUsage]
from . import _protos
from . import _ssl

_LOGGER = logging.getLogger("hikari.orchestrator")
_DEFAULT_SUBPROCESS_COUNT = os.cpu_count() or 1


def _now() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.timezone.utc)


class _TrackedShard:
    __slots__ = ("queue", "state")

    def __init__(self, shard_id: int, gateway_url: str, /) -> None:
        self.queue: asyncio.Queue[_protos.Instruction] | None = None
        last_seen = timestamp_pb2.Timestamp()
        last_seen.FromDatetime(_now())
        self.state = _protos.Shard(
            latency=float("nan"), last_seen=last_seen, state=_protos.STOPPED, shard_id=shard_id, gateway_url=gateway_url
        )

    def update_state(self, state: _protos.Shard, /) -> None:
        state.last_seen.FromDatetime(_now())
        # TODO: support reconnects
        state.gateway_url = self.state.gateway_url
        self.state = state


async def _release_after_5(semaphore: asyncio.BoundedSemaphore) -> None:
    await asyncio.sleep(5)
    semaphore.release()


def hash_token(token: str) -> str:
    return hashlib.scrypt(token.encode(), salt=token.rsplit(".", 1)[-1].encode(), n=2048, r=8, p=1).hex()


class _AuthInterceptor(grpc.aio.ServerInterceptor):
    def __init__(self, token: str, /) -> None:
        self._token_hash = hash_token(token)

    async def intercept_service(
        self,
        continuation: collections.Callable[[grpc.HandlerCallDetails], collections.Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        authorisation = typing.cast(
            "str | None", dict(handler_call_details.invocation_metadata).get("authorization")  # type: ignore
        )

        if not authorisation or not secrets.compare_digest(
            self._token_hash, authorisation.removeprefix("Bearer ")  # "token "
        ):
            raise KeyError("Invalid auth")

        # abort_with_status?
        return await continuation(handler_call_details)


class Orchestrator(_protos.OrchestratorServicer):
    def __init__(
        self,
        token: str,
        gateway_info: hikari.GatewayBotInfo,
        /,
        *,
        shard_count: int | None = None,
        intents: hikari.Intents | int = hikari.Intents.ALL_UNPRIVILEGED,
    ) -> None:
        if shard_count is None:
            shard_count = gateway_info.shard_count

        self._buckets = {
            bucket: asyncio.BoundedSemaphore() for bucket in range(gateway_info.session_start_limit.max_concurrency)
        }
        self._intents = intents
        self._shards: dict[int, _TrackedShard] = {
            shard_id: _TrackedShard(shard_id, gateway_info.url) for shard_id in range(shard_count)
        }
        self._shard_count_zfill = len(str(shard_count))
        self._tasks: list[asyncio.Task[None]] = []
        self._token = token

    def _store_task(self, task: asyncio.Task[None], /) -> None:
        task.add_done_callback(self._tasks.remove)
        self._tasks.append(task)

    def _char_fill(self, value: str, /) -> str:
        return value * self._shard_count_zfill

    def _zfill(self, value: int, /) -> str:
        return str(value).zfill(self._shard_count_zfill)

    def Acquire(
        self, request_iterator: collections.AsyncIterator[_protos.Shard], context: grpc.ServicerContext
    ) -> collections.AsyncIterator[_protos.Instruction]:
        raise NotImplementedError

    async def AcquireNext(
        self, request_iterator: collections.AsyncIterator[_protos.Shard], context: grpc.ServicerContext
    ) -> collections.AsyncIterator[_protos.Instruction]:
        for shard in self._shards.values():
            if shard.state.state is _protos.ShardState.STOPPED:
                break

        else:
            _LOGGER.warning("Received AcquireNext request but all shards taken")
            yield _protos.Instruction(type=_protos.InstructionType.DISCONNECT)
            return

        shard_id = shard.state.shard_id
        log_shard_id = self._zfill(shard_id)
        _LOGGER.info("Shard %s: AcquireNext request received", log_shard_id)
        shard.state.state = _protos.ShardState.STARTING
        semaphore = self._buckets[shard_id % len(self._buckets)]
        await semaphore.acquire()

        _LOGGER.info("Shard %s: Starting", log_shard_id)
        yield _protos.Instruction(type=_protos.InstructionType.CONNECT, shard_id=shard_id, shard_state=shard.state)

        state = await anext(aiter(request_iterator))
        _LOGGER.info("Shard %s: Started", log_shard_id)

        shard.update_state(state)
        self._store_task(asyncio.create_task(_release_after_5(semaphore)))

        state_event = asyncio.create_task(self._handle_states(shard, request_iterator))
        queue = shard.queue = asyncio.Queue[_protos.Instruction]()
        queue_wait = asyncio.create_task(queue.get())

        try:
            while not state_event.done():
                completed, _ = await asyncio.wait((state_event, queue_wait), return_when=asyncio.FIRST_COMPLETED)
                if queue_wait in completed:
                    yield await queue_wait

                queue_wait = asyncio.create_task(queue.get())

        finally:
            _LOGGER.info("Shard %s went way", log_shard_id)
            queue_wait.cancel()
            state_event.cancel()
            shard.state.state = _protos.STOPPED
            shard.queue = None

    async def _handle_states(
        self, stored: _TrackedShard, request_iterator: collections.AsyncIterator[_protos.Shard]
    ) -> None:
        async for shard_state in request_iterator:
            _LOGGER.debug("Shard %s: Received state update", self._zfill(stored.state.shard_id))
            stored.update_state(shard_state)

    def Disconnect(self, request: _protos.ShardId, _: grpc.ServicerContext) -> _protos.DisconnectResult:
        shard = self._shards.get(request.shard_id)
        if not shard or not shard.queue:
            _LOGGER.warning(
                "Shard %s: Received invalid Disconnect request; shard %s",
                self._zfill(request.shard_id),
                "is in-active" if shard else "doesn't exist",
            )
            return _protos.DisconnectResult(status=_protos.FAILED)

        _LOGGER.info("Shard %s: Received Disconnect request", self._zfill(request.shard_id))
        instruction = _protos.Instruction(type=_protos.DISCONNECT)
        shard.queue.put_nowait(instruction)
        return _protos.DisconnectResult(status=_protos.SUCCESS, state=shard.state)

    def GetState(self, request: _protos.ShardId, _: grpc.ServicerContext) -> _protos.Shard:
        _LOGGER.debug("Shard %s: Received GetState request", self._zfill(request.shard_id))
        return self._shards[request.shard_id].state

    async def GetAllStates(self, _: _protos.Undefined, __: grpc.ServicerContext) -> _protos.AllShards:
        _LOGGER.debug("Shard %s: Received GetAllStates request", self._char_fill("*"))
        return _protos.AllShards(shards=(shard.state for shard in self._shards.values()))

    async def SendPayload(self, request: _protos.GatewayPayload, context: grpc.ServicerContext) -> _protos.Undefined:
        match request.WhichOneof("payload"):
            case "presence_update":
                payload = request.presence_update
                shard_id = request.shard_id
                shard_id_repr = self._char_fill("*") if shard_id is None else self._zfill(shard_id)
                _LOGGER.debug("Shard %s: Received request_guild_members SendPayload request", shard_id_repr)

            case "voice_state":
                payload = request.voice_state
                shard_id = hikari.snowflakes.calculate_shard_id(len(self._shards), payload.guild_id)
                _LOGGER.debug("Shard %s: Received voice_state SendPayload request", shard_id)

            case "request_guild_members":
                payload = request.request_guild_members
                shard_id = hikari.snowflakes.calculate_shard_id(len(self._shards), payload.guild_id)
                _LOGGER.debug("Shard %s: Received request_guild_members SendPayload request", shard_id)

            case value:
                shard_id_repr = self._char_fill("?") if request.shard_id is None else self._zfill(request.shard_id)
                _LOGGER.warning("Shard %s: Received unexpected %s SendPayload request", shard_id_repr, value)
                return _protos.Undefined()

        return _protos.Undefined()

    async def GetConfig(self, _: _protos.Undefined, __: grpc.ServicerContext) -> _protos.Config:
        _LOGGER.debug("Shard %s: Received GetConfig request", self._char_fill("*"))
        return _protos.Config(shard_count=len(self._shards), intents=self._intents)


def _spawn_child(
    orchestrator_address: str,
    token: str,
    global_shard_count: int,
    local_shard_count: int,
    callback: collections.Callable[[hikari.GatewayBotAware], None] | None,
    ca_cert: bytes | None,
    intents: hikari.Intents | int,
) -> None:
    bot = _bot.Bot(
        orchestrator_address,
        token,
        ca_cert=ca_cert,
        intents=intents,
        global_shard_count=global_shard_count,
        local_shard_count=local_shard_count,
    )
    if callback:
        callback(bot)

    bot.run()


async def _fetch_bot_info(token: str, /) -> hikari.GatewayBotInfo:
    rest_app = hikari.RESTApp()
    await rest_app.start()

    try:
        async with rest_app.acquire(token, hikari.TokenType.BOT) as acquire:
            return await acquire.fetch_gateway_bot_info()

    finally:
        await rest_app.close()


async def _spawn_server(
    token: str,
    address: str,
    /,
    *,
    gateway_info: hikari.GatewayBotInfo | None = None,
    intents: hikari.Intents | int = hikari.Intents.ALL_UNPRIVILEGED,
    # TODO: can this generate a private key from the CA cert easily?
    private_key: bytes | None = None,
    ca_cert: bytes | None = None,
    shard_count: int | None = None,
) -> tuple[int | None, grpc.aio.Server]:
    gateway_info = gateway_info or await _fetch_bot_info(token)
    orchestrator = Orchestrator(token, gateway_info, shard_count=shard_count, intents=intents)

    server = grpc.aio.server(interceptors=[_AuthInterceptor(token)])
    _protos.add_OrchestratorServicer_to_server(orchestrator, server)

    if private_key and ca_cert:
        credentials = grpc.ssl_server_credentials([(private_key, ca_cert)])
        port = server.add_secure_port(address, credentials)

    elif private_key or ca_cert:
        raise RuntimeError("private_key and ca_cert must be both passed")

    else:
        port = server.add_insecure_port(address)

    if address.startswith("unix"):
        port = None
        _LOGGER.info("Starting server at %s", address)

    else:
        _LOGGER.info("Starting server at %s:%s", address.rsplit(":", 1)[0], port)

    await server.start()
    return port, server


def run_server(
    token: str,
    address: str,
    /,
    *,
    # gateway_info: hikari.GatewayBotInfo | None = None,
    intents: hikari.Intents | int = hikari.Intents.ALL_UNPRIVILEGED,
    ca_cert: bytes | None = None,
    private_key: bytes | None = None,
    shard_count: int | None = None,
) -> None:
    """Run an Orchestrator server instance.

    Parameters
    ----------
    token
        Discord token of the bot this orchestrator serve is managing.
    address
        The address to host the server at.

        This defaults to TCP if no scheme is included and more information on
        the supported schemes can be found
        [here](https://github.com/grpc/grpc/blob/master/doc/naming.md).
    intents
        The Discord gateway intents this bot should run with.
    ca_cert
        Bytes of the unencrypted PEM certificate authority this should use for
        TLS SSL encryption.

        `private_key` must also be passed when this is provided.
    private_key
        Bytes of the unencrypted private PEM key this server should use for
        TLS SSL encryption.

        `ca_cert` must also be passed when this is proivded.
    shard_count
        The amount of shards this bot should be split into.

        If left as [None][] then this defaults to the shard count
        recommended by Discord.
    """
    loop = asyncio.new_event_loop()
    _, server = loop.run_until_complete(
        _spawn_server(
            token,
            address,
            ca_cert=ca_cert,
            # gateway_info=gateway_info,
            intents=intents,
            private_key=private_key,
            shard_count=shard_count,
        )
    )
    loop.run_until_complete(server.wait_for_termination())


async def spawn_subprocesses(
    token: str,
    /,
    *,
    callback: collections.Callable[[hikari.GatewayBotAware], None] | None = None,
    shard_count: int | None = None,
    intents: hikari.Intents | int = hikari.Intents.ALL_UNPRIVILEGED,
    subprocess_count: int = _DEFAULT_SUBPROCESS_COUNT,
) -> None:
    """Asynchronously variant of [run_subprocesses][hikari_orchestrator.run_subprocesses].

    The returned coroutine will finish once the server has closed.
    """
    gateway_info = await _fetch_bot_info(token)
    global_shard_count = shard_count or gateway_info.shard_count
    local_shard_count = global_shard_count / subprocess_count

    if local_shard_count < 1:
        local_shard_count = 1
        subprocess_count = global_shard_count

    else:
        local_shard_count = math.ceil(local_shard_count)

    if hasattr(socket, "AF_UNIX"):
        ca_cert = None
        private_key = None
        server_address = f"unix-abstract:{uuid.uuid4().hex}"

    else:
        ca_cert, private_key = _ssl.gen_ca()
        server_address = "localhost:0"

    port, server = await _spawn_server(
        token,
        server_address,
        gateway_info=gateway_info,
        intents=intents,
        private_key=private_key,
        ca_cert=ca_cert,
        shard_count=global_shard_count,
    )

    if port is not None:
        server_address = f"localhost:{port}"

    executor = concurrent.futures.ProcessPoolExecutor()
    loop = asyncio.get_running_loop()
    for _ in range(subprocess_count):
        loop.run_in_executor(
            executor,
            _spawn_child,
            server_address,
            token,
            global_shard_count,
            local_shard_count,
            callback,
            ca_cert,
            intents,
        )

    await server.wait_for_termination()


def run_subprocesses(
    token: str,
    /,
    *,
    callback: collections.Callable[[hikari.GatewayBotAware], None] | None = None,
    shard_count: int | None = None,
    intents: hikari.Intents | int = hikari.Intents.ALL_UNPRIVILEGED,
    subprocess_count: int = _DEFAULT_SUBPROCESS_COUNT,
) -> None:
    """Run a bot across several procsesses.

    Parameters
    ----------
    token
        Discord token of the bot
    callback
        Callback to call each child bot instance with on startup.

        This is used to setup the bot.
    shard_count
        The amount of shards this bot should be split into.

        If left as [None][] then this defaults to the shard count
        recommended by Discord.
    intents
        The Discord gateway intents this bot should run with.
    subprocess_count
        The amount of subprocesses to spawn.

        If left as undefined then the current system's thread count is used.
        This will never spawn more subprocesses than `shard_count`.
    """
    asyncio.run(
        spawn_subprocesses(
            token, callback=callback, shard_count=shard_count, intents=intents, subprocess_count=subprocess_count
        )
    )
