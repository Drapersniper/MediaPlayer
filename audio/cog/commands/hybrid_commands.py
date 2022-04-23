from abc import ABC
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.app_commands import Range
from red_commons.logging import getLogger
from redbot.core import commands
from redbot.core.i18n import Translator

from pylav import Track, converters
from pylav.utils import AsyncIter, PyLavContext

from audio.cog import MY_GUILD, MPMixin
from audio.cog.menus.menus import QueueMenu
from audio.cog.menus.sources import QueueSource
from audio.cog.utils import rgetattr
from audio.cog.utils.decorators import requires_player

LOGGER = getLogger("red.3pt.mp.commands.hybrids")
_ = Translator("MediaPlayer", Path(__file__))


class HybridCommands(MPMixin, ABC):
    @commands.hybrid_command(name="play", description="Plays a specified query.", aliases=["p"])
    @app_commands.guilds(MY_GUILD)
    @commands.guild_only()
    async def command_play(self, context: PyLavContext, *, query: converters.QueryConverter):
        if isinstance(context, discord.Interaction):
            context = await self.bot.get_context(context)
        if context.interaction and not context.interaction.response.is_done():
            await context.defer(ephemeral=True)

        player = context.player
        if player is None:
            channel = rgetattr(context, "author.voice.channel", None)
            if not channel:
                await context.send(
                    embed=await context.lavalink.construct_embed(
                        description=_("You must be in a voice channel to allow me to connect.")
                    ),
                    ephemeral=True,
                )
                return
            player = await context.connect_player(channel=channel, self_deaf=True)
        is_partial = query.is_search
        response = {}
        if not is_partial:
            response: dict = await context.lavalink.get_tracks(query)
            if not response.get("tracks"):
                await context.send(
                    embed=await context.lavalink.construct_embed(
                        description=_("No results found for {query}").format(query=await query.query_to_string()),
                    ),
                    ephemeral=True,
                )
                return
        if is_partial:
            track = Track(node=player.node, data=None, query=query, requester=context.author.id)
            await player.add(requester=context.author.id, track=track)
            await context.send(
                embed=await context.lavalink.construct_embed(
                    description=_("{track} enqueued").format(track=await track.get_track_display_name(with_url=True)),
                ),
                ephemeral=True,
            )
        elif query.is_single:
            track = Track(
                node=player.node, data=response["tracks"][0], query=query.with_index(0), requester=context.author.id
            )
            await player.add(requester=context.author.id, track=track)
            await context.send(
                embed=await context.lavalink.construct_embed(
                    description=_("{track} enqueued").format(track=await track.get_track_display_name(with_url=True)),
                ),
                ephemeral=True,
            )
        else:
            tracks = response["tracks"]
            track_count = len(tracks)
            await player.bulk_add(
                requester=context.author.id,
                tracks_and_queries=[
                    Track(node=player.node, data=track["track"], query=query.with_index(i), requester=context.author.id)
                    async for i, track in AsyncIter(tracks).enumerate()
                ],
            )
            bundle_name = response.get("playlistInfo", {}).get("name")
            if bundle_name and not (query.is_search or query.is_local):
                bundle_prefix = _("Album") if query.is_album else _("Playlist") if query.is_playlist else ""
                if bundle_name:
                    bundle_name = discord.utils.escape_markdown(bundle_name)
                    bundle_name = f"[{bundle_name}]"
                bundle_name += f"({query.query_identifier})"
                playlist_name = f"\n\n**{bundle_prefix}:  {bundle_name}**"
            elif not bundle_name and query.is_album and query.is_local:
                bundle_prefix = _("Album")
                folder_name = await query.folder()
                if folder_name:
                    bundle_name = discord.utils.escape_markdown(await query.query_to_string())
                    playlist_name = f"\n\n**{bundle_prefix}:  {bundle_name}**"
                else:
                    playlist_name = ""
            else:
                playlist_name = ""

            await context.send(
                embed=await context.lavalink.construct_embed(
                    description=_("{track_count} tracks enqueued.{playlist_name}").format(
                        track_count=track_count, playlist_name=playlist_name
                    ),
                ),
                ephemeral=True,
            )

        if not player.is_playing:
            await player.play(requester=context.author)

    @commands.hybrid_command(
        name="connect", description="Connects the Player to the specified channel or your current channel."
    )
    @app_commands.guilds(MY_GUILD)
    @commands.guild_only()
    async def command_connect(self, context: PyLavContext, *, channel: Optional[discord.VoiceChannel] = None):
        if isinstance(context, discord.Interaction):
            context = await self.bot.get_context(context)
        if context.interaction and not context.interaction.response.is_done():
            await context.defer(ephemeral=True)
        channel = channel or rgetattr(context, "author.voice.channel", None)
        if not channel:
            await context.send(
                embed=await context.lavalink.construct_embed(
                    description=_(
                        "You need to be in a voice channel if you don't specify which channel I need to connect to."
                    ),
                ),
                ephemeral=True,
            )
            return
        if not ((permission := channel.permissions_for(context.me)) and permission.connect and permission.speak):
            if permission.connect:
                description = _("I don't have permission to connect to that channel.").format(channel=channel.mention)
            else:
                description = _("I don't have permission to speak in {channel}.").format(channel=channel.mention)
            await context.send(
                embed=await context.lavalink.construct_embed(
                    description=_("I don't have permission to connect to {channel}.").format(channel=description),
                ),
                ephemeral=True,
            )
            return
        if (player := context.lavalink.get_player(context.guild)) is None:
            await context.lavalink.connect_player(context.author, channel=channel, self_deaf=True)
        else:
            await player.move_to(context.author, channel, self_deaf=True)

        await context.send(
            embed=await context.lavalink.construct_embed(
                description=_("Connected to {channel}").format(channel=channel.mention),
            ),
            ephemeral=True,
        )

    @commands.hybrid_command(name="np", description="Shows the track currently being played.", aliases=["now"])
    @app_commands.guilds(MY_GUILD)
    @commands.guild_only()
    @requires_player()
    async def command_now(self, context: PyLavContext):
        if isinstance(context, discord.Interaction):
            context = await self.bot.get_context(context)
        if context.interaction and not context.interaction.response.is_done():
            await context.defer(ephemeral=True)
        player = context.lavalink.get_player(context.guild)
        if not player:
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("No player detected.")),
                ephemeral=True,
            )
            return
        if not player.current:
            await context.send(
                embed=await context.lavalink.construct_embed(
                    description=_("Player is not currently playing anything.")
                ),
                ephemeral=True,
            )
            return
        current_embed = await player.get_currently_playing_message(messageable=context)
        await context.send(embed=current_embed, ephemeral=True)

    @commands.hybrid_command(name="skip", description="Skips or votes to skip the current track.")
    @app_commands.guilds(MY_GUILD)
    @commands.guild_only()
    @requires_player()
    async def command_skip(self, context: PyLavContext):
        if isinstance(context, discord.Interaction):
            context = await self.bot.get_context(context)
        if context.interaction and not context.interaction.response.is_done():
            await context.defer(ephemeral=True)
        player = context.lavalink.get_player(context.guild)
        if not player:
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("No player detected.")),
                ephemeral=True,
            )
            return
        if not player.current:
            await context.send(
                embed=await context.lavalink.construct_embed(
                    description=_("Player is not currently playing anything.")
                ),
                ephemeral=True,
            )
            return
        await context.send(
            embed=await context.lavalink.construct_embed(
                description=_("Skipped - {track}").format(
                    track=await player.current.get_track_display_name(with_url=True)
                ),
            ),
            ephemeral=True,
        )
        await player.skip(requester=context.author)

    @commands.hybrid_command(name="stop", description="Stops the player and remove all tracks from the queue.")
    @app_commands.guilds(MY_GUILD)
    @commands.is_owner()
    @commands.guild_only()
    @requires_player()
    async def command_stop(self, context: PyLavContext):
        if isinstance(context, discord.Interaction):
            context = await self.bot.get_context(context)
        if context.interaction and not context.interaction.response.is_done():
            await context.defer(ephemeral=True)
        player = context.lavalink.get_player(context.guild)
        if not player:
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("No player detected.")),
                ephemeral=True,
            )
            return
        if not player.current:
            await context.send(
                embed=await context.lavalink.construct_embed(
                    description=_("Player is not currently playing anything.")
                ),
                ephemeral=True,
            )
            return
        await player.stop(context.author)
        await context.send(
            embed=await context.lavalink.construct_embed(description=_("Player stopped")),
            ephemeral=True,
        )

    @commands.hybrid_command(
        name="dc", description="Disconnects the player from the voice channel.", aliases=["disconnect"]
    )
    @app_commands.guilds(MY_GUILD)
    @commands.is_owner()
    @requires_player()
    async def command_disconnect(self, context: PyLavContext):
        if isinstance(context, discord.Interaction):
            context = await self.bot.get_context(context)
        if context.interaction and not context.interaction.response.is_done():
            await context.defer(ephemeral=True)
        LOGGER.info("Disconnecting from voice channel - %s", context.author)
        player = context.lavalink.get_player(context.guild)
        if not player:
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("No player detected.")),
                ephemeral=True,
            )
            return
        await player.disconnect(requester=context.author)
        await context.send(
            embed=await context.lavalink.construct_embed(description=_("Disconnected from voice channel")),
            ephemeral=True,
        )

    @commands.hybrid_command(name="queue", description="Shows the current queue for the player.", aliases=["q"])
    @app_commands.guilds(MY_GUILD)
    @commands.guild_only()
    @requires_player()
    async def command_queue(self, context: PyLavContext):
        if isinstance(context, discord.Interaction):
            context = await self.bot.get_context(context)
        if context.interaction and not context.interaction.response.is_done():
            await context.defer(ephemeral=True)
        player = context.lavalink.get_player(context.guild)
        if not player:
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("No player detected.")),
                ephemeral=True,
            )
            return
        if player.queue.empty():
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("There is nothing in the queue.")),
                ephemeral=True,
            )
            return
        await QueueMenu(
            cog=self,  # type: ignore
            bot=self.bot,
            source=QueueSource(guild_id=context.guild.id, cog=self),  # type: ignore
            original_author=context.author if not context.interaction else context.interaction.user,
        ).start(ctx=context)

    @commands.hybrid_command(name="shuffle", description="Shuffles the player's queue.")
    @app_commands.guilds(MY_GUILD)
    @commands.guild_only()
    @requires_player()
    async def command_shuffle(self, context: PyLavContext):
        if isinstance(context, discord.Interaction):
            context = await self.bot.get_context(context)
        if context.interaction and not context.interaction.response.is_done():
            await context.defer(ephemeral=True)
        player = context.lavalink.get_player(context.guild)
        if not player:
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("No player detected.")),
                ephemeral=True,
            )
            return
        if player.queue.empty():
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("There is nothing in the queue.")),
                ephemeral=True,
            )
            return
        await player.shuffle_queue(context.author)
        await context.send(
            embed=await context.lavalink.construct_embed(
                description=_("{queue_size} tracks shuffled").format(queue_size=player.queue.size()),
            ),
            ephemeral=True,
        )

    @commands.hybrid_command(name="repeat", description="Set whether to repeat current song or queue.")
    @app_commands.guilds(MY_GUILD)
    @commands.guild_only()
    @requires_player()
    async def command_repeat(self, context: PyLavContext, queue: Optional[bool] = None):
        if isinstance(context, discord.Interaction):
            context = await self.bot.get_context(context)
        if context.interaction and not context.interaction.response.is_done():
            await context.defer(ephemeral=True)
        player = context.lavalink.get_player(context.guild)
        if not player:
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("No player detected.")),
                ephemeral=True,
            )
            return
        if queue:
            await player.set_repeat("queue", True, context.author)
            msg = _("Repeating the queue")
        else:
            if player.repeat_queue or player.repeat_current:
                await player.set_repeat("disable", False, context.author)
                msg = _("Repeating disabled")
            else:
                await player.set_repeat("current", True, context.author)
                msg = _("Repeating {track}").format(track=await player.current.get_track_display_name(with_url=True))
        await context.send(
            embed=await context.lavalink.construct_embed(description=msg, messageable=context), ephemeral=True
        )

    @commands.hybrid_command(name="pause", description="Pause the player.")
    @app_commands.guilds(MY_GUILD)
    @commands.guild_only()
    @requires_player()
    async def command_pause(self, context: PyLavContext):
        if isinstance(context, discord.Interaction):
            context = await self.bot.get_context(context)
        if context.interaction and not context.interaction.response.is_done():
            await context.defer(ephemeral=True)
        player = context.lavalink.get_player(context.guild)
        if not player:
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("No player detected.")),
                ephemeral=True,
            )
            return
        if player.paused:
            if context.interaction:
                description = _("Player already paused did you mean to run `/resume`.")
            else:
                description = _("Player already paused did you mean to run `{prefix}{command}`.").format(
                    prefix=context.prefix, command=self.command_resume.qualified_name
                )
            await context.send(
                embed=await context.lavalink.construct_embed(description=description),
                ephemeral=True,
            )
            return

        await player.set_pause(True, requester=context.author)
        await context.send(
            embed=await context.lavalink.construct_embed(description=_("Player paused.")),
            ephemeral=True,
        )

    @commands.hybrid_command(name="resume", description="Resume the player.")
    @app_commands.guilds(MY_GUILD)
    @commands.guild_only()
    @requires_player()
    async def command_resume(self, context: PyLavContext):
        if isinstance(context, discord.Interaction):
            context = await self.bot.get_context(context)
        if context.interaction and not context.interaction.response.is_done():
            await context.defer(ephemeral=True)
        player = context.lavalink.get_player(context.guild)
        if not player:
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("No player detected.")),
                ephemeral=True,
            )
            return
        if not player.paused:
            if context.interaction:
                description = _("Player already paused did you mean to run `/pause`.")
            else:
                description = _("Player already paused did you mean to run `{prefix}{command}`.").format(
                    prefix=context.prefix, command=self.command_pause.qualified_name
                )
            await context.send(
                embed=await context.lavalink.construct_embed(description=description),
                ephemeral=True,
            )
            return

        await player.set_pause(False, context.author)
        await context.send(
            embed=await context.lavalink.construct_embed(description=_("Player resumed.")),
            ephemeral=True,
        )

    @commands.hybrid_command(name="volume", description="Set the player volume.")
    @app_commands.guilds(MY_GUILD)
    @commands.guild_only()
    @requires_player()
    async def command_volume(self, context: PyLavContext, volume: Range[int, 0, 1000] = 100):
        if isinstance(context, discord.Interaction):
            context = await self.bot.get_context(context)
        if context.interaction and not context.interaction.response.is_done():
            await context.defer(ephemeral=True)
        player = context.lavalink.get_player(context.guild)
        if not player:
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("No player detected.")),
                ephemeral=True,
            )
            return
        await player.set_volume(volume, requester=context.author)
        await context.send(
            embed=await context.lavalink.construct_embed(
                description=_("Player volume set to {volume}%.").format(volume=volume)
            ),
            ephemeral=True,
        )

    @commands.hybrid_command(name="prev", description="Play the previous tracks.", aliases=["previous"])
    @app_commands.guilds(MY_GUILD)
    @commands.guild_only()
    @requires_player()
    async def command_previous(self, context: PyLavContext):
        if isinstance(context, discord.Interaction):
            context = await self.bot.get_context(context)
        if context.interaction and not context.interaction.response.is_done():
            await context.defer(ephemeral=True)

        player = context.lavalink.get_player(context.guild)
        if not player:
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("No player detected.")),
                ephemeral=True,
            )
            return

        if player.history.empty():
            await context.send(
                embed=await context.lavalink.construct_embed(description=_("No previous in player history.")),
                ephemeral=True,
            )
            return
        await player.previous(requester=context.author)
        await context.send(
            embed=await context.lavalink.construct_embed(
                description=_("Playing previous track: {track}.").format(
                    track=await player.current.get_track_display_name(with_url=True)
                ),
            ),
            ephemeral=True,
        )
