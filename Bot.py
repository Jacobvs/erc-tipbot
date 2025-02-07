import discord
from discord_webhook import DiscordWebhook
from discord.ext import commands
from discord.ext.commands import Bot, AutoShardedBot, when_mentioned_or, CheckFailure
from discord.utils import get

import os
import time, timeago
from datetime import datetime
from config import config
import click
import sys, traceback
import asyncio, aiohttp
import json
from decimal import Decimal

import re
import math, random

import store
from typing import List, Dict

# for randomString
import random, string

# Eth wallet py
import functools
from pywallet import wallet as ethwallet
import logging

# redis
import redis
redis_pool = None
redis_conn = None
redis_expired = 120

logging.basicConfig(level=logging.INFO)

TX_IN_PROCESS = []
TOKEN_NAME = config.moon.ticker.upper()
MOD_LIST = config.discord.mod_list.split(",")

EMOJI_ERROR = "\u274C"
EMOJI_OK_BOX = "\U0001F197"
EMOJI_RED_NO = "\u26D4"
EMOJI_OK_HAND = "\U0001F44C"
EMOJI_MONEYBAG = "\U0001F4B0"
EMOJI_QUESTEXCLAIM = "\u2049"
EMOJI_ARROW_RIGHTHOOK = "\u21AA"
EMOJI_ZIPPED_MOUTH = "\U0001F910"
EMOJI_MONEYFACE = "\U0001F911"
EMOJI_BELL_SLASH = "\U0001F515"
EMOJI_BELL = "\U0001F514"
EMOJI_HOURGLASS_NOT_DONE = "\u23F3"
EMOJI_PARTY = "\U0001F389"
EMOJI_SPEAK = "\U0001F4AC"
EMOJI_INFORMATION = "\u2139"
EMOJI_WARNING = "\u26A1"

NOTIFICATION_OFF_CMD = 'Type: `.notifytip off` to turn off this DM notification.'

bot_help_about = "About MoonTipBot."
bot_help_invite = "Invite link of bot to your server."
bot_help_balance = "Check your tipbot balance."
bot_help_deposit = "Get your wallet ticker's deposit address."
bot_help_register = "Register or change your deposit address for MoonTipBot."
bot_help_withdraw = "Withdraw coin from your MoonTipBot balance."

bot_help_admin_shutdown = "Restart bot."
bot_help_admin_maintenance = "Bot to be in maintenance mode ON / OFF"

intents = discord.Intents.default()
intents.members = True
intents.presences = True


def init():
    global redis_pool
    print("PID %d: initializing redis pool..." % os.getpid())
    redis_pool = redis.ConnectionPool(host='localhost', port=6379, decode_responses=True, db=8)


def openRedis():
    global redis_pool, redis_conn
    if redis_conn is None:
        try:
            redis_conn = redis.Redis(connection_pool=redis_pool)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)


async def logchanbot(content: str):
    filterword = config.moon.logfilterword.split(",")
    for each in filterword:
        content = content.replace(each, config.moon.filteredwith)
    try:
        webhook = DiscordWebhook(url=config.moon.webhook_url, content=f'```{discord.utils.escape_markdown(content)}```')
        webhook.execute()
    except Exception as e:
        traceback.print_exc(file=sys.stdout)


# Steal from https://github.com/cree-py/RemixBot/blob/master/bot.py#L49
async def get_prefix(bot, message):
    """Gets the prefix for the guild"""
    pre_cmd = config.discord.prefixCmd
    if isinstance(message.channel, discord.DMChannel):
        extras = [pre_cmd, 'm!', 'moon!', '?', '.', '+', '!', '-']
        return when_mentioned_or(*extras)(bot, message)

    serverinfo = await store.sql_info_by_server(str(message.guild.id))
    if serverinfo is None:
        # Let's add some info if guild return None
        add_server_info = await store.sql_addinfo_by_server(str(message.guild.id), message.guild.name,
                                                            config.discord.prefixCmd)
        serverinfo = await store.sql_info_by_server(str(message.guild.id))
    if serverinfo and 'prefix' in serverinfo:
        pre_cmd = serverinfo['prefix']
    else:
        pre_cmd =  config.discord.prefixCmd
    extras = [pre_cmd, 'm!', 'moon!']
    return when_mentioned_or(*extras)(bot, message)


bot = AutoShardedBot(command_prefix=get_prefix, owner_id = config.discord.ownerID, case_insensitive=True, intents=intents)

# Create ETH
def create_eth_wallet():
    seed = ethwallet.generate_mnemonic()
    w = ethwallet.create_wallet(network="ETH", seed=seed, children=1)
    return w

async def create_address_eth():
    wallet_eth = functools.partial(create_eth_wallet)
    create_wallet = await bot.loop.run_in_executor(None, wallet_eth)
    return create_wallet


@bot.event
async def on_shard_ready(shard_id):
    print(f'Shard {shard_id} connected')

@bot.event
async def on_ready():
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)
    print('------')
    game = discord.Game(name="m.")
    await bot.change_presence(status=discord.Status.online, activity=game)


@bot.event
async def on_guild_join(guild):
    botLogChan = bot.get_channel(id=int(config.moon.logchan))
    add_server_info = await store.sql_addinfo_by_server(str(guild.id), guild.name,
                                                        config.discord.prefixCmd)
    await botLogChan.send(f'Bot joins a new guild {guild.name} / {guild.id} / Users: {len(guild.members)}. Total guilds: {len(bot.guilds)}.')
    return


@bot.event
async def on_guild_remove(guild):
    botLogChan = bot.get_channel(id=int(config.moon.logchan))
    add_server_info = await store.sql_updateinfo_by_server(str(guild.id), "status", "REMOVED")
    await botLogChan.send(f'Bot was removed from guild {guild.name} / {guild.id}. Total guilds: {len(bot.guilds)}')
    return


@bot.event
async def on_message(message):
    # should ignore webhook message
    if isinstance(message.channel, discord.DMChannel) == False and message.webhook_id:
        return

    if isinstance(message.channel, discord.DMChannel) == False and message.author.bot == False and len(message.content) > 0 and message.author != bot.user:
        await add_msg_redis(json.dumps([str(message.guild.id), message.guild.name, str(message.channel.id), message.channel.name, 
                                        str(message.author.id), message.author.name, str(message.id), message.content, int(time.time())]), False)
    # Do not remove this, otherwise, command not working.
    ctx = await bot.get_context(message)
    await bot.invoke(ctx)


@bot.event
async def on_reaction_add(reaction, user):
    # If bot react, ignore.
    if user.id == bot.user.id:
        return
    # If other people beside bot react.
    else:
        # If reaction is OK box and message author is bot itself
        if reaction.emoji == EMOJI_OK_BOX and reaction.message.author.id == bot.user.id:
            # do not delete some embed message
            if reaction.message.embeds and len(reaction.message.embeds) > 0:
                try:
                    title = reaction.message.embeds[0].title
                    if title and 'FREE TIP' in str(title.upper()):
                        return
                except Exception as e:
                    pass
            try:
                await reaction.message.delete()
            except Exception as e:
                pass


@bot.event
async def on_raw_reaction_add(payload):
    if payload.guild_id is None:
        return  # Reaction is on a private message
    """Handle a reaction add."""
    try:
        emoji_partial = str(payload.emoji)
        message_id = payload.message_id
        channel_id = payload.channel_id
        user_id = payload.user_id
        guild = bot.get_guild(payload.guild_id)
        channel = bot.get_channel(id=channel_id)
        if not channel:
            return
        if isinstance(channel, discord.DMChannel):
            return
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
        return
    message = None
    author = None
    if message_id:
        try:
            message = await channel.fetch_message(message_id)
            author = message.author
        except (discord.errors.NotFound, discord.errors.Forbidden) as e:
            # No message found
            return
        member = bot.get_user(id=user_id)
        if emoji_partial in [EMOJI_OK_BOX] and message.author.id == bot.user.id \
            and author != member and message:
            # do not delete some embed message
            if message.embeds and len(message.embeds) > 0:
                try:
                    title = message.embeds[0].title
                    if title and 'FREE TIP' in str(title.upper()):
                        return
                except Exception as e:
                    pass
            try:
                await reaction.message.delete()
            except Exception as e:
                pass


@bot.command(help='Check pending things', hidden = True)
async def pending(ctx):
    if str(ctx.author.id) not in [str(each) for each in MOD_LIST]:
        return

    ts = datetime.utcnow()
    embed = discord.Embed(title='Pending Actions', timestamp=ts)
    embed.add_field(name="Pending Tx", value=str(len(TX_IN_PROCESS)), inline=True)
    if len(TX_IN_PROCESS) > 0:
        string_ints = [str(num) for num in TX_IN_PROCESS]
        list_pending = '{' + ', '.join(string_ints) + '}'
        embed.add_field(name="List Pending By", value=list_pending, inline=True)
    embed.set_footer(text=f"Pending requested by {ctx.message.author.name}#{ctx.message.author.discriminator}")
    try:
        msg = await ctx.author.send(embed=embed)
        await msg.add_reaction(EMOJI_OK_BOX)
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
    return


@bot.command(help='Clear TX_IN_PROCESS', hidden = True)
async def cleartx(ctx):
    global TX_IN_PROCESS
    if str(ctx.author.id) not in [str(each) for each in MOD_LIST]:
        return

    if len(TX_IN_PROCESS) == 0:
        await ctx.author.send(f'{ctx.author.mention} Nothing in tx pending to clear.')
    else:
        try:
            string_ints = [str(num) for num in TX_IN_PROCESS]
            list_pending = '{' + ', '.join(string_ints) + '}'
            await ctx.message.add_reaction(EMOJI_WARNING)
            await logchanbot(f'{ctx.author.mention} Clearing {str(len(TX_IN_PROCESS))} {list_pending} in pending...')
            await ctx.author.send(f'Clearing {str(len(TX_IN_PROCESS))} {list_pending} in pending...')
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
        TX_IN_PROCESS = [] 
    return


@bot.command(pass_context = True, name='fetchtalk', hidden = True)
async def fetchtalk(ctx, channelid: int, countmsg: int=5000):
    if ctx.author.id != config.discord.ownerID:
        return

    talk_channel = bot.get_channel(id=channelid)
    count = 0
    temp_msg_list = []
    if talk_channel:
        messages = await talk_channel.history(limit=countmsg).flatten()
        try:
            if messages and len(messages) > 0:
                for each in messages:
                    # ignore bot messages
                    if each.author != bot.user:
                        count += 1
                        # add to DB
                        timestamp = datetime.timestamp(each.created_at)
                        temp_msg_list.append((str(talk_channel.guild.id), talk_channel.guild.name, str(channelid), talk_channel.name, str(each.author.id), each.author.name, str(each.id), each.content, timestamp))
                        print((str(talk_channel.guild.id), talk_channel.guild.name, str(channelid), talk_channel.name, str(each.author.id), each.author.name, str(each.id), each.content, timestamp))
            if len(temp_msg_list) > 0:             
                try:
                    num_add = await store.sql_add_messages(temp_msg_list)
                except Exception as e:
                    traceback.print_exc(file=sys.stdout)
                    await logchanbot(traceback.format_exc())
                await ctx.send(f'{ctx.author.mention} Found {len(temp_msg_list)} message(s) and added {str(num_add)}.')
                return
        except Exception as e:
            print(traceback.format_exc())
    else:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{ctx.author.mention} I can not find channel **{channelid}**.')
        return


@bot.command(hidden = True, pass_context=True, name='prefix')
async def prefix(ctx):
    prefix = await get_guild_prefix(ctx)
    try:
        msg = await ctx.send(f'{EMOJI_INFORMATION} {ctx.author.mention}, the prefix here is **{prefix}**')
        await msg.add_reaction(EMOJI_OK_BOX)
    except (discord.errors.NotFound, discord.errors.Forbidden) as e:
        await msg.add_reaction(EMOJI_ERROR)
        await logchanbot(traceback.format_exc())
    return


@bot.command(pass_context=True, name='about', help=bot_help_about)
async def about(ctx):
    invite_link = "https://discordapp.com/oauth2/authorize?client_id="+str(bot.user.id)+"&scope=bot"
    botdetails = discord.Embed(title='About Me', description='Basic ERC Tipping Bot', timestamp=datetime.utcnow(), colour=7047495)
    botdetails.add_field(name='Invite Me:', value=f'[Invite TipBot]({invite_link})', inline=True)
    botdetails.add_field(name='Servers I am in:', value=len(bot.guilds), inline=True)
    botdetails.set_footer(text='Made in Python3.8 with discord.py library!', icon_url='http://findicons.com/files/icons/2804/plex/512/python.png')
    botdetails.set_author(name=bot.user.name, icon_url=bot.user.avatar_url)
    try:
        await ctx.send(embed=botdetails)
    except Exception as e:
        await ctx.send(embed=botdetails)
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())


@bot.command(pass_context=True, name='invite', aliases=['inviteme'], help=bot_help_invite)
async def invite(ctx):
    invite_link = "https://discordapp.com/oauth2/authorize?client_id="+str(bot.user.id)+"&scope=bot"
    await ctx.send('**[INVITE LINK]**\n'
                f'{invite_link}')


@bot.command(pass_context=True, help="Toggle notify tip notification from bot ON|OFF")
async def notifytip(ctx, onoff: str):
    if onoff.upper() not in ["ON", "OFF"]:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{ctx.author.mention} You need to use only `ON` or `OFF`.')
        return

    onoff = onoff.upper()
    notifyList = await store.sql_get_tipnotify()
    if onoff == "ON":
        if str(ctx.message.author.id) in notifyList:
            await store.sql_toggle_tipnotify(str(ctx.message.author.id), "ON")
            await ctx.send(f'{ctx.author.mention} OK, you will get all notification when tip.')
            await ctx.message.add_reaction(EMOJI_BELL)
            return
        else:
            await ctx.send(f'{ctx.author.mention} You already have notification ON by default.')
            await ctx.message.add_reaction(EMOJI_BELL)
            return
    elif onoff == "OFF":
        if str(ctx.message.author.id) in notifyList:
            await ctx.send(f'{ctx.author.mention} You already have notification OFF.')
            await ctx.message.add_reaction(EMOJI_BELL_SLASH)
            return
        else:
            await store.sql_toggle_tipnotify(str(ctx.message.author.id), "OFF")
            await ctx.send(f'{ctx.author.mention} OK, you will not get any notification when anyone tips.')
            await ctx.message.add_reaction(EMOJI_BELL_SLASH)
            return


