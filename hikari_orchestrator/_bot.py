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
import math
from collections import abc as collections

import hikari
import hikari.impl.event_factory  # TODO: export at hikari.impl
import hikari.urls

from . import _client  # type: ignore[reportPrivateUsage]
from . import _protos


class Bot(hikari.GatewayBotAware):
    """Bot implementation which is managed by an Orchestrator server."""

    __slots__ = (
        "_cache_settings",
        "_cache",
        "_ca_cert",
        "_close_event",
        "_entity_factory",
        "_event_factory",
        "_event_manager",
        "_global_shard_count",
        "_http_settings",
        "_intents",
        "_local_shard_count",
        "_local_shard_ids",
        "_manager",
        "_orchestrator_address",
        "_proxy_settings",
        "_rest",
        "_shards",
        "_token",
        "_voice",
    )

    def __init__(
        self,
        orchestrator_address: str,
        token: str,
        /,
        *,
        cache_settings: hikari.impl.CacheSettings | None = None,
        ca_cert: bytes | None = None,
        http_settings: hikari.impl.HTTPSettings | None = None,
        intents: hikari.Intents | int | None = None,
        proxy_settings: hikari.impl.ProxySettings | None = None,
        rest_url: str = hikari.urls.REST_API_URL,
        global_shard_count: int | None = None,
        local_shard_count: int = 1,
    ) -> None:
        """Initialise an orchestrator Bot.

        Parameters
        ----------
        orchestrator_address
            Address the orchestrator server is hosted at.
        token
            Discord bot token to use.
        cache_settings
            The cache settings to use.
        ca_cert
            Certificate authority certificate used by the orchestrator server for
            TLS SSL.
        http_settings
            Configuration to use for the REST client.
        proxy_settings
            Custom proxy settings to use with network-layer logic
            in your application to get through an HTTP-proxy.
        rest_url
            Base URl to use for requests made by the REST client.
        local_shard_count
            Amount of shards this bot should spawn locally.
        """
        self._cache_settings = cache_settings or hikari.impl.CacheSettings()
        self._cache = hikari.impl.CacheImpl(self, self._cache_settings)
        self._ca_cert = ca_cert
        self._close_event: asyncio.Event | None = None
        self._intents = hikari.Intents(intents) if intents is not None else None
        self._entity_factory = hikari.impl.EntityFactoryImpl(self)
        # TODO: export at hikari.impl
        self._event_factory = hikari.impl.event_factory.EventFactoryImpl(self)
        # Have to default intents here then hack in the real values later when intents is None.
        self._event_manager = hikari.impl.EventManagerImpl(
            self._entity_factory, self._event_factory, hikari.Intents.ALL if self._intents is None else self._intents
        )
        self._global_shard_count = global_shard_count
        self._http_settings = http_settings or hikari.impl.HTTPSettings()
        self._local_shard_count = local_shard_count
        self._local_shard_ids: list[int] = []
        self._orchestrator_address = orchestrator_address
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
        self._shards: dict[int, hikari.api.GatewayShard] = {}
        self._voice = hikari.impl.VoiceComponentImpl(self)
        self._token = token
        self._manager = _client.Client(self._token, self._orchestrator_address, ca_cert=self._ca_cert)

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
        if self._intents is None:
            return hikari.Intents.ALL  # This isn't known yet, assume ALL.

        return self._intents

    @property
    def heartbeat_latencies(self) -> collections.Mapping[int, float]:
        return {shard.id: shard.heartbeat_latency for shard in self._shards.values()}

    @property
    def heartbeat_latency(self) -> float:
        latencies = [
            shard.heartbeat_latency for shard in self._shards.values() if not math.isnan(shard.heartbeat_latency)
        ]
        return sum(latencies) / len(latencies) if latencies else float("nan")

    @property
    def is_alive(self) -> bool:
        return self._close_event is not None

    @property
    def shards(self) -> collections.Mapping[int, hikari.api.GatewayShard]:
        return self._shards.copy()

    @property
    def shard_count(self) -> int:
        if self._global_shard_count is None:
            return self._local_shard_count  # This isn't known yet, return the local count

        return self._global_shard_count

    def get_me(self) -> hikari.OwnUser | None:
        raise NotImplementedError

    def _get_shard(self, guild: hikari.SnowflakeishOr[hikari.PartialGuild]) -> hikari.api.GatewayShard:
        guild = hikari.Snowflake(guild)
        return self._shards[hikari.snowflakes.calculate_shard_id(self.shard_count, guild)]

    async def update_presence(
        self,
        *,
        idle_since: hikari.UndefinedNoneOr[datetime.datetime] = hikari.UNDEFINED,
        status: hikari.UndefinedOr[hikari.Status] = hikari.UNDEFINED,
        activity: hikari.UndefinedNoneOr[hikari.Activity] = hikari.UNDEFINED,
        afk: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
    ) -> None:
        await self._manager.update_presence(idle_since=idle_since, status=status, activity=activity, afk=afk)

    async def update_voice_state(
        self,
        guild: hikari.SnowflakeishOr[hikari.PartialGuild],
        channel: hikari.SnowflakeishOr[hikari.GuildVoiceChannel] | None,
        *,
        self_mute: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        self_deaf: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
    ) -> None:
        await self._get_shard(guild).update_voice_state(guild, channel, self_mute=self_mute, self_deaf=self_deaf)

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
        await self._get_shard(guild).request_guild_members(
            guild, include_presences=include_presences, query=query, limit=limit, users=users, nonce=nonce
        )

    async def join(self) -> None:
        if not self._close_event:
            raise hikari.ComponentStateConflictError("Not running")

        await self._close_event.wait()

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.start())
        loop.run_until_complete(self.join())

    async def close(self) -> None:
        if not self._close_event:
            raise hikari.ComponentStateConflictError("Not running")

        # TODO: inform the server of these closes
        await self._event_manager.dispatch(self._event_factory.deserialize_stopping_event())
        await self._manager.stop()
        await self._voice.close()
        await asyncio.gather(*(self._shards[shard_id].close() for shard_id in self._local_shard_ids))
        self._local_shard_ids.clear()
        self._shards.clear()
        self._close_event.set()
        self._close_event = None
        await self._event_manager.dispatch(self._event_factory.deserialize_stopped_event())
        await self._rest.close()

    def _make_shard(self, shard_state: _protos.Shard, /) -> hikari.impl.GatewayShardImpl:
        assert self._global_shard_count is not None
        assert self._intents is not None
        return hikari.impl.GatewayShardImpl(
            event_factory=self._event_factory,
            event_manager=self._event_manager,
            http_settings=self._http_settings,
            intents=self._intents,
            proxy_settings=self._proxy_settings,
            shard_id=shard_state.shard_id,
            token=self._token,
            shard_count=self._global_shard_count,
            url=shard_state.gateway_url,
        )

    async def _spawn_shard(self) -> None:
        shard = await self._manager.recommended_shard(self._make_shard)
        self._shards[shard.id] = shard
        self._local_shard_ids.append(shard.id)

    async def start(self) -> None:
        if self._close_event:
            raise hikari.ComponentStateConflictError("Already running")

        self._close_event = asyncio.Event()
        await self._manager.start()
        self._shards.update(self._manager.remote_shards)

        if self._global_shard_count is None or self._intents is None:
            config = await self._manager.fetch_config()
            if self._global_shard_count is None:
                self._global_shard_count = config.shard_count

            if self._intents is None:
                self._intents = hikari.Intents(config.intents)
                # TODO: find better work-around.
                self._event_manager._intents = self._intents  # pyright: ignore[reportPrivateUsage]

        # TODO: is there a smarter way to handle this?
        if self._local_shard_count > self._global_shard_count:
            raise RuntimeError("Local shard count can't be greater than the global shard count")

        self._voice.start()
        self._rest.start()
        await self._event_manager.dispatch(self._event_factory.deserialize_starting_event())
        await asyncio.gather(*(self._spawn_shard() for _ in range(self._local_shard_count)))
        await self._event_manager.dispatch(self._event_factory.deserialize_started_event())
