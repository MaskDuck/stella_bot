import asyncio
import itertools
from typing import Union

import discord
from discord.context_managers import Typing
from discord.ext import commands

from utils.errors import UserLocked


class BreakableTyping(Typing):
    def __init__(self, messageable, /, *, limit=None):
        self.loop = messageable._state.loop
        self.messageable = messageable
        self.limit = limit

    async def cancel_typing(self):
        self.limit -= 5
        values = await asyncio.wait(
            {self.task, asyncio.sleep(self.limit)},
            return_when=asyncio.FIRST_COMPLETED
        )
        for t in itertools.chain.from_iterable(values):
            t.cancel()

    def __enter__(self):
        super().__enter__()
        if self.limit is not None:
            self.loop.create_task(self.cancel_typing())
        return self


class UserLock:
    def __init__(self, user: Union[discord.Member, discord.User, discord.Object], error_message: str):
        self.user = user
        self.error_message = error_message
        self.lock = asyncio.Lock()

    def __call__(self, bot):
        bot.add_user_lock(self)
        return self.lock

    def locked(self):
        return self.lock.locked()

    @property
    def error(self):
        return UserLocked(message=self.error_message)