@bot.command(pass_context=True, aliases=['randomtip'], help='Tip to random user in the guild')
async def randtip(ctx, amount: str, *, rand_option: str=None):
    # Check if tx in progress
    if ctx.message.author.id in TX_IN_PROCESS:
        await ctx.message.add_reaction(EMOJI_HOURGLASS_NOT_DONE)
        msg = await ctx.send(f'{EMOJI_ERROR} {ctx.author.mention} You have another tx in progress.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    amount = amount.replace(",", "")

    try:
        amount = float(amount)
    except ValueError:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid amount.')
        return

    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.send(f'{EMOJI_RED_NO} This command can not be in private.')
        return

    # Get a random user in the guild, except bots. At least 3 members for random.
    has_last = False
    message_talker = None
    listMembers = None
    minimum_users = 2
    try:
        # Check random option
        if rand_option is None or rand_option.upper().startswith("ALL"):
            listMembers = [member for member in ctx.guild.members if member.bot == False]
        elif rand_option and rand_option.upper().startswith("ONLINE"):
            listMembers = [member for member in ctx.guild.members if member.bot == False and member.status != discord.Status.offline]
        elif rand_option and rand_option.upper().strip().startswith("LAST "):
            argument = rand_option.strip().split(" ")            
            if len(argument) == 2:
                # try if the param is 1111u
                num_user = argument[1].lower()
                if 'u' in num_user or 'user' in num_user or 'users' in num_user or 'person' in num_user or 'people' in num_user:
                    num_user = num_user.replace("people", "")
                    num_user = num_user.replace("person", "")
                    num_user = num_user.replace("users", "")
                    num_user = num_user.replace("user", "")
                    num_user = num_user.replace("u", "")
                    try:
                        num_user = int(num_user)
                        if num_user < minimum_users:
                            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Number of random users cannot below **{minimum_users}**.')
                            return
                        elif num_user >= minimum_users:
                            message_talker = await store.sql_get_messages(str(ctx.message.guild.id), str(ctx.message.channel.id), 0, num_user + 1)
                            if ctx.message.author.id in message_talker:
                                message_talker.remove(ctx.message.author.id)
                            else:
                                # remove the last one
                                message_talker.pop()
                            if len(message_talker) < minimum_users:
                                await ctx.message.add_reaction(EMOJI_ERROR)
                                await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} There is not sufficient user to count for random tip.')
                                return
                            elif len(message_talker) < num_user:
                                try:
                                    await ctx.message.add_reaction(EMOJI_INFORMATION)
                                    await ctx.send(f'{EMOJI_INFORMATION} {ctx.author.mention} I could not find sufficient talkers up to **{num_user}**. I found only **{len(message_talker)}**'
                                                   f' and will random to one of those **{len(message_talker)}** users.')
                                except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                                    # No need to tip if failed to message
                                    await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                                    # Let it still go through
                                    #return
                        has_last = True
                    except ValueError:
                        await ctx.message.add_reaction(EMOJI_ERROR)
                        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid param after **LAST** for random tip. Support only *LAST* **X**u right now.')
                        return
                else:
                    await ctx.message.add_reaction(EMOJI_ERROR)
                    await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid param after **LAST** for random tip. Support only *LAST* **X**u right now.')
                    return
            else:
                await ctx.message.add_reaction(EMOJI_ERROR)
                await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid param after **LAST** for random tip. Support only *LAST* **X**u right now.')
                return
        if has_last == False and listMembers and len(listMembers) >= minimum_users:
            rand_user = random.choice(listMembers)
            max_loop = 0
            while True:
                if rand_user != ctx.message.author and rand_user.bot == False:
                    break
                else:
                    rand_user = random.choice(listMembers)
                max_loop += 1
                if max_loop >= 5:
                    await ctx.message.add_reaction(EMOJI_ERROR)
                    await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} {TOKEN_NAME} Please try again, maybe guild doesnot have so many users.')
                    return
                    break
        elif has_last == True and message_talker and len(message_talker) >= minimum_users:
            rand_user_id = random.choice(message_talker)
            max_loop = 0
            while True:
                rand_user = bot.get_user(id=rand_user_id)
                if rand_user and rand_user != ctx.message.author and rand_user.bot == False and rand_user in ctx.guild.members:
                    break
                else:
                    rand_user_id = random.choice(message_talker)
                    rand_user = bot.get_user(id=rand_user_id)
                max_loop += 1
                if max_loop >= 10:
                    await ctx.message.add_reaction(EMOJI_ERROR)
                    await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} {TOKEN_NAME} Please try again, maybe guild doesnot have so many users.')
                    return
                    break
        else:
            await ctx.message.add_reaction(EMOJI_ERROR)
            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} {TOKEN_NAME} not enough member for random tip.')
            return
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
        return

    notifyList = await store.sql_get_tipnotify()
    token_info = await store.get_token_info(TOKEN_NAME)
    MinTx = float(token_info['real_min_tip'])
    MaxTX = float(token_info['real_max_tip'])


    user_from = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME)
    if user_from is None:
        w = await create_address_eth()
        user_from = await store.sql_register_user(str(ctx.message.author.id), TOKEN_NAME, w, 'DISCORD')
    userdata_balance = await store.sql_user_balance(str(ctx.message.author.id), TOKEN_NAME)
    actual_balance = float(user_from['real_actual_balance']) + float(userdata_balance['Adjust'])

    if amount > MaxTX:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be bigger than '
                       f'{num_format_coin(MaxTX)} '
                       f'{TOKEN_NAME}.')
        return
    elif amount < MinTx:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be smaller than '
                       f'{num_format_coin(MinTx)} '
                       f'{TOKEN_NAME}.')
        return
    elif amount > actual_balance:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Insufficient balance to do a free tip of '
                       f'{num_format_coin(amount)} '
                       f'{TOKEN_NAME}.')
        return

    # add queue also randtip
    if ctx.message.author.id not in TX_IN_PROCESS:
        TX_IN_PROCESS.append(ctx.message.author.id)
    else:
        await ctx.message.add_reaction(EMOJI_HOURGLASS_NOT_DONE)
        msg = await ctx.send(f'{EMOJI_ERROR} {ctx.author.mention} You have another tx in progress.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    print('random get user: {}/{}'.format(rand_user.name, rand_user.id))

    tip = None
    user_to = await store.sql_get_userwallet(str(rand_user.id), TOKEN_NAME)
    if user_to is None:
        w = await create_address_eth()
        userregister = await store.sql_register_user(str(rand_user.id), TOKEN_NAME, w, 'DISCORD')
        user_to = await store.sql_get_userwallet(str(rand_user.id), TOKEN_NAME)

    try:
        tip = await store.sql_mv_erc_single(str(ctx.message.author.id), str(rand_user.id), amount, TOKEN_NAME, "RANDTIP", token_info['contract'])
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())

    # remove queue from randtip
    if ctx.message.author.id in TX_IN_PROCESS:
        TX_IN_PROCESS.remove(ctx.message.author.id)

    if tip:
        randtip_public_respond = False
        # tipper shall always get DM. Ignore notifyList
        try:
            await ctx.message.author.send(
                f'{EMOJI_ARROW_RIGHTHOOK} {rand_user.name}#{rand_user.discriminator} got your random tip of {num_format_coin(amount)} '
                f'{TOKEN_NAME} in server `{ctx.guild.name}`')
        except (discord.Forbidden, discord.errors.Forbidden) as e:
            await store.sql_toggle_tipnotify(str(ctx.message.author.id), "OFF")
        if str(rand_user.id) not in notifyList:
            try:
                await rand_user.send(
                    f'{EMOJI_MONEYFACE} You got a random tip of {num_format_coin(amount)} '
                    f'{TOKEN_NAME} from {ctx.message.author.name}#{ctx.message.author.discriminator} in server `{ctx.guild.name}`\n'
                    f'{NOTIFICATION_OFF_CMD}')
            except (discord.Forbidden, discord.errors.Forbidden) as e:
                await store.sql_toggle_tipnotify(str(user.id), "OFF")
        try:
            # try message in public also
            msg = await ctx.send(
                            f'{rand_user.name}#{rand_user.discriminator} got a random tip of {num_format_coin(amount)} '
                            f'{TOKEN_NAME} from {ctx.message.author.name}#{ctx.message.author.discriminator}')
            await msg.add_reaction(EMOJI_OK_BOX)
            randtip_public_respond = True
        except (discord.Forbidden, discord.errors.Forbidden) as e:
            pass
        serverinfo = await store.sql_info_by_server(str(ctx.guild.id))
        if randtip_public_respond == False and serverinfo and 'botchan' in serverinfo and serverinfo['botchan']:
            # It has bot channel, let it post in bot channel
            try:
                bot_channel = bot.get_channel(id=int(serverinfo['botchan']))
                msg = await bot_channel.send(
                            f'{rand_user.name}#{rand_user.discriminator} got a random tip of {num_format_coin(amount)} '
                            f'{TOKEN_NAME} from {ctx.message.author.name}#{ctx.message.author.discriminator} in {ctx.channel.mention}')
                await msg.add_reaction(EMOJI_OK_BOX)
            except Exception as e:
                traceback.print_exc(file=sys.stdout)
                await logchanbot(traceback.format_exc())
        await ctx.message.add_reaction(EMOJI_OK_BOX)
        return


