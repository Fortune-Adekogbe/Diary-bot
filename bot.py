# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

from datetime import datetime as dt
from botbuilder.ai.luis import LuisRecognizer, LuisApplication
from azure.cognitiveservices.language.luis.runtime.models import LuisResult
from botbuilder.core import ActivityHandler, TurnContext, UserState, \
    ConversationState, StoreItem, RecognizerResult, IntentScore, TopIntent
from typing import Dict
from enum import Enum
from botbuilder.schema import ChannelAccount
from .welcome_user_state import WelcomeUserState
from .conversation_data import ConversationData
from .user_profile import UserProfile
import time
from datetime import datetime
from botbuilder.azure import BlobStorage, BlobStorageSettings
from .config import DefaultConfig
import re


class Note(StoreItem):
    """
    This class stores each user entry
    """
    def __init__(self, name: str, contents: list, e_tag='*'):
        super(Note, self).__init__()
        self.name = name
        self.contents = contents
        self.e_tag = e_tag


class Intent(Enum):
    """
    These are the different actions a user may wish to carry out.
    """
    DELETE = "Delete"
    GREET = "Greet"
    MODIFY = "Modify"
    NONE = "None"
    REPLACER = "replacer"
    THANKS = "Thanks"
    VIEW = "View"


def top_intent(intents: Dict[Intent, dict]) -> TopIntent:
    """
    This function determines the top intention of a user.
    """
    max_intent = Intent.NONE_INTENT
    max_value = 0.0

    for intent, value in intents:
        intent_score = IntentScore(value)
        if intent_score.score > max_value:
            max_intent, max_value = intent, intent_score.score

    return TopIntent(max_intent, max_value)


