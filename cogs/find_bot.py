import discord
import datetime
import re
import asyncio
import itertools
import ctypes
import contextlib
import humanize
import functools
import collections
from dataclasses import dataclass
from discord.ext import commands
from discord.ext.commands import UserNotFound
from discord.ext.menus import ListPageSource
from utils import flags as flg
from utils.new_converters import BotPrefix, BotUsage, IsBot
from utils.useful import try_call, BaseEmbed, compile_prefix, search_prefix, MenuBase, default_date, plural, realign
from utils.errors import NotInDatabase, BotNotFound
from utils.decorators import is_discordpy, event_check, wait_ready, Pages


@dataclass
class BotAdded:
    """BotAdded information for discord.py that is used in whoadd and whatadd command."""
    author: discord.Member = None
    bot: discord.Member = None
    reason: str = None
    requested_at: datetime.datetime = None
    jump_url: str = None
    joined_at: datetime.datetime = None

    @classmethod
    def from_json(cls, bot=None, *, bot_id=None, **data):
        """factory method on data from a dictionary like object into BotAdded."""
        author = data.pop("author_id", None)
        join = data.pop("joined_at", None)
        bot = bot or bot_id
        if isinstance(bot, discord.Member):
            join = bot.joined_at
            author = bot.guild.get_member(author) or author

        return cls(author=author, bot=bot, joined_at=join, **data)

    @classmethod
    async def convert(cls, ctx, argument):
        """Invokes when the BotAdded is use as a typehint."""
        with contextlib.suppress(commands.BadArgument):
            if user := await IsBot().convert(ctx, argument, cls=BotAdded):
                for attribute in ("pending", "confirmed")[isinstance(user, discord.Member):]:
                    attribute += "_bots"
                    if user.id in getattr(ctx.bot, attribute):
                        data = await ctx.bot.pool_pg.fetchrow(f"SELECT * FROM {attribute} WHERE bot_id = $1", user.id)
                        return cls.from_json(user, **data)
                raise NotInDatabase(user, converter=cls)
        raise BotNotFound(argument, converter=cls)

    def __str__(self):
        return str(self.bot or "")


def pprefix(bot_guild, prefix):
    if content := re.search("<@(!?)(?P<id>[0-9]*)>", prefix):
        method = getattr(bot_guild, ("get_user","get_member")[isinstance(bot_guild, discord.Guild)])
        if user := method(int(content["id"])):
            return f"@{user.display_name} "
    return prefix


class AllPrefixes(ListPageSource):
    """Menu for allprefix command."""
    def __init__(self, data, count_mode):
        super().__init__(data, per_page=6)
        self.count_mode = count_mode

    async def format_page(self, menu: MenuBase, entries):
        key = "(\u200b|\u200b)"
        offset = menu.current_page * self.per_page
        content = "`{no}. {prefix} {key} {b.count}`" if self.count_mode else "`{no}. {b} {key} {prefix}`"
        contents = [content.format(no=i+1, b=b, key=key, prefix=pprefix(menu.ctx.guild, b.prefix)) for i, b in enumerate(entries, start=offset)]
        embed = BaseEmbed(title="All Prefixes",
                          description="\n".join(realign(contents, key)))
        return menu.generate_page(embed, self._max_pages)


@Pages(per_page=10)
async def all_bot_count(self, menu: MenuBase, entries):
    """Menu for botrank command."""
    key = "(\u200b|\u200b)"
    offset = menu.current_page * self.per_page
    content = "`{no}. {b} {key} {b.count}`"
    contents = [content.format(no=i+1, b=b, key=key) for i, b in enumerate(entries, start=offset)]
    embed = BaseEmbed(title="Bot Command Rank",
                      description="\n".join(realign(contents, key)))
    return menu.generate_page(embed, self._max_pages)


@Pages(per_page=6)
async def bot_added_list(self, menu: MenuBase, entries):
    """Menu for recentbotadd command."""
    offset = menu.current_page * self.per_page
    contents = ((f"{b.author}", f'**{b}** `{humanize.precisedelta(b.joined_at)}`')
                for i, b in enumerate(entries, start=offset))

    embed = BaseEmbed(title="Bots added today")
    for n, v in contents:
        embed.add_field(name=n, value=v, inline=False)
    return menu.generate_page(embed, self._max_pages)