@bot.command(pass_context=True, help="Spread free tip by user reacting with emoji")
async def freetip(ctx, amount: str, duration: str, *, comment: str=None):
    global TX_IN_PROCESS
    # Check if tx in progress
    if ctx.message.author.id in TX_IN_PROCESS:
        await ctx.message.add_reaction(EMOJI_HOURGLASS_NOT_DONE)
        msg = await ctx.send(f'{EMOJI_ERROR} {ctx.author.mention} You have another tx in progress.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    def hms_to_seconds(time_string):
        duration_in_second = 0
        try:
            time_string = time_string.replace("hours", "h")
            time_string = time_string.replace("hour", "h")
            time_string = time_string.replace("hrs", "h")
            time_string = time_string.replace("hr", "h")

            time_string = time_string.replace("minutes", "mn")
            time_string = time_string.replace("mns", "mn")
            time_string = time_string.replace("mins", "mn")
            time_string = time_string.replace("min", "mn")
            time_string = time_string.replace("m", "mn")
            mult = {'h': 60*60, 'mn': 60}
            duration_in_second = sum(int(num) * mult.get(val, 1) for num, val in re.findall('(\d+)(\w+)', time_string))
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
        return duration_in_second


    amount = amount.replace(",", "")
    try:
        amount = float(amount)
    except ValueError:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid amount.')
        return

    duration_s = 0
    try:
        duration_s = hms_to_seconds(duration)
    except Exception as e:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid duration.')
        return

    print('get duration: {}'.format(duration_s))
    if duration_s == 0:
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid time given. Please use time format: XXs')
        return
    elif duration_s < config.freetip.duration_min or duration_s > config.freetip.duration_max:
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid duration. Please use between {str(config.freetip.duration_min)}s to {str(config.freetip.duration_max)}s.')
        return

    try:
        amount = float(amount)
    except ValueError:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid amount.')
        return

    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.send(f'{EMOJI_RED_NO} This command can not be in private.')
        return

    notifyList = await store.sql_get_tipnotify()
    token_info = await store.get_token_info(TOKEN_NAME)
    MinTx = float(token_info['real_min_tip'])
    MaxTX = float(token_info['real_max_tip'])

    user_from = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME)
    if user_from is None:
        w = await create_address_eth()
        user_from = await store.sql_register_user(str(ctx.message.author.id), TOKEN_NAME, w, 'DISCORD')
    userdata_balance = await store.sql_user_balance(str(ctx.message.author.id), TOKEN_NAME)
    actual_balance = float(user_from['real_actual_balance']) + float(userdata_balance['Adjust'])

    if amount > MaxTX:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be bigger than '
                       f'{num_format_coin(MaxTX)} '
                       f'{TOKEN_NAME}.')
        return
    elif amount < MinTx:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be smaller than '
                       f'{num_format_coin(MinTx)} '
                       f'{TOKEN_NAME}.')
        return
    elif amount > actual_balance:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Insufficient balance to do a free tip of '
                       f'{num_format_coin(amount)} '
                       f'{TOKEN_NAME}.')
        return

    attend_list = []
    ts = datetime.utcnow()
    embed = discord.Embed(title=f"Free Tip appears {num_format_coin(amount)}{TOKEN_NAME}", description=f"React {EMOJI_PARTY} to collect", timestamp=ts, color=0x00ff00)
    add_index = 0
    try:
        if comment and len(comment) > 0:
            add_index = 1
            embed.add_field(name="Comment", value=comment, inline=True)
        embed.add_field(name="Individual Tip Amount", value=f"{num_format_coin(amount)}{TOKEN_NAME}", inline=True)
        embed.add_field(name="Num. Attendees", value="**0** members", inline=True)
        embed.set_footer(text=f"Free tip by {ctx.message.author.name}#{ctx.message.author.discriminator}, Time Left: {seconds_str(duration_s)}")
        msg:discord.Message = await ctx.send(embed=embed)
        await msg.add_reaction(EMOJI_PARTY)
    except (discord.errors.NotFound, discord.errors.Forbidden) as e:
        await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
        return
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
            
    if ctx.author.id not in TX_IN_PROCESS:
        TX_IN_PROCESS.append(ctx.author.id)

    prev = []
    start_time = time.time()
    time_left = duration_s
    while time_left > 0:
        # Retrieve new reactions
        try:
            _msg: discord.Message = await ctx.fetch_message(msg.id)

            for r in _msg.reactions:
                # Find reaction we're looking for
                if str(r.emoji) == EMOJI_PARTY:
                    # Get list of Users that reacted & filter bots out
                    attend_list = [i for i in await r.users().flatten() if not i.bot and i != ctx.message.author]

                    # Check if there's been a change, otherwise delay & recheck
                    if set(attend_list) == set(prev) or len(attend_list) == 0:
                        await asyncio.sleep(0.25)
                        break

                    attend_list_names = " | ".join([str(u.name) + "#" + str(u.discriminator) for u in attend_list])
                    if len(attend_list_names) >= 1000:
                        attend_list_names = attend_list_names[:1000]
                    try:
                        embed.set_field_at(index=add_index, name='Attendees', value=attend_list_names, inline=False)
                        embed.set_field_at(index=1+add_index, name='Each Member Receives:', value=f"{num_format_coin(round(amount / len(attend_list), 4))}{TOKEN_NAME}",
                                           inline=True)
                        embed.set_field_at(index=2+add_index, name="Num. Attendees", value=f"**{len(attend_list)}** members", inline=True)
                    except Exception as e:
                        traceback.print_exc(file=sys.stdout)
                    embed.set_footer(text=f"Free tip by {ctx.message.author.name}#{ctx.message.author.discriminator}, Time Left: {seconds_str(int(time_left))}")
                    await _msg.edit(embed=embed)
                    prev = attend_list

            time_left = duration_s - (time.time() - start_time)

        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())

    try:
        await msg.clear_reaction(EMOJI_PARTY)
    except discord.Forbidden or discord.HTTPException:
        pass

    if len(attend_list) == 0:
        embed = discord.Embed(title=f"Free Tip appears {num_format_coin(amount)}{TOKEN_NAME}", description=f"Already expired", timestamp=ts, color=0x00ff00)
        if comment and len(comment) > 0:
            embed.add_field(name="Comment", value=comment, inline=False)
        embed.set_footer(text=f"Free tip by {ctx.message.author.name}#{ctx.message.author.discriminator}, and no one collected!")
        try:
            await msg.edit(embed=embed)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
        if ctx.author.id in TX_IN_PROCESS:
            TX_IN_PROCESS.remove(ctx.author.id)
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    attend_list_id = [u.id for u in attend_list if not u.bot and u != ctx.message.author]

    # re-check balance
    userdata_balance = await store.sql_user_balance(str(ctx.message.author.id), TOKEN_NAME)
    actual_balance = float(user_from['real_actual_balance']) + float(userdata_balance['Adjust'])

    if amount > actual_balance:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Insufficient balance to do a free tip of '
                       f'{num_format_coin(amount)} '
                       f'{TOKEN_NAME}.')
        if ctx.author.id in TX_IN_PROCESS:
            TX_IN_PROCESS.remove(ctx.author.id)
        return
    # end of re-check balance

    # Multiple tip here
    notifyList = await store.sql_get_tipnotify()
    amountDiv = round(amount / len(attend_list_id), 4)
    tips = None

    try:
        tips = await store.sql_mv_erc_multiple(str(ctx.message.author.id), attend_list_id, amountDiv, TOKEN_NAME, "TIPALL", token_info['contract'])
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
    if ctx.author.id in TX_IN_PROCESS:
        TX_IN_PROCESS.remove(ctx.author.id)

    if tips:
        tipAmount = num_format_coin(amount)
        ActualSpend_str = num_format_coin(amountDiv * len(attend_list_id))
        amountDiv_str = num_format_coin(amountDiv)
        numMsg = 0
        for each_id in attend_list_id:
            member = bot.get_user(id=each_id)
            # TODO: set limit here 100
            dm_user = bool(random.getrandbits(1)) if len(attend_list_id) > 100 else True
            if ctx.message.author.id != member.id and member.id != bot.user.id and str(member.id) not in notifyList:
                try:
                    if dm_user:
                        try:
                            await member.send(f'{EMOJI_MONEYFACE} You had collected a free tip of {amountDiv_str} '
                                              f'{TOKEN_NAME} from {ctx.message.author.name}#{ctx.message.author.discriminator} in server `{ctx.guild.name}`\n'
                                              f'{NOTIFICATION_OFF_CMD}')
                            numMsg += 1
                        except (discord.Forbidden, discord.errors.Forbidden) as e:
                            await store.sql_toggle_tipnotify(str(member.id), "OFF")
                except Exception as e:
                    traceback.print_exc(file=sys.stdout)
                    await logchanbot(traceback.format_exc())
        # free tip shall always get DM. Ignore notifyList
        try:
            await ctx.message.author.send(
                    f'{EMOJI_ARROW_RIGHTHOOK} Free tip of {tipAmount} '
                    f'{TOKEN_NAME} '
                    f'was sent spread to ({len(attend_list_id)}) members in server `{ctx.guild.name}`.\n'
                    f'Each member got: `{amountDiv_str}{TOKEN_NAME}`\n'
                    f'Actual spending: `{ActualSpend_str}{TOKEN_NAME}`')
        except (discord.Forbidden, discord.errors.Forbidden) as e:
            await store.sql_toggle_tipnotify(str(ctx.message.author.id), "OFF")
        # Edit embed
        try:
            embed = discord.Embed(title=f"Free Tip appears {num_format_coin(amount)}{TOKEN_NAME}", description=f"React {EMOJI_PARTY} to collect", timestamp=ts, color=0x00ff00)
            if comment and len(comment) > 0:
                embed.add_field(name="Comment", value=comment, inline=False)
            if len(attend_list_names) >= 1000: attend_list_names = attend_list_names[:1000]
            try:
                if len(attend_list) > 0:
                    embed.add_field(name='Attendees', value=attend_list_names, inline=False)
                    embed.add_field(name='Individual Tip amount', value=f"{num_format_coin(round(amount / len(attend_list), 4))}{TOKEN_NAME}", inline=True)
                    embed.add_field(name="Num. Attendees", value=f"**{len(attend_list)}** members", inline=True)
            except Exception as e:
                traceback.print_exc(file=sys.stdout)
            embed.set_footer(text=f"Completed! Collected by {len(attend_list_id)} member(s)")
            await msg.edit(embed=embed)
            await msg.add_reaction(EMOJI_OK_BOX)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
        await ctx.message.add_reaction(EMOJI_OK_HAND)
    else:
        await ctx.message.add_reaction(EMOJI_ERROR)
    return


@bot.command(pass_context=True, name='gfreetip', aliases=['mfreetip', 'guildfreetip'], help="Spread guild free tip by reacting with emoji")
@commands.has_permissions(manage_channels=True)
async def gfreetip(ctx, amount: str, duration: str, *, comment: str=None):
    global TX_IN_PROCESS

    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.send(f'{EMOJI_RED_NO} This command can not be in private.')
        return

    # Check if tx in progress
    if ctx.guild.id in TX_IN_PROCESS:
        await ctx.message.add_reaction(EMOJI_HOURGLASS_NOT_DONE)
        msg = await ctx.send(f'{EMOJI_ERROR} {ctx.author.mention} This guild has another tx in progress.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    def hms_to_seconds(time_string):
        duration_in_second = 0
        try:
            time_string = time_string.replace("hours", "h")
            time_string = time_string.replace("hour", "h")
            time_string = time_string.replace("hrs", "h")
            time_string = time_string.replace("hr", "h")

            time_string = time_string.replace("minutes", "mn")
            time_string = time_string.replace("mns", "mn")
            time_string = time_string.replace("mins", "mn")
            time_string = time_string.replace("min", "mn")
            time_string = time_string.replace("m", "mn")
            mult = {'h': 60*60, 'mn': 60}
            duration_in_second = sum(int(num) * mult.get(val, 1) for num, val in re.findall('(\d+)(\w+)', time_string))
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
        return duration_in_second


    amount = amount.replace(",", "")
    try:
        amount = float(amount)
    except ValueError:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid amount.')
        return

    duration_s = 0
    try:
        duration_s = hms_to_seconds(duration)
    except Exception as e:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid duration.')
        return

    print('get duration: {}'.format(duration_s))
    if duration_s == 0:
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid time given. Please use time format: XXs')
        return
    elif duration_s < config.freetip.duration_min or duration_s > config.freetip.duration_max:
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid duration. Please use between {str(config.freetip.duration_min)}s to {str(config.freetip.duration_max)}s.')
        return

    try:
        amount = float(amount)
    except ValueError:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid amount.')
        return

    notifyList = await store.sql_get_tipnotify()
    token_info = await store.get_token_info(TOKEN_NAME)
    MinTx = float(token_info['real_min_tip'])
    MaxTX = float(token_info['real_max_tip'])

    user_from = await store.sql_get_userwallet(str(ctx.guild.id), TOKEN_NAME)
    if user_from is None:
        w = await create_address_eth()
        user_from = await store.sql_register_user(str(ctx.guild.id), TOKEN_NAME, w, 'DISCORD')
    userdata_balance = await store.sql_user_balance(str(ctx.guild.id), TOKEN_NAME)
    actual_balance = float(user_from['real_actual_balance']) + float(userdata_balance['Adjust'])

    if amount > MaxTX:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be bigger than '
                       f'{num_format_coin(MaxTX)} '
                       f'{TOKEN_NAME}.')
        return
    elif amount < MinTx:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be smaller than '
                       f'{num_format_coin(MinTx)} '
                       f'{TOKEN_NAME}.')
        return
    elif amount > actual_balance:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Insufficient balance to do a guild free tip of '
                       f'{num_format_coin(amount)} '
                       f'{TOKEN_NAME}.')
        return

    attend_list = []
    ts = datetime.utcnow()
    try:
        embed = discord.Embed(title=f"Guild Free Tip appears {num_format_coin(amount)}{TOKEN_NAME}", description=f"React {EMOJI_PARTY} to collect", timestamp=ts, color=0x00ff00)
        embed.add_field(name="Individual Tip Amount", value=f"{num_format_coin(amount)}{TOKEN_NAME}", inline=True)
        embed.add_field(name="Num. Attendees", value="**0** members", inline=True)
        if comment and len(comment) > 0:
            embed.add_field(name="Comment", value=comment, inline=True)
        embed.set_footer(text=f"Guild Free Tip by in {ctx.guild.name} / issued by {ctx.message.author.name}#{ctx.message.author.discriminator}, timeout: {seconds_str(duration_s)}")
        msg = await ctx.send(embed=embed)
        await msg.add_reaction(EMOJI_PARTY)
    except (discord.errors.NotFound, discord.errors.Forbidden) as e:
        await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
        return
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())

    if ctx.guild.id not in TX_IN_PROCESS:
        TX_IN_PROCESS.append(ctx.guild.id)

    prev = []
    add_index = 0
    start_time = time.time()
    time_left = duration_s
    while time_left > 0:
        # Retrieve new reactions
        try:
            _msg: discord.Message = await ctx.fetch_message(msg.id)

            for r in _msg.reactions:
                # Find reaction we're looking for
                if str(r.emoji) == EMOJI_PARTY:
                    # Get list of Users that reacted & filter bots out
                    attend_list = [i for i in await r.users().flatten() if not i.bot and i != ctx.message.author]

                    # Check if there's been a change, otherwise delay & recheck
                    if set(attend_list) == set(prev) or len(attend_list) == 0:
                        await asyncio.sleep(0.25)
                        break

                    attend_list_names = " | ".join([str(u.name) + "#" + str(u.discriminator) for u in attend_list])
                    if len(attend_list_names) >= 1000: attend_list_names = attend_list_names[:1000]
                    embed = discord.Embed(title=f"Guild free tip appears {num_format_coin(amount)}{TOKEN_NAME}", description=f"React {EMOJI_PARTY} to collect", timestamp=ts, color=0x00ff00)
                    if comment and len(comment) > 0:
                        embed.add_field(name="Comment", value=comment, inline=False)
                    try:
                        if len(attend_list) > 0:
                            embed.add_field(name='Attendees', value=attend_list_names, inline=False)
                            embed.add_field(name='Individual Tip amount', value=f"{num_format_coin(round(amount / len(attend_list), 4))}{TOKEN_NAME}", inline=True)
                            embed.add_field(name="Num. Attendees", value=f"**{len(attend_list)}** members", inline=True)
                    except Exception as e:
                        traceback.print_exc(file=sys.stdout)
                    embed.set_footer(text=f"Guild Free Tip by in {ctx.guild.name} / issued by {ctx.message.author.name}#{ctx.message.author.discriminator}, Time Left: {seconds_str(duration_s)}")
                    await _msg.edit(embed=embed)
                    prev = attend_list

            time_left = duration_s - (time.time() - start_time)

        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())

    if len(attend_list) == 0:
        embed = discord.Embed(title=f"Guild Free Tip appears {num_format_coin(amount)}{TOKEN_NAME}", description=f"Already expired", timestamp=ts, color=0x00ff00)
        if comment and len(comment) > 0:
            embed.add_field(name="Comment", value=comment, inline=False)
        embed.set_footer(text=f"Guild Free tip in {ctx.guild.name} / issued by {ctx.message.author.name}#{ctx.message.author.discriminator}, and no one collected!")
        try:
            await msg.edit(embed=embed)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
        if ctx.guild.id in TX_IN_PROCESS:
            TX_IN_PROCESS.remove(ctx.guild.id)
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    attend_list_id = [u.id for u in attend_list if not u.bot and u != ctx.message.author]

    # TODO, add one by one
    # re-check balance
    userdata_balance = await store.sql_user_balance(str(ctx.guild.id), TOKEN_NAME)
    actual_balance = float(user_from['real_actual_balance']) + float(userdata_balance['Adjust'])

    if amount > actual_balance:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Insufficient balance to do a free tip of '
                       f'{num_format_coin(amount)} '
                       f'{TOKEN_NAME}.')
        if ctx.guild.id in TX_IN_PROCESS:
            TX_IN_PROCESS.remove(ctx.guild.id)
        return
    # end of re-check balance

    # Multiple tip here
    notifyList = await store.sql_get_tipnotify()
    amountDiv = round(amount / len(attend_list_id), 4)
    tips = None

    try:
        tips = await store.sql_mv_erc_multiple(str(ctx.guild.id), attend_list_id, amountDiv, TOKEN_NAME, "TIPALL", token_info['contract'])
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
    if ctx.guild.id in TX_IN_PROCESS:
        TX_IN_PROCESS.remove(ctx.guild.id)

    if tips:
        tipAmount = num_format_coin(amount)
        ActualSpend_str = num_format_coin(amountDiv * len(attend_list_id))
        amountDiv_str = num_format_coin(amountDiv)
        numMsg = 0
        for each_id in attend_list_id:
            member = bot.get_user(id=each_id)
            # TODO: set limit here 100
            dm_user = bool(random.getrandbits(1)) if len(attend_list_id) > 100 else True
            if ctx.message.author.id != member.id and member.id != bot.user.id and str(member.id) not in notifyList:
                try:
                    if dm_user:
                        try:
                            await member.send(f'{EMOJI_MONEYFACE} You had collected a guild free tip of {amountDiv_str} '
                                              f'{TOKEN_NAME} from {ctx.guild.name} / issued by {ctx.author.name}#{ctx.author.discriminator}\n'
                                              f'{NOTIFICATION_OFF_CMD}')
                            numMsg += 1
                        except (discord.Forbidden, discord.errors.Forbidden) as e:
                            await store.sql_toggle_tipnotify(str(member.id), "OFF")
                except Exception as e:
                    traceback.print_exc(file=sys.stdout)
                    await logchanbot(traceback.format_exc())
        # free tip shall always get DM. Ignore notifyList
        try:
            await ctx.message.author.send(
                    f'{EMOJI_ARROW_RIGHTHOOK} Guild free tip of {tipAmount} '
                    f'{TOKEN_NAME} '
                    f'was sent spread to ({len(attend_list_id)}) members in server `{ctx.guild.name}`.\n'
                    f'Each member got: `{amountDiv_str}{TOKEN_NAME}`\n'
                    f'Actual spending: `{ActualSpend_str}{TOKEN_NAME}`')
        except (discord.Forbidden, discord.errors.Forbidden) as e:
            await store.sql_toggle_tipnotify(str(ctx.message.author.id), "OFF")
        # Edit embed
        try:
            embed = discord.Embed(title=f"Guild Free Tip appears {num_format_coin(amount)}{TOKEN_NAME}", description=f"React {EMOJI_PARTY} to collect", timestamp=ts, color=0x00ff00)
            if comment and len(comment) > 0:
                embed.add_field(name="Comment", value=comment, inline=False)
            if len(attend_list_names) >= 1000: attend_list_names = attend_list_names[:1000]
            try:
                if len(attend_list) > 0:
                    embed.add_field(name='Attendees', value=attend_list_names, inline=False)
                    embed.add_field(name='Individual Tip amount', value=f"{num_format_coin(round(amount / len(attend_list), 4))}{TOKEN_NAME}", inline=True)
                    embed.add_field(name="Num. Attendees", value=f"**{len(attend_list)}** members", inline=True)
            except Exception as e:
                traceback.print_exc(file=sys.stdout)
            embed.set_footer(text=f"Completed! Collected by {len(attend_list_id)} member(s)")
            await msg.edit(embed=embed)
            await msg.add_reaction(EMOJI_OK_BOX)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
        await ctx.message.add_reaction(EMOJI_OK_HAND)
    else:
        await ctx.message.add_reaction(EMOJI_ERROR)
    return


