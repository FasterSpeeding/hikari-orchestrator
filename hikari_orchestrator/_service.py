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
        raise NotImplementedError

    async def AcquireNext(
        self, request_iterator: collections.AsyncIterator[_protos.Shard], context: grpc.ServicerContext
    ) -> collections.AsyncIterator[_protos.Instruction]:
        for shard in self._shards.values():
            if shard.state.state is _protos.ShardState.STOPPED:
                break

        else:
            yield _protos.Instruction(type=_protos.InstructionType.DISCONNECT)
            return

        shard.state.state = _protos.ShardState.STARTING
        state_event = asyncio.create_task(_handle_states(shard, request_iterator))

        # TODO: work out scheduling
        yield _protos.Instruction(type=_protos.InstructionType.CONNECT, shard_id=shard.state.shard_id)

        queue = shard.queue = asyncio.Queue[_protos.Instruction]()
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
            shard.state.state = _protos.STOPPED
            shard.queue = None

    def Disconnect(self, request: _protos.ShardId, _: grpc.ServicerContext) -> _protos.DisconnectResult:
        shard = self._shards.get(request.shard_id)
        if not shard or not shard.queue:
            return _protos.DisconnectResult(_protos.FAILED)

        instruction = _protos.Instruction(_protos.DISCONNECT)
        shard.queue.put_nowait(instruction)
        return _protos.DisconnectResult(_protos.SUCCESS, shard.state)

    def GetState(self, request: _protos.ShardId, _: grpc.ServicerContext) -> _protos.Shard:
        return self._shards[request.shard_id].state

    async def SendPayload(self, request: _protos.GatewayPayload, context: grpc.ServicerContext) -> _protos.Empty:
        return _protos.Empty()


def _spawn_child(
    manager_address: str,
    token: str,
    global_shard_count: int,
    local_shard_count: int,
    callback: collections.Callable[[hikari.GatewayBotAware], None] | None,
    # credentials: grpc.ChannelCredentials | None,  # TODO: Can't be pickled
    gateway_url: str,
    intents: hikari.Intents | int,
) -> None:
    bot = _bot.Bot(
        manager_address,
        token,
        global_shard_count,
        local_shard_count,
        credentials=grpc.local_channel_credentials(),
        gateway_url=gateway_url,
        intents=intents,
    )
    if callback:
        callback(bot)

    bot.run()


async def spawn_subprocesses(
    token: str,
    /,
    *,
    callback: collections.Callable[[hikari.GatewayBotAware], None] | None = None,
    shard_count: int | None = None,
    intents: hikari.Intents | int = hikari.Intents.ALL_UNPRIVILEGED,
    subprocess_count: int = os.cpu_count() or 1,
) -> None:
    rest_app = hikari.RESTApp()
    await rest_app.start()

    async with rest_app.acquire(token, hikari.TokenType.BOT) as acquire:
        gateway_info = await acquire.fetch_gateway_bot_info()

    global_shard_count = shard_count or gateway_info.shard_count
    local_shard_count = math.ceil(global_shard_count / subprocess_count)
    orchestrator = Orchestrator(token, global_shard_count, session_start_limit=gateway_info.session_start_limit)

    server = grpc.aio.server()
    _protos.add_OrchestratorServicer_to_server(orchestrator, server)
    port = server.add_secure_port("[::]:0", grpc.local_server_credentials())
    await server.start()

    executor = concurrent.futures.ProcessPoolExecutor()
    loop = asyncio.get_running_loop()
    for _ in range(subprocess_count):
        loop.run_in_executor(
            executor,
            _spawn_child,
            f"localhost:{port}",
            token,
            global_shard_count,
            local_shard_count,
            callback,
            gateway_info.url,
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
    subprocess_count: int = os.cpu_count() or 1,
) -> None:
    asyncio.run(
        spawn_subprocesses(
            token, callback=callback, shard_count=shard_count, intents=intents, subprocess_count=subprocess_count
        )
    )
