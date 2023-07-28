# -*- coding: utf-8 -*-
# Tanjun Examples - A collection of examples for Tanjun.
# Written in 2023 by Faster Speeding Lucina@lmbyrne.dev
#
# To the extent possible under law, the author(s) have dedicated all copyright
# and related and neighboring rights to this software to the public domain worldwide.
# This software is distributed without any warranty.
#
# You should have received a copy of the CC0 Public Domain Dedication along with this software.
# If not, see <https://creativecommons.org/publicdomain/zero/1.0/>.
import os

import hikari
import tanjun

import hikari_orchestrator


def subprocess_bot_example():
    def create_bot(bot: hikari.GatewayBotAware) -> None:
        tanjun.Client.from_gateway_bot(bot)

    hikari_orchestrator.run_subprocesses(os.environ["BOT_TOKEN"], callback=create_bot)


def distributed_bot_child():
    bot = hikari_orchestrator.Bot("localhost:6969", os.environ["BOT_TOKEN"], local_shard_count=1)

    tanjun.Client.from_gateway_bot(bot)

    bot.run()