@bot.command(pass_context=True, help='Tip other people')
async def tip(ctx, amount: str, *args):
    global TX_IN_PROCESS
    amount = amount.replace(",", "")
    token_info = await store.get_token_info(TOKEN_NAME)
    try:
        amount = float(amount)
    except ValueError:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid amount.')
        return

    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.send(f'{EMOJI_RED_NO} This command can not be in private.')
        return

    if len(ctx.message.mentions) == 0 and len(ctx.message.role_mentions) == 0:
        # Use how time.
        if len(args) >= 2:
            time_given = None
            if args[0].upper() == "LAST" or args[1].upper() == "LAST":
                # try if the param is 1111u
                num_user = None
                if args[0].upper() == "LAST":
                    num_user = args[1].lower()
                elif args[1].upper() == "LAST":
                    num_user = args[2].lower()
                if 'u' in num_user or 'user' in num_user or 'users' in num_user or 'person' in num_user or 'people' in num_user:
                    num_user = num_user.replace("people", "")
                    num_user = num_user.replace("person", "")
                    num_user = num_user.replace("users", "")
                    num_user = num_user.replace("user", "")
                    num_user = num_user.replace("u", "")
                    try:
                        num_user = int(num_user)
                        if len(ctx.guild.members) <= 2:
                            await ctx.message.add_reaction(EMOJI_ERROR)
                            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Please use normal tip command. There are only few users.')
                            return
                        # Check if we really have that many user in the guild 20%
                        elif num_user >= len(ctx.guild.members):
                            try:
                                await ctx.message.add_reaction(EMOJI_INFORMATION)
                                await ctx.send(f'{ctx.author.mention} Boss, you want to tip more than the number of people in this guild!?.'
                                               ' Can be done :). Wait a while.... I am doing it. (**counting..**)')
                            except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                                # No need to tip if failed to message
                                await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                                return
                            message_talker = await store.sql_get_messages(str(ctx.message.guild.id), str(ctx.message.channel.id), 0, len(ctx.guild.members))
                            if ctx.message.author.id in message_talker:
                                message_talker.remove(ctx.message.author.id)
                            if len(message_talker) == 0:
                                await ctx.message.add_reaction(EMOJI_ERROR)
                                await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} There is not sufficient user to count.')
                            elif len(message_talker) < len(ctx.guild.members) - 1: # minus bot
                                await ctx.send(f'{EMOJI_INFORMATION} {ctx.author.mention} I could not find sufficient talkers up to **{num_user}**. I found only **{len(message_talker)}**'
                                               f' and tip to those **{len(message_talker)}** users if they are still here.')
                                # tip all user who are in the list
                                try:
                                    async with ctx.typing():
                                        await _tip_talker(ctx, amount, message_talker, False, TOKEN_NAME)
                                except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                                    await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                                    # zipped mouth but still need to do tip talker
                                    await _tip_talker(ctx, amount, message_talker, False, TOKEN_NAME)
                                except Exception as e:
                                    traceback.print_exc(file=sys.stdout)
                                    await logchanbot(traceback.format_exc())
                            return
                        elif num_user > 0:
                            message_talker = await store.sql_get_messages(str(ctx.message.guild.id), str(ctx.message.channel.id), 0, num_user + 1)
                            if ctx.message.author.id in message_talker:
                                message_talker.remove(ctx.message.author.id)
                            else:
                                # remove the last one
                                message_talker.pop()
                            if len(message_talker) == 0:
                                await ctx.message.add_reaction(EMOJI_ERROR)
                                await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} There is not sufficient user to count.')
                            elif len(message_talker) < num_user:
                                try:
                                    await ctx.message.add_reaction(EMOJI_INFORMATION)
                                    await ctx.send(f'{EMOJI_INFORMATION} {ctx.author.mention} I could not find sufficient talkers up to **{num_user}**. I found only **{len(message_talker)}**'
                                                   f' and tip to those **{len(message_talker)}** users if they are still here.')
                                except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                                    # No need to tip if failed to message
                                    await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                                    return
                                # tip all user who are in the list
                                try:
                                    async with ctx.typing():
                                        await _tip_talker(ctx, amount, message_talker, False, TOKEN_NAME)
                                except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                                    await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                                    # zipped mouth but still need to do tip talker
                                    await _tip_talker(ctx, amount, message_talker, False, TOKEN_NAME)
                                except Exception as e:
                                    traceback.print_exc(file=sys.stdout)
                                    await logchanbot(traceback.format_exc())
                            else:
                                try:
                                    async with ctx.typing():
                                        await _tip_talker(ctx, amount, message_talker, False, TOKEN_NAME)
                                except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                                    await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                                    # zipped mouth but still need to do tip talker
                                    await _tip_talker(ctx, amount, message_talker, False, TOKEN_NAME)
                                except Exception as e:
                                    traceback.print_exc(file=sys.stdout)
                                    await logchanbot(traceback.format_exc())
                                return
                            return
                        else:
                            await ctx.message.add_reaction(EMOJI_ERROR)
                            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} What is this **{num_user}** number? Please give a number bigger than 0 :) ')
                            return
                    except ValueError:
                        await ctx.message.add_reaction(EMOJI_ERROR)
                        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid param after **LAST**.')
                    return
                time_string = ctx.message.content.lower().split("last", 1)[1].strip()
                time_second = None
                try:
                    time_string = time_string.replace("years", "y")
                    time_string = time_string.replace("yrs", "y")
                    time_string = time_string.replace("yr", "y")
                    time_string = time_string.replace("year", "y")
                    time_string = time_string.replace("months", "mon")
                    time_string = time_string.replace("month", "mon")
                    time_string = time_string.replace("mons", "mon")
                    time_string = time_string.replace("weeks", "w")
                    time_string = time_string.replace("week", "w")

                    time_string = time_string.replace("day", "d")
                    time_string = time_string.replace("days", "d")

                    time_string = time_string.replace("hours", "h")
                    time_string = time_string.replace("hour", "h")
                    time_string = time_string.replace("hrs", "h")
                    time_string = time_string.replace("hr", "h")

                    time_string = time_string.replace("minutes", "mn")
                    time_string = time_string.replace("mns", "mn")
                    time_string = time_string.replace("mins", "mn")
                    time_string = time_string.replace("min", "mn")
                    time_string = time_string.replace("m", "mn")

                    mult = {'y': 12*30*24*60*60, 'mon': 30*24*60*60, 'w': 7*24*60*60, 'd': 24*60*60, 'h': 60*60, 'mn': 60}
                    time_second = sum(int(num) * mult.get(val, 1) for num, val in re.findall('(\d+)(\w+)', time_string))
                except Exception as e:
                    traceback.print_exc(file=sys.stdout)
                    await logchanbot(traceback.format_exc())
                    await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid time given. Please use this example: `.tip 10 last 12mn`')
                    return
                try:
                    time_given = int(time_second)
                except ValueError:
                    await ctx.message.add_reaction(EMOJI_ERROR)
                    await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid time given check.')
                    return
                if time_given:
                    if time_given < 5*60 or time_given > 2*24*60*60:
                        await ctx.message.add_reaction(EMOJI_ERROR)
                        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Please give try time inteval between 5minutes to 24hours.')
                        return
                    else:
                        message_talker = await store.sql_get_messages(str(ctx.message.guild.id), str(ctx.message.channel.id), time_given, None)
                        if len(message_talker) == 0:
                            await ctx.message.add_reaction(EMOJI_ERROR)
                            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} There is no active talker in such period.')
                            return
                        else:
                            try:
                                async with ctx.typing():
                                    await _tip_talker(ctx, amount, message_talker, False, TOKEN_NAME)
                            except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                                await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                                # zipped mouth but still need to do tip talker
                                await _tip_talker(ctx, amount, message_talker, False, TOKEN_NAME)
                            except Exception as e:
                                traceback.print_exc(file=sys.stdout)
                                await logchanbot(traceback.format_exc())
                            return
            else:
                await ctx.message.add_reaction(EMOJI_ERROR)
                try:
                    await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} You need at least one person to tip to.')
                except (discord.Forbidden, discord.errors.Forbidden) as e:
                    try:
                        await ctx.message.author.send(f'{EMOJI_RED_NO} {ctx.author.mention} You need at least one person to tip to.')
                    except (discord.Forbidden, discord.errors.Forbidden) as e:
                        return
                return
        else:
            await ctx.message.add_reaction(EMOJI_ERROR)
            try:
                await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} You need at least one person to tip to.')
            except (discord.Forbidden, discord.errors.Forbidden) as e:
                try:
                    await ctx.message.author.send(f'{EMOJI_RED_NO} {ctx.author.mention} You need at least one person to tip to.')
                except (discord.Forbidden, discord.errors.Forbidden) as e:
                    return
            return
    elif len(ctx.message.mentions) == 1 and (bot.user in ctx.message.mentions):
        # Tip to TipBot
        member = ctx.message.mentions[0]
        print('TipBot is receiving tip from {} amount: {}{}'.format(ctx.message.author.name, amount, TOKEN_NAME))
    elif len(ctx.message.mentions) == 1 and (bot.user not in ctx.message.mentions):
        member = ctx.message.mentions[0]
        if ctx.message.author.id == member.id:
            await ctx.message.add_reaction(EMOJI_ERROR)
            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Tip me if you want.')
            return
    elif len(ctx.message.role_mentions) >= 1:
        mention_roles = ctx.message.role_mentions
        if "@everyone" in mention_roles:
            mention_roles.remove("@everyone")
            if len(mention_roles) < 1:
                await ctx.message.add_reaction(EMOJI_ERROR)
                await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Can not find user to tip to.')
                return
        async with ctx.typing():
            await _tip(ctx, amount, TOKEN_NAME)
            return
    elif len(ctx.message.mentions) > 1:
        async with ctx.typing():
            await _tip(ctx, amount, TOKEN_NAME)
            return

    MinTx = token_info['real_min_tip']
    MaxTX = token_info['real_max_tip']
    user_from = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME)
    if user_from is None:
        w = await create_address_eth()
        user_from = await store.sql_register_user(str(ctx.message.author.id), TOKEN_NAME, w, 'DISCORD')
        user_from = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME)
    userdata_balance = await store.sql_user_balance(str(ctx.message.author.id), TOKEN_NAME)

    actual_balance = float(user_from['real_actual_balance']) + float(userdata_balance['Adjust'])
    if amount < MinTx:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.author.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be smaller than '
                              f'{num_format_coin(MinTx)} '
                              f'{TOKEN_NAME}.')
        return
    elif amount > MaxTX:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.author.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be bigger than '
                              f'{num_format_coin(MaxTX)} '
                              f'{TOKEN_NAME}.')
        return
    elif amount > actual_balance:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.author.send(f'{EMOJI_RED_NO} {ctx.author.mention} Insufficient balance to send tip of '
                              f'{num_format_coin(amount)} '
                              f'{TOKEN_NAME} to {member.name}#{member.discriminator}.')
        return
    if ctx.message.author.id not in TX_IN_PROCESS:
        TX_IN_PROCESS.append(ctx.message.author.id)
        try:
            tip = await store.sql_mv_erc_single(str(ctx.message.author.id), str(member.id), amount, TOKEN_NAME, "TIP", token_info['contract'])
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
        TX_IN_PROCESS.remove(ctx.message.author.id)
    else:
        # reject and tell to wait
        await ctx.message.add_reaction(EMOJI_HOURGLASS_NOT_DONE)
        msg = await ctx.author.send(f'{EMOJI_RED_NO} {ctx.author.mention} You have another tx in process. Please wait it to finish. ')
        await msg.add_reaction(EMOJI_OK_BOX)
        return
    
    notifyList = await store.sql_get_tipnotify()
    if tip:
        try:
            await ctx.message.author.send(
                f'{EMOJI_ARROW_RIGHTHOOK} Tip of {num_format_coin(amount)} '
                f'{TOKEN_NAME} '
                f'was sent to {member.name}#{member.discriminator} in server `{ctx.guild.name}`\n')
        except (discord.Forbidden, discord.errors.Forbidden) as e:
            await store.sql_toggle_tipnotify(str(ctx.message.author.id), "OFF")
        try:
            await ctx.send(
                f'{EMOJI_MONEYFACE} {member.mention} got a tip of {num_format_coin(amount)} '
                f'{TOKEN_NAME} from {ctx.message.author.name}#{ctx.message.author.discriminator}')
        except (discord.Forbidden, discord.errors.Forbidden) as e:
            if bot.user.id != member.id and str(member.id) not in notifyList:
                try:
                    await member.send(f'{EMOJI_MONEYFACE} You got a tip of {num_format_coin(amount)} '
                                      f'{TOKEN_NAME} from {ctx.message.author.name}#{ctx.message.author.discriminator} in server `{ctx.guild.name}`\n'
                                      f'{NOTIFICATION_OFF_CMD}')
                except (discord.Forbidden, discord.errors.Forbidden) as e:
                    await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                    await store.sql_toggle_tipnotify(str(member.id), "OFF")
        await ctx.message.add_reaction(EMOJI_OK_HAND)
        return
    else:
        await ctx.message.add_reaction(EMOJI_ERROR)
    return