def is_user():
    """Event check for returning true if it's a bot."""
    return event_check(lambda _, m: not m.author.bot)


async def command_count_check(self, message):
    """Event check for command_count"""
    return self.compiled_pref and not message.author.bot and message.guild


def dpy_bot():
    """Event check for dpy_bots"""
    return event_check(lambda _, member: member.bot and member.guild.id == 336642139381301249)


class FindBot(commands.Cog, name="Bots"):
    def __init__(self, bot):
        self.bot = bot
        self.help_trigger = {}
        valid_prefix = ("!", "?", "？", "<@(!?)80528701850124288> ")
        re_command = "(\{}|\{}|\{}|({}))addbot".format(*valid_prefix)
        re_bot = "[\s|\n]+(?P<id>[0-9]{17,19})[\s|\n]"
        re_reason = "+(?P<reason>.[\s\S\r]+)"
        self.re_addbot = re_command + re_bot + re_reason
        self.compiled_pref = None
        self.all_bot_prefixes = {}
        bot.loop.create_task(self.loading_all_prefixes())

    async def loading_all_prefixes(self):
        """Loads all unique prefix when it loads and set compiled_pref for C code."""
        await self.bot.wait_until_ready()
        datas = await self.bot.pool_pg.fetch("SELECT * FROM bot_prefix_list")
        for data in datas:
            prefixes = self.all_bot_prefixes.setdefault(data["bot_id"], {})
            prefixes.update({data["prefix"]: data["usage"]})
        self.update_compile()

    def update_compile(self):
        temp = [*{prefix for prefixes in self.all_bot_prefixes.values() for prefix in prefixes}]
        self.compiled_pref = compile_prefix(sorted(temp))

    @commands.Cog.listener("on_member_join")
    @wait_ready()
    @dpy_bot()
    async def join_bot_tracker(self, member):
        """Tracks when a bot joins in discord.py where it logs all the BotAdded information."""
        if member.id in self.bot.pending_bots:
            data = await self.bot.pool_pg.fetchrow("SELECT * FROM pending_bots WHERE bot_id = $1", member.id)
            await self.update_confirm(BotAdded.from_json(member, **data))
            await self.bot.pool_pg.execute("DELETE FROM pending_bots WHERE bot_id = $1", member.id)
        else:
            await self.update_confirm(BotAdded.from_json(member, joined_at=member.joined_at))

    @commands.Cog.listener("on_member_remove")
    @wait_ready()
    @dpy_bot()
    async def remove_bot_tracker(self, member):
        if member.id in self.bot.confirmed_bots:
            await self.bot.pool_pg.execute("DELETE FROM confirmed_bots WHERE bot_id=$1", member.id)
            self.bot.confirmed_bots.remove(member.id)

    async def update_prefix_bot(self, message, func, prefix):
        """Updates the prefix of a bot, or multiple bot where it waits for the bot to respond. It updates in the database."""
        def setting(inner):
            def check(msg):
                return msg.channel == message.channel and not msg.author.bot or inner(msg)
            return check

        bots = []
        while message.created_at + datetime.timedelta(seconds=5) > datetime.datetime.utcnow():
            with contextlib.suppress(asyncio.TimeoutError):
                if m := await self.bot.wait_for("message", check=setting(func), timeout=1):
                    if not m.author.bot:
                        break
                    bots.append(m.author.id)
        if not bots:
            return
        # Possibility of duplication removal
        exist_query = "SELECT * FROM bot_prefix_list WHERE bot_id=ANY($1::BIGINT[])"
        existing = await self.bot.pool_pg.fetch(exist_query, bots)
        for x in existing:
            if prefix.startswith(x["prefix"]):
                bots.remove(x["bot_id"])
        if not bots:
            return
        query = "INSERT INTO bot_prefix_list VALUES($1, $2, $3) " \
                "ON CONFLICT (bot_id, prefix) DO " \
                "UPDATE SET usage=bot_prefix_list.usage+1"
        values = [(x, prefix, 1) for x in bots]

        await self.bot.pool_pg.executemany(query, values)
        for x, prefix, _ in values:
            prefixes = self.all_bot_prefixes.setdefault(x, {})
            prefixes[prefix] = prefixes.get(prefix, 0) + 1
        self.update_compile()

    @commands.Cog.listener("on_message")
    @wait_ready()
    @is_user()
    async def find_bot_prefix(self, message):
        """Responsible for checking if a message has a prefix for a bot or not by checking if it's a jishaku or help command."""
        def check_jsk(m):
            possible_text = ("Jishaku", "discord.py", "Python ", "Module ", "guild(s)", "user(s).")
            return all(text in m.content for text in possible_text)

        def check_help(m):
            def search(search_text):
                possible_text = ("command", "help", "category", "categories")
                return any(f"{t}" in search_text.lower() for t in possible_text)
            content = search(m.content)
            embeds = any(search(str(e.to_dict())) for e in m.embeds)
            return content or embeds

        for x in "jsk", "help":
            if match := re.match("(?P<prefix>^.{{1,30}}?(?={}$))".format(x), message.content):
                if x not in match["prefix"]:
                    if x == "help":
                        self.help_trigger.update({message.channel.id: message})
                    return await self.update_prefix_bot(message, locals()[f"check_{x}"], match["prefix"])

    @commands.Cog.listener("on_message")
    @wait_ready()
    @event_check(command_count_check)
    async def command_count(self, message):
        """
        Checks if the message contains a valid prefix, which will wait for the bot to respond to count that message
        as a command.
        """
        limit = min(len(message.content), 31)
        content_compiled = ctypes.create_string_buffer(message.content[:limit].encode("utf-8"))
        if not (result := search_prefix(self.compiled_pref, content_compiled)):
            return

        query = f"SELECT * FROM bot_prefix_list WHERE {' OR '.join(map('prefix=${}'.format, range(1, len(result) + 1)))}"
        bots = await self.bot.pool_pg.fetch(query, *result)
        match_bot = {bot["bot_id"] for bot in bots if message.guild.get_member(bot["bot_id"])}

        def check(msg):
            return msg.author.bot and msg.channel == message.channel and msg.author.id in match_bot

        bot_found = []
        while message.created_at + datetime.timedelta(seconds=5) > datetime.datetime.utcnow():
            with contextlib.suppress(asyncio.TimeoutError):
                if m := await self.bot.wait_for("message", check=check, timeout=1):
                    bot_found.append(m.author.id)
                if len(bot_found) == len(match_bot):
                    break
        if not bot_found:
            return
        command_query = "INSERT INTO bot_usage_count VALUES($1, $2) " \
                        "ON CONFLICT (bot_id) DO " \
                        "UPDATE SET count=bot_usage_count.count + 1"
        prefix_query = "INSERT INTO bot_prefix_list VALUES($1, $2, $3) " \
                       "ON CONFLICT (bot_id, prefix) DO " \
                       "UPDATE SET usage=bot_prefix_list.usage + 1"

        command_values = [(x, 1) for x in bot_found]
        prefix_values = []
        for prefix, bot in itertools.product(result, bots):
            if bot["prefix"] == prefix and bot["bot_id"] in bot_found:
                prefix_values.append((bot["bot_id"], bot["prefix"], 1))
        for x in ("command", "prefix"):
            await self.bot.pool_pg.executemany(locals()[x + "_query"], locals()[x + "_values"])

    @commands.Cog.listener("on_message")
    @wait_ready()
    @is_user()
    async def addbot_command_tracker(self, message):
        """Tracks ?addbot command. This is an exact copy of R. Danny code."""
        if message.channel.id not in (559455534965850142, 381963689470984203, 381963705686032394):
            return
        if result := await self.is_valid_addbot(message, check=True):
            confirm = False

            def terms_acceptance(msg):
                nonlocal confirm
                if msg.author.id != message.author.id:
                    return False
                if msg.channel.id != message.channel.id:
                    return False
                if msg.content in ('**I agree**', 'I agree'):
                    confirm = True
                    return True
                elif msg.content in ('**Abort**', 'Abort'):
                    return True
                return False

            try:
                await self.bot.wait_for("message", check=terms_acceptance, timeout=60)
            except asyncio.TimeoutError:
                return

            if not confirm:
                return
            await self.update_pending(result)

    async def check_author(self, bot_id, author_id, mode):
        """Checks if the author of a bot is the same as what is stored in the database."""
        if data := await self.bot.pool_pg.fetchrow(f"SELECT * FROM {mode} WHERE bot_id=$1", bot_id):
            old_author = data['author_id']
            return old_author == author_id

    async def is_valid_addbot(self, message, check=False):
        """Check if a message is a valid ?addbot command."""
        if result := re.match(self.re_addbot, message.content):
            reason = result["reason"]
            get_member = message.guild.get_member
            if not check:
                member = get_member(int(result["id"]))
                six_days = datetime.datetime.utcnow() - datetime.timedelta(days=6)
                if not member and message.created_at > six_days:
                    member = await try_call(self.bot.fetch_user, int(result["id"]), exception=discord.NotFound)
                    if all((reason, member and member.bot and str(member.id) not in self.bot.pending_bots)):
                        if str(member.id) not in self.bot.confirmed_bots:
                            await self.update_pending(
                                BotAdded(author=message.author,
                                         bot=member,
                                         reason=reason,
                                         requested_at=message.created_at,
                                         jump_url=message.jump_url))
                        return

            else:
                if member := get_member(int(result["id"])):
                    if int(result["id"]) not in self.bot.confirmed_bots and \
                            await self.check_author(member.id, message.author.id, "confirmed_bots"):
                        newAddBot = BotAdded(author=message.author,
                                             bot=member,
                                             reason=reason,
                                             requested_at=message.created_at,
                                             jump_url=message.jump_url,
                                             joined_at=member.joined_at)
                        await self.update_confirm(newAddBot)
                    return
                member = await try_call(self.bot.fetch_user, int(result["id"]), exception=discord.NotFound)
            if all((reason, member and member.bot)):
                join = None
                if isinstance(member, discord.Member):
                    join = member.joined_at
                    if join < message.created_at:
                        return
                return BotAdded(author=message.author,
                                bot=member,
                                reason=reason,
                                requested_at=message.created_at,
                                jump_url=message.jump_url,
                                joined_at=join)

    async def update_pending(self, result):
        """Insert a new addbot request which is yet to enter the discord.py server."""
        query = """INSERT INTO pending_bots VALUES($1, $2, $3, $4, $5) 
                   ON CONFLICT (bot_id) DO
                   UPDATE SET reason = $3, requested_at=$4, jump_url=$5"""
        value = (result.bot.id, result.author.id, result.reason, result.requested_at, result.jump_url)
        await self.bot.pool_pg.execute(query, *value)
        if result.bot.id not in self.bot.pending_bots:
            self.bot.pending_bots.add(result.bot.id)

    async def update_confirm(self, result):
        """Inserts a new confirmed bot with an author where the bot is actually in the discord.py server."""
        query = """INSERT INTO confirmed_bots VALUES($1, $2, $3, $4, $5, $6) 
                   ON CONFLICT (bot_id) DO
                   UPDATE SET reason = $3, requested_at=$4, jump_url=$5, joined_at=$6"""
        if not result.author:
            return self.bot.pending_bots.remove(result.bot.id)

        value = (result.bot.id, result.author.id, result.reason, result.requested_at, result.jump_url, result.joined_at)
        await self.bot.pool_pg.execute(query, *value)
        if result.bot.id in self.bot.pending_bots:
            self.bot.pending_bots.remove(result.bot.id)
        if result.bot.id not in self.bot.confirmed_bots:
            self.bot.confirmed_bots.add(result.bot.id)

    @commands.command(aliases=["owns", "userowns", "whatadds", "whatadded"],
                      brief="Shows what bot the user owns in discord.py.",
                      help="Shows the name of the bot that the user has added in discord.py. "
                           "This is useful for flexing for no reason."
                      )
    @is_discordpy()
    async def whatadd(self, ctx, author: IsBot(is_bot=False, user_check=False) = None):
        if not author:
            author = ctx.author
        if author.bot:
            return await ctx.maybe_reply("That's a bot lol")
        query = "SELECT * FROM {}_bots WHERE author_id=$1"
        total_list = [await self.bot.pool_pg.fetch(query.format(x), author.id) for x in ("pending", "confirmed")]
        total_list = itertools.chain.from_iterable(total_list)

        async def get_member(b_id):
            return ctx.guild.get_member(b_id) or await self.bot.fetch_user(b_id)
        list_bots = [BotAdded.from_json(await get_member(x["bot_id"]), **x) for x in total_list]
        embed = BaseEmbed.default(ctx, title=plural(f"{author}'s bot(s)", len(list_bots)))
        for dbot in list_bots:
            bot_id = dbot.bot.id
            value = ""
            if buse := await try_call(BotUsage.convert, ctx, str(bot_id)):
                value += f"**Usage:** `{buse.count}`\n"
            if bprefix := await try_call(BotPrefix.convert, ctx, str(bot_id)):
                value += f"**Prefix:** `{self.clean_prefix(ctx, bprefix.prefix)}`\n"

            value += f"**Created at:** `{default_date(dbot.bot.created_at)}`"
            embed.add_field(name=dbot, value=value, inline=False)
        embed.set_thumbnail(url=author.avatar_url)
        if not list_bots:
            embed.description = f"{author} doesnt own any bot here."
        await ctx.embed(embed=embed)

    @commands.command(aliases=["whoowns", "whosebot", "whoadds", "whoadded"],
                      brief="Shows who added the bot.",
                      help="Shows who added the bot, when they requested it and when the bot was added including the "
                           "jump url to the original request message in discord.py.")
    @is_discordpy()
    async def whoadd(self, ctx, bot: BotAdded):
        data = bot
        author = await try_call(commands.UserConverter().convert, ctx, str(data.author), exception=UserNotFound)
        embed = discord.Embed(title=str(data.bot))
        embed.set_thumbnail(url=data.bot.avatar_url)

        def or_none(condition, func):
            if condition:
                return func(condition)

        fields = (("Added by", f"{author.mention} (`{author.id}`)"),
                  ("Reason", data.reason),
                  ("Requested", or_none(data.requested_at, default_date)),
                  ("Joined", or_none(data.joined_at, default_date)),
                  ("Message Request", or_none(data.jump_url, "[jump]({})".format)))

        await ctx.embed(embed=embed, fields=fields)

    def clean_prefix(self, ctx, prefix):
        value = (ctx.guild, ctx.bot)[ctx.guild is None]
        prefix = pprefix(value, prefix)
        if prefix == "":
            prefix = "\u200b"
        return re.sub("`", "`\u200b", prefix)

    @commands.command(aliases=["wp", "whatprefixes"],
                      brief="Shows the bot prefix.",
                      help="Shows what the bot's prefix. This is sometimes inaccurate. Don't rely on it too much. "
                           "This also does not know it's aliases prefixes.")
    @commands.guild_only()
    async def whatprefix(self, ctx, member: BotPrefix):
        show_prefix = functools.partial(self.clean_prefix, ctx)
        prefix = show_prefix(member.prefix)
        alias = '`, `'.join(map(show_prefix, member.aliases))
        e = discord.Embed()
        e.add_field(name="Current", value=f"`{prefix}`")
        if member.aliases:
            e.add_field(name="Potential Aliases", value=f"`{alias}`")
        await ctx.embed(title=f"{member}'s Prefix", embed=e)

    @commands.command(aliases=["pu", "shares", "puse"],
                      brief="Shows the amount of bot that uses the same prefix.",
                      help="Shows the number of bot that shares a prefix between bots.")
    @commands.guild_only()
    async def prefixuse(self, ctx, prefix):
        instance_bot = await self.get_all_prefix(ctx.guild, prefix)
        prefix = self.clean_prefix(ctx, prefix)
        desk = plural(f"There (is/are) `{len(instance_bot)}` bot(s) that use `{prefix}` as prefix", len(instance_bot))
        await ctx.embed(description=desk)

    async def get_all_prefix(self, guild, prefix):
        """Quick function that gets the amount of bots that has the same prefix in a server."""
        data = await self.bot.pool_pg.fetch("SELECT * FROM bot_prefix WHERE prefix=$1", prefix)

        def mem(x):
            return guild.get_member(x)

        return [mem(x['bot_id']) for x in data if mem(x['bot_id'])]

    @commands.command(aliases=["pb", "prefixbots", "pbots"],
                      brief="Shows the name of bot(s) have a given prefix.",
                      help="Shows a list of bot(s) name that have a given prefix.")
    @commands.guild_only()
    async def prefixbot(self, ctx, prefix):
        instance_bot = await self.get_all_prefix(ctx.guild, prefix)
        list_bot = "\n".join(f"`{no + 1}. {x}`" for no, x in enumerate(instance_bot)) or "`Not a single bot have it.`"
        prefix = self.clean_prefix(ctx, prefix)
        desk = f"Bot(s) with `{prefix}` as prefix\n{list_bot}"
        await ctx.embed(description=plural(desk, len(list_bot)))

    @commands.command(aliases=["ap", "aprefix", "allprefixes"],
                      brief="Shows every bot's prefix in the server.",
                      help="Shows a list of every single bot's prefix in a server.",
                      cls=flg.SFlagCommand)
    @commands.guild_only()
    @flg.add_flag("--count", type=bool, default=False, action="store_true",
                  help="Create a rank of the highest prefix that is being use by bots. This flag accepts True or False, "
                       "defaults to False if not stated.")
    @flg.add_flag("--reverse", type=bool, default=False, action="store_true",
                  help="Reverses the list. This flag accepts True or False, default to False if not stated.")
    async def allprefix(self, ctx, **flags):
        bots = await self.bot.pool_pg.fetch("SELECT * FROM bot_prefix_list")
        attr = "count" if (count_mode := flags.pop("count", False)) else "prefix"
        reverse = flags.pop("reverse", False)

        def mem(x):
            return ctx.guild.get_member(x)

        temp = {}
        for bot in filter(lambda b: mem(b["bot_id"]), bots):
            prefixes = temp.setdefault(bot["bot_id"], {bot["prefix"]: bot["usage"]})
            prefixes.update({bot["prefix"]: bot["usage"]})
        data = [BotPrefix(mem(b), v) for b, v in temp.items()]

        if count_mode:
            PrefixCount = collections.namedtuple("PrefixCount", "prefix count")
            aliases = itertools.chain.from_iterable(map(lambda x: x.aliases, data))
            count_prefixes = collections.Counter([*map(lambda x: x.prefix, data), *aliases])
            data = [PrefixCount(*a) for a in count_prefixes.items()]

        data.sort(key=lambda x: getattr(x, attr), reverse=count_mode is not reverse)
        menu = MenuBase(source=AllPrefixes(data, count_mode))
        await menu.start(ctx)

    @commands.command(aliases=["bot_use", "bu", "botusage", "botuses"],
                      brief="Show's how many command calls for a bot.",
                      help="Show's how many command calls for a given bot. This works by counting how many times "
                           "a message is considered a command for that bot where that bot has responded in less than "
                           "2 seconds.")
    async def botuse(self, ctx, bot: BotUsage):
        await ctx.embed(title=f"{bot}'s Usage",
                        description=plural(f"`{bot.count}` command(s) has been called for **{bot}**.", bot.count))

    @commands.command(aliases=["bot_info", "bi", "botinfos"],
                      brief="Shows the bot information such as bot owner, prefixes, command usage.",
                      help="Shows the bot information such as bot owner, it's prefixes, the amount of command it has "
                           "been called, the reason on why it was added, the time it was requested and the time it "
                           "joined the server.")
    @is_discordpy()
    async def botinfo(self, ctx, bot: IsBot):
        # TODO: this is pretty terrible, optimise this
        titles = (("Bot Prefix", "{0.allprefixes}", BotPrefix),
                  ("Command Usage", "{0.count}", BotUsage),
                  (("Bot Invited by", "{0.author}"),
                   (("Reason", "reason"),
                    ("Requested at", 'requested_at')),
                   BotAdded))
        embed = BaseEmbed.default(ctx, title=str(bot))
        embed.set_thumbnail(url=bot.avatar_url)
        embed.add_field(name="ID", value=f"`{bot.id}`")
        for title, attrib, converter in reversed(titles):
            with contextlib.suppress(Exception):
                if obj := await converter.convert(ctx, str(bot.id)):
                    if isinstance(attrib, tuple):
                        for t, a in attrib:
                            if dat := getattr(obj, a):
                                dat = dat if not isinstance(dat, datetime.datetime) else default_date(dat)
                                embed.add_field(name=t, value=f"`{dat}`", inline=False)

                        title, attrib = title
                    embed.add_field(name=title, value=f"{attrib.format(obj)}", inline=False)

        embed.add_field(name="Created at", value=f"`{default_date(bot.created_at)}`")
        embed.add_field(name="Joined at", value=f"`{default_date(bot.joined_at)}`")
        await ctx.embed(embed=embed)

    @commands.command(aliases=["rba", "recentbot", "recentadd"],
                      brief="Shows a list of bots that has been added in a day.",
                      help="Shows a list of bots that has been added in a day along with the owner that requested it, "
                           "and how long ago it was added.",
                      cls=flg.SFlagCommand)
    @is_discordpy()
    @flg.add_flag("--reverse", type=bool, default=False, action="store_true",
                  help="Reverses the list. This flag accepts True or False, default to False if not stated.")
    async def recentbotadd(self, ctx, **flags):
        reverse = flags.pop("reverse", False)

        def predicate(m):
            return m.bot and m.joined_at > ctx.message.created_at - datetime.timedelta(days=1)
        members = {m.id: m for m in filter(predicate, ctx.guild.members)}
        if not members:
            member = max(filter(lambda x: x.bot, ctx.guild.members), key=lambda x: x.joined_at)
            time_add = humanize.precisedelta(member.joined_at, minimum_unit="minutes")
            return await ctx.embed(
                            title="Bots added today",
                            description="Looks like there are no bots added in the span of 24 hours.\n"
                                        f"The last time a bot was added was `{time_add}` for `{member}`"
            )
        db_data = await self.bot.pool_pg.fetch("SELECT * FROM confirmed_bots WHERE bot_id=ANY($1::BIGINT[])", list(members))
        member_data = [BotAdded.from_json(bot=members[data["bot_id"]], **data) for data in db_data]
        member_data.sort(key=lambda x: x.joined_at, reverse=not reverse)
        menu = MenuBase(source=bot_added_list(member_data))
        await menu.start(ctx)

    @commands.command(aliases=["rht", "recenthelptrip", "recenttrigger"],
                      brief="Shows the last message that triggers a help command in a channel.",
                      help="Shows the last message that triggers a help command in a channel that it was called from. "
                           "Useful for finding out who's the annoying person that uses common prefix help command.")
    async def recenthelptrigger(self, ctx):
        if message := self.help_trigger.get(ctx.channel.id):
            embed_dict = {
                "title": "Recent Help Trigger",
                "description": f"**Author:** `{message.author}`\n"
                               f"**Message ID:** `{message.id}`\n"
                               f"**Command:** `{message.content}`\n"
                               f"**Message Link:** [`jump`]({message.jump_url})",
            }
        else:
            embed_dict = {
                "title": "Recent Help Trigger",
                "description": "There is no help command triggered recently."
            }
        await ctx.embed(**embed_dict)

    @commands.command(aliases=["br", "brrrr", "botranks", "botpos", "botposition", "botpositions"],
                      help="Shows all bot's command usage in the server on a sorted list.",
                      cls=flg.SFlagCommand)
    @flg.add_flag("--reverse", type=bool, default=False, action="store_true",
                  help="Reverses the list. This flag accepts True or False, default to False if not stated.")
    async def botrank(self, ctx, bot: BotUsage = None, **flags):
        reverse = flags.pop("reverse", False)
        bots = {x.id: x for x in ctx.guild.members if x.bot}
        query = "SELECT * FROM bot_usage_count WHERE bot_id=ANY($1::BIGINT[])"
        record = await self.bot.pool_pg.fetch(query, list(bots))
        bot_data = [BotUsage(bots[r["bot_id"]], r["count"]) for r in record]
        bot_data.sort(key=lambda x: x.count, reverse=not reverse)
        if not bot:
            menu = MenuBase(source=all_bot_count(bot_data))
            await menu.start(ctx)
        else:
            key = "(\u200b|\u200b)"
            idx = [*map(int, bot_data)].index(bot.bot.id)
            scope_bot = bot_data[idx:min(idx + len(bot_data[idx:]), idx + 10)]
            contents = ["`{0}. {1} {2} {1.count}`".format(i + idx + 1, b, key) for i, b in enumerate(scope_bot)]
            await ctx.embed(title="Bot Command Rank", description="\n".join(realign(contents, key)))

    @commands.command(aliases=["pendingbots", "penbot", "peb"],
                      help="A bot that registered to ?addbot command of R. Danny but never joined the server.")
    @is_discordpy()
    async def pendingbot(self, ctx):
        bots = await self.bot.pool_pg.fetch("SELECT * FROM pending_bots")


def setup(bot):
    bot.add_cog(FindBot(bot))
