import hikari
import threading

def foo(bot: hikari.GatewayBotAware) -> None:
    thread = threading.current_thread().name

    @bot.event_manager.listen()
    async def _(event: hikari.Event) -> None:
        print(thread, type(event))