@bot.command(pass_context=True, name='gtip', aliases=['guildtip', 'mtip'], help='Tip other people from guild balance')
@commands.has_permissions(manage_channels=True)
async def gtip(ctx, amount: str, *args):
    global TX_IN_PROCESS
    amount = amount.replace(",", "")
    token_info = await store.get_token_info(TOKEN_NAME)
    try:
        amount = float(amount)
    except ValueError:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid amount.')
        return

    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.send(f'{EMOJI_RED_NO} This command can not be in private.')
        return

    if len(ctx.message.mentions) == 0 and len(ctx.message.role_mentions) == 0:
        # Use how time.
        if len(args) >= 2:
            time_given = None
            if args[0].upper() == "LAST" or args[1].upper() == "LAST":
                # try if the param is 1111u
                num_user = None
                if args[0].upper() == "LAST":
                    num_user = args[1].lower()
                elif args[1].upper() == "LAST":
                    num_user = args[2].lower()
                if 'u' in num_user or 'user' in num_user or 'users' in num_user or 'person' in num_user or 'people' in num_user:
                    num_user = num_user.replace("people", "")
                    num_user = num_user.replace("person", "")
                    num_user = num_user.replace("users", "")
                    num_user = num_user.replace("user", "")
                    num_user = num_user.replace("u", "")
                    try:
                        num_user = int(num_user)
                        if len(ctx.guild.members) <= 2:
                            await ctx.message.add_reaction(EMOJI_ERROR)
                            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Please use normal tip command. There are only few users.')
                            return
                        # Check if we really have that many user in the guild 20%
                        elif num_user >= len(ctx.guild.members):
                            try:
                                await ctx.message.add_reaction(EMOJI_INFORMATION)
                                await ctx.send(f'{ctx.author.mention} Boss, you want to tip more than the number of people in this guild!?.'
                                               ' Can be done :). Wait a while.... I am doing it. (**counting..**)')
                            except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                                # No need to tip if failed to message
                                await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                                return
                            message_talker = await store.sql_get_messages(str(ctx.message.guild.id), str(ctx.message.channel.id), 0, len(ctx.guild.members))
                            if ctx.message.author.id in message_talker:
                                message_talker.remove(ctx.message.author.id)
                            if len(message_talker) == 0:
                                await ctx.message.add_reaction(EMOJI_ERROR)
                                await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} There is not sufficient user to count.')
                            elif len(message_talker) < len(ctx.guild.members) - 1: # minus bot
                                await ctx.send(f'{EMOJI_INFORMATION} {ctx.author.mention} I could not find sufficient talkers up to **{num_user}**. I found only **{len(message_talker)}**'
                                               f' and tip to those **{len(message_talker)}** users if they are still here.')
                                # tip all user who are in the list
                                try:
                                    async with ctx.typing():
                                        await _tip_talker(ctx, amount, message_talker, True, TOKEN_NAME)
                                except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                                    await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                                    # zipped mouth but still need to do tip talker
                                    await _tip_talker(ctx, amount, message_talker, True, TOKEN_NAME)
                                except Exception as e:
                                    traceback.print_exc(file=sys.stdout)
                                    await logchanbot(traceback.format_exc())
                            return
                        elif num_user > 0:
                            message_talker = await store.sql_get_messages(str(ctx.message.guild.id), str(ctx.message.channel.id), 0, num_user + 1)
                            if ctx.message.author.id in message_talker:
                                message_talker.remove(ctx.message.author.id)
                            else:
                                # remove the last one
                                message_talker.pop()
                            if len(message_talker) == 0:
                                await ctx.message.add_reaction(EMOJI_ERROR)
                                await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} There is not sufficient user to count.')
                            elif len(message_talker) < num_user:
                                try:
                                    await ctx.message.add_reaction(EMOJI_INFORMATION)
                                    await ctx.send(f'{EMOJI_INFORMATION} {ctx.author.mention} I could not find sufficient talkers up to **{num_user}**. I found only **{len(message_talker)}**'
                                                   f' and tip to those **{len(message_talker)}** users if they are still here.')
                                except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                                    # No need to tip if failed to message
                                    await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                                    return
                                # tip all user who are in the list
                                try:
                                    async with ctx.typing():
                                        await _tip_talker(ctx, amount, message_talker, True, TOKEN_NAME)
                                except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                                    await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                                    # zipped mouth but still need to do tip talker
                                    await _tip_talker(ctx, amount, message_talker, True, TOKEN_NAME)
                                except Exception as e:
                                    traceback.print_exc(file=sys.stdout)
                                    await logchanbot(traceback.format_exc())
                            else:
                                try:
                                    async with ctx.typing():
                                        await _tip_talker(ctx, amount, message_talker, True, TOKEN_NAME)
                                except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                                    await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                                    # zipped mouth but still need to do tip talker
                                    await _tip_talker(ctx, amount, message_talker, True, TOKEN_NAME)
                                except Exception as e:
                                    traceback.print_exc(file=sys.stdout)
                                    await logchanbot(traceback.format_exc())
                                return
                            return
                        else:
                            await ctx.message.add_reaction(EMOJI_ERROR)
                            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} What is this **{num_user}** number? Please give a number bigger than 0 :) ')
                            return
                    except ValueError:
                        await ctx.message.add_reaction(EMOJI_ERROR)
                        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid param after **LAST**.')
                    return
                time_string = ctx.message.content.lower().split("last", 1)[1].strip()
                time_second = None
                try:
                    time_string = time_string.replace("years", "y")
                    time_string = time_string.replace("yrs", "y")
                    time_string = time_string.replace("yr", "y")
                    time_string = time_string.replace("year", "y")
                    time_string = time_string.replace("months", "mon")
                    time_string = time_string.replace("month", "mon")
                    time_string = time_string.replace("mons", "mon")
                    time_string = time_string.replace("weeks", "w")
                    time_string = time_string.replace("week", "w")

                    time_string = time_string.replace("day", "d")
                    time_string = time_string.replace("days", "d")

                    time_string = time_string.replace("hours", "h")
                    time_string = time_string.replace("hour", "h")
                    time_string = time_string.replace("hrs", "h")
                    time_string = time_string.replace("hr", "h")

                    time_string = time_string.replace("minutes", "mn")
                    time_string = time_string.replace("mns", "mn")
                    time_string = time_string.replace("mins", "mn")
                    time_string = time_string.replace("min", "mn")
                    time_string = time_string.replace("m", "mn")

                    mult = {'y': 12*30*24*60*60, 'mon': 30*24*60*60, 'w': 7*24*60*60, 'd': 24*60*60, 'h': 60*60, 'mn': 60}
                    time_second = sum(int(num) * mult.get(val, 1) for num, val in re.findall('(\d+)(\w+)', time_string))
                except Exception as e:
                    traceback.print_exc(file=sys.stdout)
                    await logchanbot(traceback.format_exc())
                    await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid time given. Please use this example: `.tip 1,000 last 5h 12mn`')
                    return
                try:
                    time_given = int(time_second)
                except ValueError:
                    await ctx.message.add_reaction(EMOJI_ERROR)
                    await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid time given check.')
                    return
                if time_given:
                    if time_given < 5*60 or time_given > 60*24*60*60:
                        await ctx.message.add_reaction(EMOJI_ERROR)
                        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Please try time inteval between 5minutes to 24hours.')
                        return
                    else:
                        message_talker = await store.sql_get_messages(str(ctx.message.guild.id), str(ctx.message.channel.id), time_given, None)
                        if len(message_talker) == 0:
                            await ctx.message.add_reaction(EMOJI_ERROR)
                            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} There is no active talker in such period.')
                            return
                        else:
                            try:
                                async with ctx.typing():
                                    await _tip_talker(ctx, amount, message_talker, True, TOKEN_NAME)
                            except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                                await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                                # zipped mouth but still need to do tip talker
                                await _tip_talker(ctx, amount, message_talker, True, TOKEN_NAME)
                            except Exception as e:
                                traceback.print_exc(file=sys.stdout)
                                await logchanbot(traceback.format_exc())
                            return
            else:
                await ctx.message.add_reaction(EMOJI_ERROR)
                try:
                    await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} You need at least one person to tip to.')
                except (discord.Forbidden, discord.errors.Forbidden) as e:
                    try:
                        await ctx.message.author.send(f'{EMOJI_RED_NO} {ctx.author.mention} You need at least one person to tip to.')
                    except (discord.Forbidden, discord.errors.Forbidden) as e:
                        return
                return
        else:
            await ctx.message.add_reaction(EMOJI_ERROR)
            try:
                await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} You need at least one person to tip to.')
            except (discord.Forbidden, discord.errors.Forbidden) as e:
                try:
                    await ctx.message.author.send(f'{EMOJI_RED_NO} {ctx.author.mention} You need at least one person to tip to.')
                except (discord.Forbidden, discord.errors.Forbidden) as e:
                    return
            return
    elif len(ctx.message.mentions) == 1 and (bot.user in ctx.message.mentions):
        # Tip to TipBot
        member = ctx.message.mentions[0]
        print('TipBot is receiving tip from {} amount: {}{}'.format(ctx.message.author.name, amount, TOKEN_NAME))
    elif len(ctx.message.mentions) == 1 and (bot.user not in ctx.message.mentions):
        member = ctx.message.mentions[0]
        if ctx.message.author.id == member.id:
            await ctx.message.add_reaction(EMOJI_ERROR)
            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Tip me if you want.')
            return
    elif len(ctx.message.role_mentions) >= 1:
        mention_roles = ctx.message.role_mentions
        if "@everyone" in mention_roles:
            mention_roles.remove("@everyone")
            if len(mention_roles) < 1:
                await ctx.message.add_reaction(EMOJI_ERROR)
                await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Can not find user to tip to.')
                return
        async with ctx.typing():
            await _tip(ctx, amount, TOKEN_NAME, True)
            return
    elif len(ctx.message.mentions) > 1:
        async with ctx.typing():
            await _tip(ctx, amount, TOKEN_NAME, True)
            return

    MinTx = token_info['real_min_tip']
    MaxTX = token_info['real_max_tip']
    user_from = await store.sql_get_userwallet(str(ctx.guild.id), TOKEN_NAME)
    if user_from is None:
        w = await create_address_eth()
        user_from = await store.sql_register_user(str(ctx.guild.id), TOKEN_NAME, w, 'DISCORD')
        user_from = await store.sql_get_userwallet(str(ctx.guild.id), TOKEN_NAME)
    userdata_balance = await store.sql_user_balance(str(ctx.guild.id), TOKEN_NAME)
    actual_balance = float(user_from['real_actual_balance']) + float(userdata_balance['Adjust'])
    if amount < MinTx:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be smaller than '
                       f'{num_format_coin(MinTx)} '
                       f'{TOKEN_NAME}.')
        return
    elif amount > MaxTX:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be bigger than '
                       f'{num_format_coin(MaxTX)} '
                       f'{TOKEN_NAME}.')
        return
    elif amount > actual_balance:
            await ctx.message.add_reaction(EMOJI_ERROR)
            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Insufficient guild balance to send tip of '
                            f'{num_format_coin(amount)} '
                            f'{TOKEN_NAME} to {member.name}#{member.discriminator}.')
            return
    if ctx.guild.id not in TX_IN_PROCESS:
        TX_IN_PROCESS.append(ctx.guild.id)
        try:
            tip = await store.sql_mv_erc_single(str(ctx.guild.id), str(member.id), amount, TOKEN_NAME, "TIP", token_info['contract'])
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
        TX_IN_PROCESS.remove(ctx.guild.id)
    else:
        # reject and tell to wait
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Your guild has another tx in process. Please wait it to finish. ')
        await msg.add_reaction(EMOJI_OK_BOX)
        return
    
    notifyList = await store.sql_get_tipnotify()

    if tip:
        try:
            await ctx.message.author.send(
                f'{EMOJI_ARROW_RIGHTHOOK} Guild Tip of {num_format_coin(amount)} '
                f'{TOKEN_NAME} '
                f'was sent to {member.name}#{member.discriminator} in server `{ctx.guild.name}`\n')
        except (discord.Forbidden, discord.errors.Forbidden) as e:
            await store.sql_toggle_tipnotify(str(ctx.message.author.id), "OFF")
        try:
            await ctx.send(
                f'{EMOJI_MONEYFACE} {member.mention} got a guild tip of {num_format_coin(amount)} '
                f'{TOKEN_NAME} from {ctx.message.author.name}#{ctx.message.author.discriminator}')
        except (discord.Forbidden, discord.errors.Forbidden) as e:
            if bot.user.id != member.id and str(member.id) not in notifyList:
                try:
                    await member.send(f'{EMOJI_MONEYFACE} You got a guild tip of {num_format_coin(amount)} '
                                      f'{TOKEN_NAME} from {ctx.message.author.name}#{ctx.message.author.discriminator} in server `{ctx.guild.name}`\n'
                                      f'{NOTIFICATION_OFF_CMD}')
                except (discord.Forbidden, discord.errors.Forbidden) as e:
                    await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
                    await store.sql_toggle_tipnotify(str(member.id), "OFF")
        await ctx.message.add_reaction(EMOJI_OK_HAND)
        return
    else:
        await ctx.message.add_reaction(EMOJI_ERROR)
    return

