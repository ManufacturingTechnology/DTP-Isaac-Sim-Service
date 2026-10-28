from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Type, Optional
import json
import time
import uuid
from pydantic import BaseModel, Field, field_validator

# import omni
# import omni.usd
# import pxr
# import pxr.Usd
# from isaacsim.gui.components.element_wrappers import (
#     UIWidgetWrapper,
#     Button,
#     CheckBox,
#     Frame,
#     CollapsableFrame,
#     ColorPicker,
#     DropDown,
#     FloatField,
#     IntField,
#     StateButton,
#     StringField,
#     TextBlock,
#     XYPlot,
# )

import paho
import paho.mqtt
import paho.mqtt.client


#from ..entity.core import EntityEditorPaneBase, CustomDataEntry
from ..lib.logging import _logger


@dataclass(frozen=True)
class Point:
    sequence: int
    timestamp: datetime
    value: Any


class Channel:

    @property
    def id(self) -> type:
        return self._id

    @property
    def data_type(self) -> type:
        return self._data_type

    def __init__(self, id: str, data_type: type):
        if not isinstance(id, str):
            raise TypeError("id must be a str")
        self._id = id

        if not isinstance(data_type, type):
            raise TypeError("data_type must be a type")
        self._data_type = data_type

        self._points = []

        self._current_sequence = 0

    def clear(self) -> None:
        self._points.clear()

    def is_empty(self) -> bool:
        return len(self._points) == 0
    
    def peek(self) -> Point:
        return self._points[0]
    
    def pop(self) -> Point:
        return self._points.pop(0)
    
    def push(self, value: Any) -> None:
        self._current_sequence += 1
        point = Point(self._current_sequence, datetime.now(timezone.utc), value)
        self._push(point)
    
    def _push(self, point: Point) -> None:
        if not isinstance(point, Point):
            raise TypeError("point must be a Point")
        if not isinstance(point.value, self.data_type):
            raise ValueError("type(point.value) must match Channel.data_type")
        self._points.append(point)


class ChannelCollection:

    def __init__(self):
        self._channels: Dict[str, Channel] = {}

    def clear(self) -> None:
        self._channels.clear()

    def add(self, channel: Channel) -> None:
        if not isinstance(channel, Channel):
            raise TypeError("channel must be a Channel")
        if channel.id in self._channels:
            raise Exception(f"Channel with id '{channel.id}' already exists")
        self._channels[channel.id] = channel

    def exists(self, id: str) -> bool:
        if not isinstance(id, str):
            raise TypeError("id must be a str")
        return id in self._channels

    def get(self, id: str) -> Channel:
        if not isinstance(id, str):
            raise TypeError("id must be a str")
        if self._channels.id not in self._channels:
            raise Exception(f"Channel with id '{id}' does not exist")
        return self._channels[id]
    
    def remove(self, channel: Channel) -> None:
        if not isinstance(channel, Channel):
            raise TypeError("channel must be a Channel")
        if channel.id not in self._channels:
            raise Exception(f"Channel with id '{channel.id}' does not exist")
        self._channels.remove(channel.id)

    def __iter__(self) -> List[Channel]:
        return iter(list(self._channels.values()))


class MqttChannel(Channel):

    def __init__(self, data_type: type, topic: str):
        super().__init__(topic, data_type)

        if not isinstance(topic, str):
            raise TypeError("topic must be a str")
        self._topic = topic

    @property
    def topic(self) -> str:
        return self._topic


class MqttGateway:

    def __init__(self, input_channels: ChannelCollection, output_channels: ChannelCollection):
        if not isinstance(input_channels, ChannelCollection):
            raise TypeError("input_channels must be a ChannelCollection")
        if not all(isinstance(channel, MqttChannel) for channel in input_channels):
            raise ValueError("input_channels must contain only MqttChannels")
        self._input_channels = input_channels

        if not isinstance(output_channels, ChannelCollection):
            raise TypeError("output_channels must be a ChannelCollection")
        if not all(isinstance(channel, MqttChannel) for channel in output_channels):
            raise ValueError("output_channels must contain only MqttChannels")
        self._output_channels = output_channels
        
        self._client = paho.mqtt.client.Client(client_id=f"amt_dtp_{uuid.uuid4()}")
        self._client.on_message = self._on_message
        self._client.on_connect = self._on_connect
        self._root_topic: Optional[str] = None

    @property
    def is_connected(self) -> bool:
        return self._client.is_connected

    def start(self, host: str, port: int, root_topic: str):
        if not isinstance(host, str):
            raise TypeError("host must be a str")
        # Sanitize localhost -> 127.0.0.1: on Windows 'localhost' resolves to IPv6 (::1)
        # first and stalls ~2s on the fallback to IPv4 before connecting to the broker.
        host = host.replace("localhost", "127.0.0.1")
        if not isinstance(port, int):
            raise TypeError("port must be an int")
        if not isinstance(root_topic, str):
            raise TypeError("root_topic must be a str")
        if root_topic == "":
            raise ValueError("root_topic must not be empty")
        self._root_topic = root_topic

        self._client.loop_start()
        self._client.connect(host, port)
        pass

    def stop(self):
        self._client.loop_stop()
        self._client.disconnect()
        self._root_topic = None
        _logger.trace("Disconnected from MQTT Broker")

    def _publish_channel_collection(self, channel_collection: ChannelCollection) -> None:
        for c in channel_collection:
            channel: MqttChannel = c
            while not channel.is_empty():
                topic = f"{self._root_topic}/{channel.topic}"
                message = json.dumps(channel.pop().value)
                result: paho.mqtt.client.MQTTMessageInfo = self._client.publish(topic, message)
                rc: paho.mqtt.client.MQTTErrorCode = result.rc
                status = result[0]
                if status != 0:
                    _logger.warning(f"MQTT Gateway failed to send message ({rc.name}) '{message}' to topic '{topic}'")

    def publish_synchronous(self):
        if all(c.is_empty() for c in self._output_channels):
            return
        if not self._client.is_connected():
            _logger.trace("MQTT Gateway is not connected")
            return
        self._publish_channel_collection(self._output_channels)
                
    def _on_message(self, client: paho.mqtt.client.Client, userdata, msg: paho.mqtt.client.MQTTMessage):
        try:
            if not msg.topic.startswith(self._root_topic):
                return
            topic = msg.topic.removeprefix(f"{self._root_topic}/").removeprefix("/")
            payload = json.loads(msg.payload)
            for c in self._input_channels:
                channel: MqttChannel = c
                if channel.topic == topic:
                    value = channel.data_type(payload)
                    channel.push(value)
        except Exception as ex:
            _logger.error("Error receiving MQTT message", exception=ex)
                
    def _on_connect(self, client: paho.mqtt.client.Client, userdata, flags: dict, rc: int):
        try:
            if rc == 0:
                _logger.trace("Connected to MQTT Broker")
                qos = 0
                self._client.subscribe(f"{self._root_topic}/#", qos)
            else:
                _logger.warning(f"Failed to connect, return code {rc}")
        except Exception as ex:
            _logger.error("Error on connecting to MQTT Broker", exception=ex)


