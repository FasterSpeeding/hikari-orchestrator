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
""""""
from __future__ import annotations

import asyncio
import concurrent.futures
import datetime
import math
import os
from collections import abc as collections

import grpc.aio  # type: ignore
import hikari

from . import _bot
from . import _protos


def _now() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.timezone.utc)


class _TrackedShard:
    __slots__ = ("queue", "state")

    def __init__(self, shard_id: int, /) -> None:
        self.queue: asyncio.Queue[_protos.Instruction] | None = None
        self.state = _protos.Shard(state=_protos.STOPPED, shard_id=shard_id)


async def _handle_states(stored: _TrackedShard, request_iterator: collections.AsyncIterator[_protos.Shard]) -> None:
    async for shard_state in request_iterator:
        shard_state.last_seen.FromDatetime(_now())
        stored.state = shard_state


class Orchestrator(_protos.OrchestratorServicer):
    def __init__(self, token: str, shard_count: int, /, *, session_start_limit: hikari.SessionStartLimit) -> None:
        self._session_start_limit = session_start_limit
        self._shards: dict[int, _TrackedShard] = {shard_id: _TrackedShard(shard_id) for shard_id in range(shard_count)}
        self._token = token

    async def Acquire(
        self, request_iterator: collections.AsyncIterator[_protos.Shard], context: grpc.ServicerContext
    ) -> collections.AsyncIterator[_protos.Instruction]:
        shard_state = await anext(request_iterator)
        stored = self._shards.get(shard_state.shard_id)
        if not stored:
            raise KeyError("Shard not known")

        if stored.state.state is not _protos.STOPPED:
            raise NotImplementedError("Cannot take over right now")

        shard_state.last_seen.FromDatetime(_now())
        stored.state = shard_state
        state_event = asyncio.create_task(_handle_states(stored, request_iterator))

        queue = stored.queue = asyncio.Queue[_protos.Instruction]()
        queue_wait = asyncio.create_task(queue.get())

        try:
            while not state_event.done():
                completed, _ = await asyncio.wait((state_event, queue_wait), return_when=asyncio.FIRST_COMPLETED)
                if queue_wait in completed:
                    yield await queue_wait

                queue_wait = asyncio.create_task(queue.get())

        finally:
            queue_wait.cancel()
            state_event.cancel()
            stored.state.state = _protos.STOPPED
            stored.queue = None

    def Disconnect(self, request: _protos.ShardId, context: grpc.ServicerContext) -> _protos.DisconnectResult:
        shard = self._shards.get(request.shard_id)
        if not shard or not shard.queue:
            return _protos.DisconnectResult(_protos.FAILED)

        instruction = _protos.Instruction(_protos.DISCONNECT)
        shard.queue.put_nowait(instruction)
        return _protos.DisconnectResult(_protos.SUCCESS, shard.state)

    def GetState(self, request: _protos.ShardId, context: grpc.ServicerContext) -> _protos.Shard:
        return self._shards[request.shard_id].state


def _spawn_child(
    manager_address: str,
    token: str,
    shard_count: int,
    shard_ids: collections.Collection[int],
    /,
    *,
    credentials: grpc.ChannelCredentials,
    gateway_url: str,
    intents: hikari.Intents | int,
) -> None:
    bot = _bot.Bot(
        manager_address,
        token,
        shard_count,
        shard_ids,
        credentials=credentials,
        gateway_url=gateway_url,
        intents=intents,
    )
    bot.run()


async def spawn_subprocesses(
    token: str,
    /,
    *,
    shard_count: int | None = None,
    shard_ids: collections.Collection[int] | None = None,
    intents: hikari.Intents | int = hikari.Intents.ALL_UNPRIVILEGED,
    subprocess_count: int = os.cpu_count() or 1,
    port: int = 50551,
) -> None:
    rest_app = hikari.RESTApp()
    await rest_app.start()

    async with rest_app.acquire(token, hikari.TokenType.BOT) as acquire:
        gateway_info = await acquire.fetch_gateway_bot_info()

    shard_count = shard_count or gateway_info.shard_count
    orchestrator = Orchestrator(token, shard_count, session_start_limit=gateway_info.session_start_limit)

    server = grpc.aio.server()
    _protos.add_OrchestratorServicer_to_server(orchestrator, server)
    server.add_secure_port(f"localhost:{port}", grpc.local_server_credentials())
    await server.start()

    shard_ids = list(shard_ids) if shard_ids else range(shard_count)

    executor = concurrent.futures.ProcessPoolExecutor()
    chunk_size = math.ceil(len(shard_ids) / subprocess_count)
    for index in range(0, len(shard_ids), subprocess_count):
        executor.submit(
            _spawn_child,
            f"localhost:{port}",
            token,
            shard_count,
            shard_ids[index : index + chunk_size],
            credentials=grpc.local_channel_credentials(),
            gateway_url=gateway_info.url,
            intents=intents,
        )

    await server.wait_for_termination()