@bot.command(pass_context=True, help='Tip all online user')
async def tipall(ctx, amount: str, user: str='ONLINE'):
    global TX_IN_PROCESS
    token_info = await store.get_token_info(TOKEN_NAME)
    MinTx = token_info['real_min_tip']
    MaxTX = token_info['real_max_tip']

    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.send(f'{EMOJI_RED_NO} This command can not be in private.')
        return

    amount = amount.replace(",", "")
    try:
        amount = float(amount)
    except ValueError:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid amount.')
        return

    user_from = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME)
    if user_from is None:
        w = await create_address_eth()
        user_from = await store.sql_register_user(str(ctx.message.author.id), TOKEN_NAME, w, 'DISCORD')
        user_from = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME)
    userdata_balance = await store.sql_user_balance(str(ctx.message.author.id), TOKEN_NAME)
    actual_balance = float(user_from['real_actual_balance']) + float(userdata_balance['Adjust'])
    if amount < MinTx:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be smaller than '
                       f'{num_format_coin(MinTx)} '
                       f'{TOKEN_NAME}.')
        return
    elif amount > MaxTX:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be bigger than '
                       f'{num_format_coin(MaxTX)} '
                       f'{TOKEN_NAME}.')
        return
    elif amount > actual_balance:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Insufficient balance to send tip of '
                       f'{num_format_coin(amount)} '
                       f'{TOKEN_NAME}.')
        return

    listMembers = [member for member in ctx.guild.members if member.status != discord.Status.offline and member.bot == False]
    if user.upper() == "ANY":
        listMembers = [member for member in ctx.guild.members]
    if len(listMembers) <= 1:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} There is no number of users.')
        return

    notifyList = await store.sql_get_tipnotify()
    print("Number of tip-all in {}: {}".format(ctx.guild.name, len(listMembers)))
    memids = []  # list of member ID
    for member in listMembers:
        # print(member.name) # you'll just print out Member objects your way.
        if ctx.message.author.id != member.id:
            memids.append(str(member.id))
    amountDiv = round(amount / len(memids), 4)
    if (amount / len(memids)) < MinTx:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be smaller than '
                       f'{num_format_coin(MinTx)} '
                       f'{TOKEN_NAME} for each member. You need at least {num_format_coin(len(memids) * MinTx)}{TOKEN_NAME}.')
        return
    if ctx.message.author.id not in TX_IN_PROCESS:
        TX_IN_PROCESS.append(ctx.message.author.id)
        try:
            tips = await store.sql_mv_erc_multiple(str(ctx.message.author.id), memids, amountDiv, TOKEN_NAME, "TIPALL", token_info['contract'])
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
        TX_IN_PROCESS.remove(ctx.message.author.id)
    else:
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} You have another tx in process. Please wait it to finish. ')
        await msg.add_reaction(EMOJI_OK_BOX)
        return
    if tips:
        tipAmount = num_format_coin(amount)
        ActualSpend_str = num_format_coin(amountDiv * len(memids))
        amountDiv_str = num_format_coin(amountDiv)
        numMsg = 0
        for member in listMembers:
            # TODO: set limit here 50
            dm_user = bool(random.getrandbits(1)) if len(listMembers) > 50 else True
            if ctx.message.author.id != member.id and member.id != bot.user.id and str(member.id) not in notifyList:
                try:
                    if dm_user:
                        try:
                            await member.send(f'{EMOJI_MONEYFACE} You got a tip of {amountDiv_str} '
                                              f'{TOKEN_NAME} from {ctx.message.author.name}#{ctx.message.author.discriminator} `{config.discord.prefixCmd}tipall` in server `{ctx.guild.name}`\n'
                                              f'{NOTIFICATION_OFF_CMD}')
                            numMsg += 1
                        except (discord.Forbidden, discord.errors.Forbidden) as e:
                            await store.sql_toggle_tipnotify(str(member.id), "OFF")
                except Exception as e:
                    traceback.print_exc(file=sys.stdout)
                    await logchanbot(traceback.format_exc())
        # tipper shall always get DM. Ignore notifyList
        try:
            await ctx.message.author.send(
                    f'{EMOJI_ARROW_RIGHTHOOK} Tip of {tipAmount} '
                    f'{TOKEN_NAME} '
                    f'was sent spread to ({len(memids)}) members in server `{ctx.guild.name}`.\n'
                    f'Each member got: `{amountDiv_str}{TOKEN_NAME}`\n'
                    f'Actual spending: `{ActualSpend_str}{TOKEN_NAME}`')
        except (discord.Forbidden, discord.errors.Forbidden) as e:
            await store.sql_toggle_tipnotify(str(ctx.message.author.id), "OFF")
        await ctx.message.add_reaction(EMOJI_OK_HAND)
    else:
        await ctx.message.add_reaction(EMOJI_ERROR)
    return



@bot.command(pass_context=True, name='balance', aliases=['bal'], help=bot_help_balance)
async def balance(ctx):
    global TX_IN_PROCESS
    wallet = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME, 'DISCORD')
    if wallet is None:
        w = await create_address_eth()
        userregister = await store.sql_register_user(str(ctx.message.author.id), TOKEN_NAME, w, 'DISCORD')
        wallet = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME, 'DISCORD')
    if wallet:
        embed = discord.Embed(title=f'Balance for {ctx.message.author.name}#{ctx.message.author.discriminator}', description='`You need <Spendable> for withdraw/tip.`', timestamp=datetime.utcnow(), colour=7047495)
        embed.set_author(name=ctx.message.author.name, icon_url=ctx.message.author.avatar_url)
        deposit_balance = await store.http_wallet_getbalance(wallet['balance_wallet_address'], TOKEN_NAME)
        
        token_info = await store.get_token_info(TOKEN_NAME)
        real_deposit_balance = round(deposit_balance / 10**token_info['token_decimal'], 6)
    
        embed.add_field(name="Deposited", value="`{}{}`".format(num_format_coin(real_deposit_balance), TOKEN_NAME), inline=True)
        try:
            note = ''
            if ctx.message.author.id in TX_IN_PROCESS:
                note = '*You have some a tx in progress. Balance is being updated.*'
            userdata_balance = await store.sql_user_balance(str(ctx.message.author.id), TOKEN_NAME, 'DISCORD')
            balance_actual = num_format_coin(wallet['real_actual_balance'] + userdata_balance['Adjust'])
            embed.add_field(name="Spendable", value="`{}{}`".format(balance_actual, TOKEN_NAME), inline=True)
            total_balance = real_deposit_balance + wallet['real_actual_balance'] + userdata_balance['Adjust']
            embed.add_field(name="Total", value="`{}{}`".format(num_format_coin(total_balance), TOKEN_NAME), inline=False)
            embed.set_footer(text=f"Minimum {str(config.moon.min_move_deposit)}{config.moon.ticker} in deposit is required to (auto)transfer to **Spendable**.")
            try:
                # Try DM first, if failed, send to public
                msg = await ctx.author.send(embed=embed)
                await ctx.message.add_reaction(EMOJI_OK_HAND)
            except (discord.Forbidden, discord.errors.Forbidden) as e:
                traceback.print_exc(file=sys.stdout)
                try:
                    msg = await ctx.send(embed=embed)
                    await msg.add_reaction(EMOJI_OK_BOX)
                    await ctx.message.add_reaction(EMOJI_OK_HAND)
                except (discord.Forbidden, discord.errors.Forbidden) as e:
                    traceback.print_exc(file=sys.stdout)
                    await logchanbot(traceback.format_exc())
                    await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)            
            return
        except Exception as e:
            await ctx.message.add_reaction(EMOJI_ERROR)
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
    return


@bot.command(pass_context=True, aliases=['botbal'], help='Get bot\'s balance')
async def botbalance(ctx, member: discord.Member):
    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} This command can not be in DM.')
        return
    if member.bot == False:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Command is only for bot!!')
        return

    wallet = await store.sql_get_userwallet(str(member.id), TOKEN_NAME, 'DISCORD')
    if wallet is None:
        w = await create_address_eth()
        userregister = await store.sql_register_user(str(member.id), TOKEN_NAME, w, 'DISCORD')
        wallet = await store.sql_get_userwallet(str(member.id), TOKEN_NAME, 'DISCORD')

    if wallet:
        embed = discord.Embed(title=f'Bot balance for {member.name}#{member.discriminator}', description='`This is bot\'s tipjar address. Do not deposit here unless you want to deposit to this bot`', timestamp=datetime.utcnow(), colour=7047495)
        embed.add_field(name="Bot Deposit Address", value="`{}`".format(wallet['balance_wallet_address']), inline=False)
        embed.set_author(name=member.name, icon_url=member.avatar_url)
        deposit_balance = await store.http_wallet_getbalance(wallet['balance_wallet_address'], TOKEN_NAME)
        
        token_info = await store.get_token_info(TOKEN_NAME)
        real_deposit_balance = deposit_balance / 10**token_info['token_decimal']
    
        embed.add_field(name="Deposited", value="`{}{}`".format(num_format_coin(real_deposit_balance), TOKEN_NAME), inline=True)
        try:
            note = ''
            if ctx.guild.id in TX_IN_PROCESS:
                note = '*There are some a tx in progress. Balance is being updated.*'
            userdata_balance = await store.sql_user_balance(str(member.id), TOKEN_NAME, 'DISCORD')
            balance_actual = num_format_coin(float(wallet['real_actual_balance']) + float(userdata_balance['Adjust']))
            embed.add_field(name="Spendable", value="`{}{}`".format(balance_actual, TOKEN_NAME), inline=True)
            total_balance = real_deposit_balance + float(wallet['real_actual_balance']) + float(userdata_balance['Adjust'])
            embed.add_field(name="Total", value="`{}{}`".format(num_format_coin(total_balance), TOKEN_NAME), inline=False)
            embed.set_footer(text=f"Minimum {str(config.moon.min_move_deposit)}{config.moon.ticker} in deposit is required to (auto)transfer to **Spendable**.")
            await ctx.message.add_reaction(EMOJI_OK_HAND)
            msg = await ctx.send(embed=embed)
            await msg.add_reaction(EMOJI_OK_BOX)
            return
        except Exception as e:
            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Internal Error')
            await ctx.message.add_reaction(EMOJI_ERROR)
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
    else:
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Internal Error')
        await ctx.message.add_reaction(EMOJI_ERROR)
    return


@bot.command(pass_context=True, name='mbalance', aliases=['mbal', 'gbal'], help='Balance and deposit to guild')
async def mbalance(ctx):
    if isinstance(ctx.channel, discord.DMChannel) == True:
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} This command can not be in DM.')
        return

    wallet = await store.sql_get_userwallet(str(ctx.guild.id), TOKEN_NAME, 'DISCORD')
    if wallet is None:
        w = await create_address_eth()
        userregister = await store.sql_register_user(str(ctx.guild.id), TOKEN_NAME, w, 'DISCORD')
        wallet = await store.sql_get_userwallet(str(ctx.guild.id), TOKEN_NAME, 'DISCORD')
    embed = discord.Embed(title=f'Deposit for Guild {ctx.guild.name} / {ctx.guild.id}', description='`This is guild\'s tipjar address. Do not deposit here unless you want to deposit to this guild`', timestamp=datetime.utcnow(), colour=7047495)

    if wallet:
        embed = discord.Embed(title=f'Guild balance for {ctx.guild.name} / {str(ctx.guild.id)}', description='`Guild balance`', timestamp=datetime.utcnow(), colour=7047495)
        embed.add_field(name="Guild Deposit Address", value="`{}`".format(wallet['balance_wallet_address']), inline=False)
        embed.set_author(name=ctx.message.author.name, icon_url=ctx.message.author.avatar_url)
        deposit_balance = await store.http_wallet_getbalance(wallet['balance_wallet_address'], TOKEN_NAME)
        
        token_info = await store.get_token_info(TOKEN_NAME)
        real_deposit_balance = round(deposit_balance / 10**token_info['token_decimal'], 6)
    
        embed.add_field(name="Deposited", value="`{}{}`".format(num_format_coin(real_deposit_balance), TOKEN_NAME), inline=True)
        try:
            note = ''
            if ctx.guild.id in TX_IN_PROCESS:
                note = '*There are some a tx in progress. Balance is being updated.*'
            userdata_balance = await store.sql_user_balance(str(ctx.guild.id), TOKEN_NAME, 'DISCORD')
            balance_actual = num_format_coin(wallet['real_actual_balance'] + userdata_balance['Adjust'])
            embed.add_field(name="Spendable", value="`{}{}`".format(balance_actual, TOKEN_NAME), inline=True)
            total_balance = real_deposit_balance + wallet['real_actual_balance'] + userdata_balance['Adjust']
            embed.add_field(name="Total", value="`{}{}`".format(num_format_coin(total_balance), TOKEN_NAME), inline=False)
            embed.set_footer(text=f"Minimum {str(config.moon.min_move_deposit)}{config.moon.ticker} in deposit is required to (auto)transfer to **Spendable**.")
            await ctx.message.add_reaction(EMOJI_OK_HAND)
            msg = await ctx.send(embed=embed)
            await msg.add_reaction(EMOJI_OK_BOX)
            return
        except Exception as e:
            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Internal Error')
            await ctx.message.add_reaction(EMOJI_ERROR)
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
    return