class MqttConfig(BaseModel):
    host: str = Field(
        title="Broker Host",
        description="The IP address or hostname of the MQTT Broker."
    )
    @field_validator("host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("host must not be empty")
        # Sanitize localhost -> 127.0.0.1 (see MqttGateway.start): avoids the ~2s Windows
        # IPv6 fallback per connection when a config supplies 'localhost'.
        return v.replace("localhost", "127.0.0.1")
    
    port: int = Field(
        1883,
        title="Broker Port",
        description="The port of the MQTT Broker.",
        ge=0,
    )
    topic: str = Field(
        title="Topic",
        description="The root topic where values should be published.",
    )
    @field_validator("topic")
    @classmethod
    def validate_topic(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("topic must not be empty")
        return v

# class MqttDataEntry(BehaviorConfigDataEntry):
    
#     @property
#     def tags(self) -> List[str]:
#         return self._tags

#     def __init__(self, enabled: bool, broker_host: str, broker_port: int, topic: str):
#         if not isinstance(enabled, bool):
#             raise TypeError("enabled must be a bool")
#         self.enabled = enabled

#         if not isinstance(broker_host, str):
#             raise TypeError("broker_host must be a str")
#         self.broker_host = broker_host

#         if not isinstance(broker_port, int):
#             raise TypeError("broker_port must be a str")
#         self.broker_port = broker_port

#         if not isinstance(topic, str):
#             raise TypeError("topic must be a str")
#         self.topic = topic

#     def __to_dict__(self) -> dict:
#         return {
#             "enabled": self.enabled,
#             "broker_host": self.broker_host,
#             "broker_port": self.broker_port,
#             "topic": self.topic
#         }

#     @classmethod
#     def __from_dict__(self, data: dict) -> CustomDataEntry:
#         return MqttDataEntry(
#             data["enabled"] if "enabled" in data else True,
#             data["broker_host"] if "broker_host" in data else "",
#             data["broker_port"] if "broker_port" in data else [],
#             data["topic"] if "topic" in data else [],
#         )


# class MqttEditorPane(EntityEditorPaneBase):

#     def get_title(self) -> str:
#             return "MQTT"

#     def get_entry_type(self) -> Type[CustomDataEntry]:
#         return MqttDataEntry

#     def construct_fields(self) -> None:
#         def _save(*args):
#             if self.block_save:
#                 return

#             if self._editor is None:
#                 return
            
#             broker_host = self._broker_host_field.get_value()
#             broker_port = self._broker_port_field.get_value()
#             topic = self._topic_field.get_value()
#             if broker_host == "" or broker_port == "" or topic == "":
#                 self._set_status(False)
#                 return
#             try:
#                 broker_port = int(broker_port)
#             except Exception as ex:
#                 self._set_status(False)
#                 return

#             self._editor.write(MqttDataEntry(
#                 self._enabled_field.get_value(),
#                 broker_host,
#                 broker_port,
#                 topic,
#             ))
#             self._set_status(True)

#         self._enabled_field = CheckBox(
#             label="Enabled",
#             default_value=True,
#             on_click_fn=_save
#         )

#         self._broker_host_field = StringField(
#             label="Broker Host",
#             on_value_changed_fn=_save,
#         )

#         self._broker_port_field = StringField(
#             label="Broker Port",
#             on_value_changed_fn=_save,
#         )

#         self._topic_field = StringField(
#             label="Topic",
#             on_value_changed_fn=_save,
#         )

#     def populate_fields(self, metadata: CustomDataEntry) -> None:
#         data: MqttDataEntry = metadata
#         self._enabled_field.set_value(data.enabled)
#         self._broker_host_field.set_value(data.broker_host)
#         self._broker_port_field.set_value(data.broker_port)
#         self._topic_field.set_value(data.topic)
