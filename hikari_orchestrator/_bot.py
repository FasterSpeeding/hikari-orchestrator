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
from collections import abc as collections

import grpc  # type: ignore
import hikari
import hikari.impl.event_factory
import hikari.urls

from . import _client

# class _RpcShard:
#     __slots__ = ()


class Bot(hikari.GatewayBotAware):
    __slots__ = (
        "_cache_settings",
        "_cache",
        "_close_event",
        "_credentials",
        "_entity_factory",
        "_event_factory",
        "_event_manager",
        "_gateway_url",
        "_global_shard_count",
        "_http_settings",
        "_intents",
        "_local_shard_count",
        "_manager",
        "_manager_address",
        "_proxy_settings",
        "_rest",
        "_shards",
        "_token",
        "_voice",
    )

    def __init__(
        self,
        manager_address: str,
        token: str,
        global_shard_count: int,
        local_shard_count: int,
        /,
        *,
        cache_settings: hikari.impl.CacheSettings | None = None,
        credentials: grpc.ChannelCredentials | None = None,
        gateway_url: str,
        http_settings: hikari.impl.HTTPSettings | None = None,
        intents: hikari.Intents | int = hikari.Intents.ALL_UNPRIVILEGED,
        proxy_settings: hikari.impl.ProxySettings | None = None,
        rest_url: str = hikari.urls.REST_API_URL,
    ) -> None:
        self._cache_settings = cache_settings or hikari.impl.CacheSettings()
        self._cache = hikari.impl.CacheImpl(self, self._cache_settings)
        self._close_event: asyncio.Event | None = None
        self._credentials = credentials
        self._intents = hikari.Intents(intents)
        self._entity_factory = hikari.impl.EntityFactoryImpl(self)
        # TODO: export at hikari.impl
        self._event_factory = hikari.impl.event_factory.EventFactoryImpl(self)
        self._event_manager = hikari.impl.EventManagerImpl(self._entity_factory, self._event_factory, self._intents)
        self._gateway_url = gateway_url
        self._global_shard_count = global_shard_count
        self._http_settings = http_settings or hikari.impl.HTTPSettings()
        self._local_shard_count = local_shard_count
        self._manager_address = manager_address
        self._proxy_settings = proxy_settings or hikari.impl.ProxySettings()
        self._rest = hikari.impl.RESTClientImpl(
            cache=self._cache,
            executor=None,
            rest_url=rest_url,
            entity_factory=self._entity_factory,
            http_settings=self._http_settings,
            proxy_settings=self._proxy_settings,
            token=token,
            token_type=hikari.TokenType.BOT,
        )
        self._shards: dict[int, hikari.impl.GatewayShardImpl] = {}
        self._voice = hikari.impl.VoiceComponentImpl(self)
        self._token = token
        self._manager = _client.Client()

    @property
    def cache(self) -> hikari.api.Cache:
        return self._cache

    @property
    def event_factory(self) -> hikari.api.EventFactory:
        return self._event_factory

    @property
    def event_manager(self) -> hikari.api.EventManager:
        return self._event_manager

    @property
    def voice(self) -> hikari.api.VoiceComponent:
        return self._voice

    @property
    def entity_factory(self) -> hikari.api.EntityFactory:
        return self._entity_factory

    @property
    def rest(self) -> hikari.api.RESTClient:
        return self._rest

    @property
    def executor(self) -> concurrent.futures.Executor | None:
        return None

    @property
    def http_settings(self) -> hikari.api.HTTPSettings:
        return self._http_settings

    @property
    def proxy_settings(self) -> hikari.api.ProxySettings:
        return self._proxy_settings

    @property
    def intents(self) -> hikari.Intents:
        return self._intents

    @property
    def heartbeat_latencies(self) -> collections.Mapping[int, float]:
        return {}
        # return self._heartbeat_latencies

    @property
    def heartbeat_latency(self) -> float:
        return super().heartbeat_latency

    @property
    def is_alive(self) -> bool:
        return self._close_event is not None

    @property
    def shards(self) -> collections.Mapping[int, hikari.api.GatewayShard]:
        return self._shards

    @property
    def shard_count(self) -> int:
        return self._global_shard_count

    def get_me(self) -> hikari.OwnUser | None:
        raise NotImplementedError

    async def update_presence(
        self,
        *,
        idle_since: hikari.UndefinedNoneOr[datetime.datetime] = hikari.UNDEFINED,
        status: hikari.UndefinedOr[hikari.Status] = hikari.UNDEFINED,
        activity: hikari.UndefinedNoneOr[hikari.Activity] = hikari.UNDEFINED,
        afk: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
    ) -> None:
        raise NotImplementedError

    async def update_voice_state(
        self,
        guild: hikari.SnowflakeishOr[hikari.PartialGuild],
        channel: hikari.SnowflakeishOr[hikari.GuildVoiceChannel] | None,
        *,
        self_mute: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        self_deaf: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
    ) -> None:
        raise NotImplementedError

    async def request_guild_members(
        self,
        guild: hikari.SnowflakeishOr[hikari.PartialGuild],
        *,
        include_presences: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        query: str = "",
        limit: int = 0,
        users: hikari.UndefinedOr[hikari.SnowflakeishSequence[hikari.User]] = hikari.UNDEFINED,
        nonce: hikari.UndefinedOr[str] = hikari.UNDEFINED,
    ) -> None:
        raise NotImplementedError

    async def join(self) -> None:
        if not self._close_event:
            raise RuntimeError("Not running")

        await self._close_event.wait()

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.start())
        loop.run_until_complete(self.join())

    async def close(self) -> None:
        if not self._close_event:
            raise RuntimeError("Not running")

        await self._event_manager.dispatch(self._event_factory.deserialize_stopping_event())
        await self._manager.stop()
        self._close_event.set()
        self._close_event = None
        await self._event_manager.dispatch(self._event_factory.deserialize_stopped_event())

    def _make_shard(self, shard_id: int, /) -> hikari.impl.GatewayShardImpl:
        return hikari.impl.GatewayShardImpl(
            event_factory=self._event_factory,
            event_manager=self._event_manager,
            http_settings=self._http_settings,
            intents=self._intents,
            proxy_settings=self._proxy_settings,
            shard_id=shard_id,
            token=self._token,
            shard_count=self._global_shard_count,
            url=self._gateway_url,
        )

    async def _spawn_shard(self) -> None:
        shard = await self._manager.recommended_shard(self._make_shard)
        self._shards[shard.id] = shard

    async def start(self) -> None:
        if self._close_event:
            raise RuntimeError("Already running")

        self._close_event = asyncio.Event()
        await self._manager.start(self._manager_address, credentials=self._credentials)
        await self._event_manager.dispatch(self._event_factory.deserialize_starting_event())
        await asyncio.gather(*(self._spawn_shard() for _ in range(self._local_shard_count)))
        await self._event_manager.dispatch(self._event_factory.deserialize_started_event())