@bot.command(pass_context=True, name='deposit', help=bot_help_deposit)
async def deposit(ctx, plain: str='embed'):
    prefix = await get_guild_prefix(ctx)
    wallet = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME, 'DISCORD')
    if wallet is None:
        w = await create_address_eth()
        userregister = await store.sql_register_user(str(ctx.message.author.id), TOKEN_NAME, w, 'DISCORD')
        wallet = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME, 'DISCORD')
    embed = discord.Embed(title=f'Deposit for {ctx.author.name}#{ctx.author.discriminator}', description='This bot\'s still under testing!', timestamp=datetime.utcnow(), colour=7047495)
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar_url)
                    
    if wallet['balance_wallet_address']:
        plain_msg = '{}#{} Your deposit address: ```{}```'.format(ctx.author.name, ctx.author.discriminator, wallet['balance_wallet_address'])
        embed.add_field(name="Deposit Address", value="`{}`".format(wallet['balance_wallet_address']), inline=False)
        if 'user_wallet_address' in wallet and wallet['user_wallet_address'] and isinstance(ctx.channel, discord.DMChannel) == True:
            embed.add_field(name="Withdraw Address", value="`{}`".format(wallet['user_wallet_address']), inline=False)
        elif 'user_wallet_address' in wallet and wallet['user_wallet_address'] and isinstance(ctx.channel, discord.DMChannel) == False:
            embed.add_field(name="Withdraw Address", value="`(Only in DM)`", inline=False)
        embed.set_footer(text=f"Use: {prefix}deposit plain (for plain text)")
        try:
            # Try DM first
            if plain and plain.lower() == 'plain' or plain.lower() == 'text':
                msg = await ctx.author.send(plain_msg)
            else:
                msg = await ctx.author.send(embed=embed)
            await ctx.message.add_reaction(EMOJI_OK_HAND)
        except (discord.Forbidden, discord.errors.Forbidden) as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
            try:
                if plain.lower() == 'plain' or plain.lower() == 'text':
                    msg = await ctx.author.send(plain_msg)
                else:
                    msg = await ctx.send(embed=embed)
                await msg.add_reaction(EMOJI_OK_BOX)
                await ctx.message.add_reaction(EMOJI_OK_HAND)
            except (discord.Forbidden, discord.errors.Forbidden) as e:
                traceback.print_exc(file=sys.stdout)
                await logchanbot(traceback.format_exc())
                await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
    else:
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Internal Error')
        await ctx.message.add_reaction(EMOJI_ERROR)
    return


@bot.command(pass_context=True, name='mdeposit', help='Deposit to Guild')
async def mdeposit(ctx, plain: str='embed'):
    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.message.add_reaction(EMOJI_ERROR) 
        await ctx.send(f'{ctx.author.mention} This command can not be in private.')
        return

    prefix = await get_guild_prefix(ctx)
    wallet = await store.sql_get_userwallet(str(ctx.guild.id), TOKEN_NAME, 'DISCORD')
    if wallet is None:
        w = await create_address_eth()
        userregister = await store.sql_register_user(str(ctx.guild.id), TOKEN_NAME, w, 'DISCORD')
        wallet = await store.sql_get_userwallet(str(ctx.guild.id), TOKEN_NAME, 'DISCORD')
    embed = discord.Embed(title=f'Deposit for Guild {ctx.guild.name} / {ctx.guild.id}', description='This bot\'s still under testing!', timestamp=datetime.utcnow(), colour=7047495)
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar_url)
    if wallet['balance_wallet_address']:
        plain_msg = 'Guild {}/{}\'s deposit address: ```{}```'.format(ctx.guild.name, ctx.guild.id, wallet['balance_wallet_address'])
        embed.add_field(name="Guild Deposit Address", value="`{}`".format(wallet['balance_wallet_address']), inline=False)
        embed.set_footer(text=f"Use: {prefix}deposit plain (for plain text)")
        if plain and plain.lower() == 'plain' or plain.lower() == 'text':
            msg = await ctx.send(plain_msg)
        else:
            msg = await ctx.send(embed=embed)
        await ctx.message.add_reaction(EMOJI_OK_HAND)
        await msg.add_reaction(EMOJI_OK_BOX)
    else:
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Internal Error')
        await ctx.message.add_reaction(EMOJI_ERROR)
    return


@bot.command(pass_context=True, name='register', aliases=['reg'], help=bot_help_register)
async def register(ctx, wallet_address: str):
    if wallet_address.isalnum() == False:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid address:\n'
                       f'`{wallet_address}`')
        return

    if wallet_address.upper().startswith("0X0000000000000000000000000000000"):
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid address:\n'
                             f'`{wallet_address}`')
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    user = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME)
    if user is None:
        w = await create_address_eth()
        userregister = await store.sql_register_user(str(ctx.message.author.id), TOKEN_NAME, w, 'DISCORD')
        user = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME)

    existing_user = user
    # correct print(valid_address)
    valid_address = await store.validate_address(wallet_address)
    valid = False
    if valid_address and valid_address.upper() == wallet_address.upper():
        valid = True
    else:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid address:\n'
                             f'`{wallet_address}`')
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    # if they want to register with tipjar address
    try:
        if user['balance_wallet_address'].upper() == wallet_address.upper():
            await ctx.message.add_reaction(EMOJI_ERROR)
            msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} You can not register with your tipjar\'s address.\n'
                               f'`{wallet_address}`')
            await msg.add_reaction(EMOJI_OK_BOX)
            return
        else:
            pass
    except Exception as e:
        await ctx.message.add_reaction(EMOJI_ERROR)
        print('Error during register user address:' + str(e))
        return

    # Check if register address in any of user balance address
    check_in_balance_users = await store.sql_check_balance_address_in_users(wallet_address)
    if check_in_balance_users:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} You can not register with any of user\'s tipjar\'s address.\n'
                             f'`{wallet_address}`')
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    if 'user_wallet_address' in existing_user and existing_user['user_wallet_address']:
        prev_address = existing_user['user_wallet_address']
        if prev_address.upper() != wallet_address.upper():
            await store.sql_update_user(str(ctx.message.author.id), wallet_address, TOKEN_NAME)
            await ctx.message.add_reaction(EMOJI_OK_HAND)
            msg = await ctx.send(f'{ctx.author.mention} Your withdraw address has changed from:\n'
                                 f'`{prev_address}`\n to\n '
                                 f'`{wallet_address}`')
            await msg.add_reaction(EMOJI_OK_BOX)
            return
        else:
            await ctx.message.add_reaction(EMOJI_ERROR)
            msg = await ctx.send(f'{ctx.author.mention} Your previous and new address is the same.')
            await msg.add_reaction(EMOJI_OK_BOX)
            return
    else:
        try:
            await store.sql_update_user(str(ctx.message.author.id), wallet_address, TOKEN_NAME)
            await ctx.message.add_reaction(EMOJI_OK_HAND)
            msg = await ctx.send(f'{ctx.author.mention} You have registered a withdraw address.\n'
                                 f'You can use `{config.discord.prefixCmd}withdraw AMOUNT` anytime.')
            await msg.add_reaction(EMOJI_OK_BOX)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
        return


@bot.command(pass_context=True, help=bot_help_withdraw)
async def withdraw(ctx, amount: str):
    global TX_IN_PROCESS
    amount = amount.replace(",", "")
    try:
        amount = float(amount)
    except ValueError:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid given amount for command withdraw.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    token_info = await store.get_token_info(TOKEN_NAME)
    MinTx = float(token_info['real_min_tx'])
    MaxTX = float(token_info['real_max_tx'])

    user_from = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME)
    if user_from is None:
        w = await create_address_eth()
        user_from = await store.sql_register_user(str(ctx.message.author.id), TOKEN_NAME, w, 'DISCORD')
        user_from = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME)

    CoinAddress = None
    if 'user_wallet_address' in user_from and user_from['user_wallet_address'] is None:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} You do not have a withdrawal address, please use '
                             f'`{config.discord.prefixCmd}register wallet_address` to register.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return
    else:
        CoinAddress = user_from['user_wallet_address']


    user_from = await store.sql_get_userwallet(str(ctx.message.author.id), TOKEN_NAME, 'DISCORD')
    userdata_balance = await store.sql_user_balance(str(ctx.message.author.id), TOKEN_NAME, 'DISCORD')
    actual_balance = float(user_from['real_actual_balance']) + float(userdata_balance['Adjust'])

    # If balance 0, no need to check anything
    if actual_balance <= 0:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Please check your **{TOKEN_NAME}** balance.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return
    if amount > actual_balance:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Insufficient balance to send out '
                             f'{num_format_coin(amount)} '
                             f'{TOKEN_NAME}.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    NetFee = token_info['real_withdraw_fee']
    if amount + NetFee > actual_balance:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Insufficient balance to send out '
                             f'{num_format_coin(amount)} '
                             f'{TOKEN_NAME}. You need to leave at least network fee: {num_format_coin(NetFee)}{TOKEN_NAME}')
        await msg.add_reaction(EMOJI_OK_BOX)
        return
    elif amount < MinTx:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transaction cannot be smaller than '
                             f'{num_format_coin(MinTx)} '
                             f'{TOKEN_NAME}.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return
    elif amount > MaxTX:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transaction cannot be bigger than '
                             f'{num_format_coin(MaxTX)} '
                             f'{TOKEN_NAME}.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    SendTx = None
    if ctx.message.author.id not in TX_IN_PROCESS:
        TX_IN_PROCESS.append(ctx.message.author.id)
        try:
            SendTx = await store.sql_external_erc_single(str(ctx.author.id), CoinAddress, amount, TOKEN_NAME, 'DISCORD')
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
        TX_IN_PROCESS.remove(ctx.message.author.id)
    else:
        # reject and tell to wait
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} You have another tx in process. Please wait it to finish. ')
        await msg.add_reaction(EMOJI_OK_BOX)
        return
    if SendTx:
        await ctx.message.add_reaction(EMOJI_OK_BOX)
        msg = await ctx.send(f'{EMOJI_ARROW_RIGHTHOOK} You have withdrawn {num_format_coin(amount)} '
                             f'{TOKEN_NAME} to `{CoinAddress}`.\n'
                             f'Transaction hash: `{SendTx}`')
        await msg.add_reaction(EMOJI_OK_BOX)
    else:
            await ctx.message.add_reaction(EMOJI_ERROR)
    return


# Multiple tip
async def _tip(ctx, amount, coin: str, if_guild: bool=False):
    TOKEN_NAME = coin.upper()
    guild_name = '**{}**'.format(ctx.guild.name) if if_guild == True else ''
    tip_type_text = 'guild tip' if if_guild == True else 'tip'
    guild_or_tip = 'GUILDTIP' if if_guild == True else 'TIPS'
    id_tipper = str(ctx.guild.id) if if_guild == True else str(ctx.message.author.id)

    token_info = await store.get_token_info(TOKEN_NAME)
    MinTx = float(token_info['real_min_tip'])
    MaxTX = float(token_info['real_max_tip'])

    user_from = await store.sql_get_userwallet(id_tipper, TOKEN_NAME)
    if user_from is None:
        w = await create_address_eth()
        user_from = await store.sql_register_user(id_tipper, TOKEN_NAME, w, 'DISCORD')
        user_from = await store.sql_get_userwallet(id_tipper, TOKEN_NAME)

    userdata_balance = await store.sql_user_balance(id_tipper, TOKEN_NAME)
    actual_balance = float(user_from['real_actual_balance']) + float(userdata_balance['Adjust'])
    if amount < MinTx:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be smaller than '
                             f'{num_format_coin(MinTx)} '
                             f'{TOKEN_NAME}.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return
    elif amount > MaxTX:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be bigger than '
                             f'{num_format_coin(MaxTX)} '
                             f'{TOKEN_NAME}.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return
    elif amount > actual_balance:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Insufficient balance to send {tip_type_text} of '
                             f'{num_format_coin(amount)} '
                             f'{TOKEN_NAME}.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    listMembers = []
    if ctx.message.role_mentions and len(ctx.message.role_mentions) >= 1:
        mention_roles = ctx.message.role_mentions
        if "@everyone" in mention_roles:
            mention_roles.remove("@everyone")
        if len(mention_roles) >= 1:
            for each_role in mention_roles:
                role_listMember = [member for member in ctx.guild.members if member.bot == False and each_role in member.roles]
                if len(role_listMember) >= 1:
                    for each_member in role_listMember:
                        if each_member not in listMembers:
                            listMembers.append(each_member)
    else:
        listMembers = ctx.message.mentions

    if len(listMembers) == 0:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} detect zero users.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    memids = []  # list of member ID
    for member in listMembers:
        if ctx.message.author.id != member.id and member in ctx.guild.members:
            memids.append(str(member.id))
    TotalAmount = amount * len(memids)

    if TotalAmount > MaxTX:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Total transaction cannot be bigger than '
                             f'{num_format_coin(MaxTX, TOKEN_NAME)} '
                             f'{TOKEN_NAME}.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return
    elif actual_balance < TotalAmount:
        await ctx.message.add_reaction(EMOJI_ERROR)
        msg = await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} You don\'t have sufficient balance. ')
        await msg.add_reaction(EMOJI_OK_BOX)
        return
    
    notifyList = await store.sql_get_tipnotify()
    try:
        tips = await store.sql_mv_erc_multiple(id_tipper, memids, amount, TOKEN_NAME, "TIPS", token_info['contract'])
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
    if tips:
        tipAmount = num_format_coin(TotalAmount)
        amountDiv_str = num_format_coin(amount)
        # tipper shall always get DM. Ignore notifyList
        try:
            await ctx.message.author.send(
                f'{EMOJI_ARROW_RIGHTHOOK} {tip_type_text} of {tipAmount} '
                f'{TOKEN_NAME} '
                f'was sent to ({len(memids)}) members in server `{ctx.guild.name}`.\n'
                f'Each member got: `{amountDiv_str}{TOKEN_NAME}`\n')
        except (discord.Forbidden, discord.errors.Forbidden) as e:
            await store.sql_toggle_tipnotify(str(ctx.message.author.id), "OFF")
        for member in ctx.message.mentions:
            # print(member.name) # you'll just print out Member objects your way.
            if ctx.message.author.id != member.id and member.id != bot.user.id and str(member.id) not in notifyList:
                try:
                    await member.send(f'{EMOJI_MONEYFACE} You got a {tip_type_text} of `{amountDiv_str}{TOKEN_NAME}` '
                                      f'from {ctx.message.author.name}#{ctx.message.author.discriminator} in server `{ctx.guild.name}`\n'
                                      f'{NOTIFICATION_OFF_CMD}')
                except (discord.Forbidden, discord.errors.Forbidden) as e:
                    await store.sql_toggle_tipnotify(str(member.id), "OFF")
        await ctx.message.add_reaction(EMOJI_OK_HAND)
    else:
        await ctx.message.add_reaction(EMOJI_ERROR)
    return