class MyBot(ActivityHandler):
    # See https://aka.ms/about-bot-activity-message to learn more about the message and other activity types.
    def __init__(self, conversation_state: ConversationState, user_state: UserState, configuration: DefaultConfig):
        if conversation_state is None:
            raise TypeError(
                "[MyBot]: Missing parameter. conversation_state is required but None was given"
            )
        if user_state is None:
            raise TypeError("[MyBot]: Missing parameter. user_state is required but None was given"
                            )
        self.conversation_state = conversation_state
        self.user_state = user_state
        self.user_state_accessor = self.user_state.create_property("WelcomeUserState")
        self.conversation_data_accessor = self.conversation_state.create_property(
            "ConversationData"
        )
        self.user_profile_accessor = self.user_state.create_property("UserProfile")
        self.WELCOME_MESSAGE = """Welcome to your favorite Dairy bot"""

        blob_settings = BlobStorageSettings(
            container_name='EnterContainerName',
            connection_string="EnterConnectionString"
        )
        self.storage = BlobStorage(blob_settings)
        self._recognizer = None

        luis_is_configured = (
                configuration.LUIS_APP_ID
                and configuration.LUIS_API_KEY
                and configuration.LUIS_API_HOST_NAME
        )
        if luis_is_configured:
            # Set the recognizer options depending on which endpoint version you want to use e.g v2 or v3.
            # More details can be found in https://docs.microsoft.com/azure/cognitive-services/luis/luis-migration-api-v3
            luis_application = LuisApplication(
                configuration.LUIS_APP_ID,
                configuration.LUIS_API_KEY,
                "https://" + configuration.LUIS_API_HOST_NAME,
            )

            self._recognizer = LuisRecognizer(luis_application)

    @property
    def is_configured(self) -> bool:
        return self._recognizer is not None

    async def recognize(self, turn_context: TurnContext) -> RecognizerResult:
        return await self._recognizer.recognize(turn_context)

    async def on_message_activity(self, turn_context: TurnContext):
        # Get the state properties from the turn context.
        user_profile = await self.user_profile_accessor.get(turn_context, UserProfile)
        conversation_data = await self.conversation_data_accessor.get(
            turn_context, ConversationData
        )
        welcome_user_state = await self.user_state_accessor.get(
            turn_context, WelcomeUserState
        )

        utterance = turn_context.activity.text
        utterance_timestamp = datetime_from_utc_to_local(
            turn_context.activity.timestamp
        )
        utterance_time = utterance_timestamp.lower()

        try:
            store_items = await self.storage.read([utterance_time])
            note = store_items[utterance_time]
            note.contents.append(utterance)
            try:
                # Save the user message to your Storage.
                changes = {utterance_time: note}
                await self.storage.write(changes)
            except Exception as exception:
                # Inform the user an error occurred.
                await turn_context.send_activity("Sorry, something went wrong storing your message!")
        except:
            try:
                # Save the user message to your Storage.
                changes = {
                    utterance_time: Note(name="Diary-entry", contents=[utterance])
                }
                await self.storage.write(changes)
            except Exception as exception:
                # Inform the user an error occurred.
                await turn_context.send_activity("Sorry, something went wrong storing your message!")

        if not welcome_user_state.did_welcome_user:
            welcome_user_state.did_welcome_user = True

            name = turn_context.activity.from_property.name
            await turn_context.send_activity(
                f"{self.WELCOME_MESSAGE}"
            )
        else:

            talk = turn_context.activity.text
            text = talk.lower()

            dtime = None
            intent = None

            try:
                recognizer_result = await self._recognizer.recognize(turn_context)
                intent = (
                    sorted(
                        recognizer_result.intents,
                        key=recognizer_result.intents.get,
                        reverse=True,
                    )[:1][0]
                    if recognizer_result.intents
                    else None
                )

                date_entities = recognizer_result.entities.get('datetime',[])
                if date_entities:
                    qq = date_entities[0]["timex"]
                    if qq:
                        date_entities = qq[0]
                s = date_entities.replace('T','-').split('-')
                s = [i.lstrip('0') for i in s]
                Dtime = datetime_from_utc_to_local(dt(int(s[0]), int(s[1]), int(s[2]), int(s[3].split(':')[0])))

                await turn_context.send_activity(Dtime)

                dtime = Dtime.lower()

            except Exception as exception:
                print(exception)

            tintent = intent

            if tintent == Intent.VIEW.value:
                store_items = await self.storage.read([dtime])
                note = store_items[dtime]
                string = '\n\n'.join(note.contents)
                await turn_context.send_activity(f"On {dtime}, you said:\n\n{string}")

            elif tintent == Intent.DELETE.value:
                await self.storage.delete([dtime])
                await turn_context.send_activity(f"You have successfully deleted the entry on {dtime}")
            elif tintent in ["Replace", "Modify"]:
                store_items = await self.storage.read([dtime])
                note = store_items[dtime]
                new_item = talk.split('this:')[-1]
                if tintent == Intent.REPLACER.value:
                    note.contents = [new_item]
                elif tintent == Intent.MODIFY.value:
                    note.contents.append(new_item)
                try:
                    # Save the user message to your Storage.
                    changes = {dtime: note}
                    await self.storage.write(changes)
                except Exception as exception:
                    # Inform the user an error occurred.
                    await turn_context.send_activity("Sorry, something went wrong storing your message!")

        if user_profile.name is None:
            # First time around this is undefined, so we will prompt user for name.
            if conversation_data.prompted_for_user_name:
                # Set the name to what the user provided.
                user_profile.name = turn_context.activity.text

                # Acknowledge that we got their name.
                await turn_context.send_activity(
                    f"Thanks {user_profile.name}. How was your day?"
                )

                await turn_context.send_activity(
                    f"Please tell me about it..."
                )

                # Reset the flag to allow the bot to go though the cycle again.
                conversation_data.prompted_for_user_name = False
            else:
                # Prompt the user for their name.
                await turn_context.send_activity("What is your name?")

                # Set the flag to true, so we don't prompt in the next turn.
                conversation_data.prompted_for_user_name = True
        else:
            # Add message details to the conversation data.
            conversation_data.timestamp = datetime_from_utc_to_local(
                turn_context.activity.timestamp
            )
            conversation_data.channel_id = turn_context.activity.channel_id


    async def on_turn(self, turn_context: TurnContext):
        await super().on_turn(turn_context)

        # save changes to WelcomeUserState after each turn
        await self.conversation_state.save_changes(turn_context)
        await self.user_state.save_changes(turn_context)

    async def on_members_added_activity(
            self,
            members_added: [ChannelAccount],
            turn_context: TurnContext
    ):
        """
        Greetings new user!
        """


def datetime_from_utc_to_local(utc_datetime):
    now_timestamp = time.time()
    offset = datetime.fromtimestamp(now_timestamp) - datetime.utcfromtimestamp(
        now_timestamp
    )
    result = utc_datetime + offset
    return result.strftime("%I:00 %p, %A, %B %d, %Y")