# Multiple tip
async def _tip_talker(ctx, amount, list_talker, if_guild: bool=False, coin: str = None):
    global TX_IN_PROCESS
    guild_or_tip = 'GUILDTIP' if if_guild == True else 'TIPS'
    guild_name = '**{}**'.format(ctx.guild.name) if if_guild == True else ''
    tip_type_text = 'guild tip' if if_guild == True else 'tip'
    id_tipper = str(ctx.guild.id) if if_guild == True else str(ctx.message.author.id)

    TOKEN_NAME = coin.upper()
    token_info = await store.get_token_info(TOKEN_NAME)
    MinTx = float(token_info['real_min_tip'])
    MaxTX = float(token_info['real_max_tip'])

    try:
        amount = float(amount)
    except ValueError:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid amount.')
        return

    notifyList = await store.sql_get_tipnotify()

    user_from = await store.sql_get_userwallet(id_tipper, TOKEN_NAME)
    if user_from is None:
        w = await create_address_eth()
        user_from = await store.sql_register_user(id_tipper, TOKEN_NAME, w, 'DISCORD')
        user_from = await store.sql_get_userwallet(id_tipper, TOKEN_NAME)

    userdata_balance = await store.sql_user_balance(id_tipper, TOKEN_NAME)
    actual_balance = float(user_from['real_actual_balance']) + float(userdata_balance['Adjust'])

    if amount > MaxTX:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be bigger than '
                       f'{num_format_coin(MaxTX)} '
                       f'{TOKEN_NAME}.')
        return
    elif amount < MinTx:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be smaller than '
                       f'{num_format_coin(MinTx)} '
                       f'{TOKEN_NAME}.')
        return
    elif amount > actual_balance:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Insufficient balance to send {tip_type_text} of '
                       f'{num_format_coin(amount)} '
                       f'{TOKEN_NAME}.')
        return

    list_receivers = []
    for member_id in list_talker:
        try:
            member = bot.get_user(id=int(member_id))
            if member and member in ctx.guild.members and ctx.message.author.id != member.id:
                user_to = await store.sql_get_userwallet(str(member_id), TOKEN_NAME)
                if user_to is None:
                    w = await create_address_eth()
                    userregister = await store.sql_register_user(str(member_id), TOKEN_NAME, w, 'DISCORD')
                    user_to = await store.sql_get_userwallet(str(member_id), TOKEN_NAME)
                try:
                    list_receivers.append(str(member_id))
                except Exception as e:
                    traceback.print_exc(file=sys.stdout)
                    await logchanbot(traceback.format_exc())
                    print('Failed creating wallet for tip talk for userid: {}'.format(member_id))
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())

    # Check number of receivers.
    if len(list_receivers) > config.tipallMax:
        await ctx.message.add_reaction(EMOJI_ERROR)
        try:
            await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} The number of receivers are too many.')
        except (discord.Forbidden, discord.errors.Forbidden) as e:
            await ctx.message.author.send(f'{EMOJI_RED_NO} The number of receivers are too many in `{ctx.guild.name}`.')
        return
    # End of checking receivers numbers.

    TotalAmount = amount * len(list_receivers)

    if TotalAmount > MaxTX:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Total transactions cannot be bigger than '
                       f'{num_format_coin(MaxTX)} '
                       f'{TOKEN_NAME}.')
        return
    elif amount < MinTx:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Total transactions cannot be smaller than '
                       f'{num_format_coin(MinTx)} '
                       f'{TOKEN_NAME}.')
        return
    elif TotalAmount > actual_balance:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} {guild_name} Insufficient balance to send total {tip_type_text} of '
                       f'{num_format_coin(TotalAmount)} '
                       f'{TOKEN_NAME}.')
        return

    if len(list_receivers) < 1:
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} There is no active talker in such period. Please increase more duration or tip directly!')
        return

    # add queue also tip
    if int(id_tipper) not in TX_IN_PROCESS:
        TX_IN_PROCESS.append(int(id_tipper))
    else:
        await ctx.message.add_reaction(EMOJI_HOURGLASS_NOT_DONE)
        msg = await ctx.send(f'{EMOJI_ERROR} {ctx.author.mention} You have another tx in progress.')
        await msg.add_reaction(EMOJI_OK_BOX)
        return

    tip = None
    try:
        tip = await store.sql_mv_erc_multiple(id_tipper, list_receivers, amount, TOKEN_NAME, "TIPS", token_info['contract'])
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())

    # remove queue from tip
    if int(id_tipper) in TX_IN_PROCESS:
        TX_IN_PROCESS.remove(int(id_tipper))

    if tip:
        # tipper shall always get DM. Ignore notifyList
        try:
            await ctx.message.author.send(
                f'{EMOJI_ARROW_RIGHTHOOK} {tip_type_text} of {num_format_coin(TotalAmount)} '
                f'{TOKEN_NAME} '
                f'was sent to ({len(list_receivers)}) members in server `{ctx.guild.name}` for active talking.\n'
                f'Each member got: `{num_format_coin(amount)}{TOKEN_NAME}`\n')
        except (discord.Forbidden, discord.errors.Forbidden) as e:
            await store.sql_toggle_tipnotify(str(ctx.message.author.id), "OFF")
        mention_list_name = ''
        for member_id in list_talker:
            # print(member.name) # you'll just print out Member objects your way.
            if ctx.message.author.id != int(member_id):
                member = bot.get_user(id=int(member_id))
                if member and member.bot == False and member in ctx.guild.members:
                    mention_list_name += '{}#{} '.format(member.name, member.discriminator)
                    if str(member_id) not in notifyList:
                        try:
                            await member.send(
                                f'{EMOJI_MONEYFACE} You got a {tip_type_text} of `{num_format_coin(amount)} {TOKEN_NAME}` '
                                f'from {ctx.message.author.name}#{ctx.message.author.discriminator} in server `{ctx.guild.name}` #{ctx.channel.name} for active talking.\n'
                                f'{NOTIFICATION_OFF_CMD}')
                        except (discord.Forbidden, discord.errors.Forbidden) as e:
                            await store.sql_toggle_tipnotify(str(member.id), "OFF")
        await ctx.message.add_reaction(EMOJI_MONEYFACE)
        try:
            await ctx.send(f'{discord.utils.escape_markdown(mention_list_name)}\n\n**({len(list_receivers)})** members got {tip_type_text} :) for active talking in `{ctx.guild.name}` {ctx.channel.mention} :)')
            await ctx.message.add_reaction(EMOJI_SPEAK)
        except discord.errors.Forbidden:
                await ctx.message.add_reaction(EMOJI_SPEAK)
                await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
        except discord.errors.HTTPException:
            await ctx.message.add_reaction(EMOJI_SPEAK)
            await ctx.send(f'**({len(list_receivers)})** members got {tip_type_text} :) for active talking in `{ctx.guild.name}` {ctx.channel.mention} :)')
        return
    else:
        await ctx.message.add_reaction(EMOJI_ERROR)
        return


async def add_msg_redis(msg: str, delete_temp: bool = False):
    try:
        openRedis()
        key = "MOONTIPBOT:MSG"
        if redis_conn:
            if delete_temp:
                redis_conn.delete(key)
            else:
                redis_conn.lpush(key, msg)
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())


async def store_message_list():
    while True:
        interval_msg_list = 15 # in second
        try:
            openRedis()
            key = "MOONTIPBOT:MSG"
            if redis_conn and redis_conn.llen(key) > 0 :
                temp_msg_list = []
                for each in redis_conn.lrange(key, 0, -1):
                    temp_msg_list.append(tuple(json.loads(each)))
                try:
                    num_add = await store.sql_add_messages(temp_msg_list)
                except Exception as e:
                    traceback.print_exc(file=sys.stdout)
                    await logchanbot(traceback.format_exc())
                if num_add and num_add > 0:
                    redis_conn.delete(key)
                else:
                    redis_conn.delete(key)
                    print(f"MOONTIPBOT:MSG: Failed delete {key}")
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
        await asyncio.sleep(interval_msg_list)


# Let's run balance update by a separate process
async def update_balance():
    INTERVAL_EACH = 10
    while True:
        await asyncio.sleep(INTERVAL_EACH)
        start = time.time()
        try:
            await store.sql_check_minimum_deposit()
        except Exception as e:
            print(e)
        end = time.time()
        await asyncio.sleep(INTERVAL_EACH)


async def unlocked_move_pending():
    INTERVAL_EACH = 10
    while True:
        await asyncio.sleep(INTERVAL_EACH)
        start = time.time()
        try:
            await store.sql_check_pending_move_deposit()
        except Exception as e:
            print(e)
        end = time.time()
        await asyncio.sleep(INTERVAL_EACH)


async def notify_new_confirmed_spendable():
    INTERVAL_EACH = 10
    is_notify_failed = False
    while True:
        await asyncio.sleep(INTERVAL_EACH)
        start = time.time()
        try:
            notify_list = await store.sql_get_pending_notification_users()
            if notify_list and len(notify_list) > 0:
                for each_notify in notify_list:
                    member = bot.get_user(id=int(each_notify['user_id']))
                    if member:
                        msg = "You got a new deposit confirmed: ```" + "Amount: {}{}".format(each_notify['real_amount'], TOKEN_NAME) + "```"
                        try:
                            await member.send(msg)
                        except (discord.Forbidden, discord.errors.Forbidden) as e:
                            is_notify_failed = True
                        except Exception as e:
                            traceback.print_exc(file=sys.stdout)
                            await logchanbot(traceback.format_exc())
                        update_status = await store.sql_updating_pending_move_deposit(True, is_notify_failed, each_notify['txn'])
        except Exception as e:
            print(e)
        end = time.time()
        await asyncio.sleep(INTERVAL_EACH)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send('This command cannot be used in private messages.')
    elif isinstance(error, commands.DisabledCommand):
        await ctx.send('Sorry. This command is disabled and cannot be used.')
    elif isinstance(error, commands.MissingRequiredArgument):
        #command = ctx.message.content.split()[0].strip('.')
        #await ctx.send('Missing an argument: try `.help` or `.help ' + command + '`')
        pass
    elif isinstance(error, commands.CommandNotFound):
        pass


async def is_owner(ctx):
    return ctx.author.id == config.discord.ownerID


async def get_guild_prefix(ctx):
    if isinstance(ctx.channel, discord.DMChannel) == True: return "."
    serverinfo = await store.sql_info_by_server(str(ctx.guild.id))
    if serverinfo is None:
        return "."
    else:
        return serverinfo['prefix']


# function to return if input string is ascii
def is_ascii(s):
    return all(ord(c) < 128 for c in s)


def seconds_str(time: float):
    # day = time // (24 * 3600)
    # time = time % (24 * 3600)
    hour = time // 3600
    time %= 3600
    minutes = time // 60
    time %= 60
    seconds = time
    return "{:02d}:{:02d}:{:02d}".format(hour, minutes, seconds)


def num_format_coin(amount):
    return '{:.4f}'.format(amount)


@register.error
async def register_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Missing your wallet address. '
                       f'You need to have a supported coin **address** after `register` command. Example: {config.discord.prefixCmd}register coin_address')
    return


@withdraw.error
async def withdraw_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Missing amount. '
                       f'You need to tell me **AMOUNT**.\nExample: {config.discord.prefixCmd}withdraw **1,000**')
    return


@freetip.error
async def freetip_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Missing arguments. '
                       f'You need to tell me **amount** and **duration** in seconds (with s).\nExample: {config.discord.prefixCmd}freetip **10 300s** or {config.discord.prefixCmd}freetip **10 300s Hello World**')
    return


@gfreetip.error
async def gfreetip_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Missing arguments. '
                       f'You need to tell me **amount** and **duration** in seconds (with s) [With comments].\n'
                       f'Example: {config.discord.prefixCmd}gfreetip **10 300s** or {config.discord.prefixCmd}gfreetip **10 300s Hello World**\n')
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Looks like you don\'t have the permission.')
    return


@tip.error
async def tip_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.message.add_reaction(EMOJI_ERROR)
        await ctx.send(f'{EMOJI_RED_NO} {ctx.author.mention} Missing arguments. '
                       f'You need to tell me **amount** and who you want to tip to.\nExample: {config.discord.prefixCmd}tip **1,000** <@{bot.user.id}>')
    return


@gtip.error
async def gtip_error(ctx, error):
    # TODO: flexible prefix
    prefix = '.'
    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.send('This command is not available in DM.')
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(f'{ctx.author.mention} You do not have permission in this guild **{ctx.guild.name}** Please use normal {prefix}tip command instead.')
        return


@gfreetip.error
async def gfreetip_error(ctx, error):
    # TODO: flexible prefix
    prefix = '.'
    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.send('This command is not available in DM.')
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(f'{ctx.author.mention} You do not have permission in this guild **{ctx.guild.name}** Please use normal {prefix}freetip command instead.')
        return


def randomString(stringLength=8):
    letters = string.ascii_lowercase
    return ''.join(random.choice(letters) for i in range(stringLength))


def truncate(number, digits) -> float:
    stepper = pow(10.0, digits)
    return math.trunc(stepper * number) / stepper


@click.command()
def main():
    bot.loop.create_task(update_balance())
    bot.loop.create_task(unlocked_move_pending())
    bot.loop.create_task(notify_new_confirmed_spendable())
    bot.loop.create_task(store_message_list())
    bot.run(config.discord.token, reconnect=True)


if __name__ == '__main__':
    main()