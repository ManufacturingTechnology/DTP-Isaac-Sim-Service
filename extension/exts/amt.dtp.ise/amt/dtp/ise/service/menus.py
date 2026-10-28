from abc import abstractmethod
import asyncio
import os
import pathlib
from typing import List, Optional

import omni
import omni.ui
import omni.kit
import omni.kit.menu
import omni.kit.menu.utils

from isaacsim.gui.components.element_wrappers import (
    Button,
    CheckBox,
    CollapsableFrame,
    ColorPicker,
    DropDown,
    FloatField,
    IntField,
    StateButton,
    StringField,
    TextBlock,
    XYPlot,
)

from .core import Service
from ..ui.core import ExtensionMenu, ExtensionWindow
from ..ui.create import CreateMenu
from ..ui.debug import DebugWindow
from ..ui.entity import EntityEditorWindow
from ..ui.profiler import ProfilerWindow
from ..ui.model_editor import ModelEditorExampleWindow
from ..ui.video_capture import VideoCaptureWindow

class MenusService(Service):
    _singleton: Optional[Service] = None
    _singleton_intialized = False

    def __new__(cls, *args, **kwargs):
        if cls._singleton is None:
            cls._singleton = super().__new__(cls)
        return cls._singleton

    @property
    def create_menu(self) -> Optional[CreateMenu]:
        return self._create_menu
    
    def __init__(self):
        if self._singleton_intialized:
            return
        self._singleton_intialized = True

        self._create_menu: Optional[CreateMenu] = None
        self._amt_menu: Optional[AmtMenu] = None

    def _service_start(self):
        self._create_menu = CreateMenu(self._ext_id)
        self._create_menu.startup()
        self._amt_menu = AmtMenu(self._ext_id)
        self._amt_menu.startup()
    
    def _service_stop(self):
        if self._create_menu is not None:
            self._create_menu.shutdown()
            self._create_menu = None
        if self._amt_menu is not None:
            self._amt_menu.shutdown()
            self._amt_menu = None

        self._singleton = None
        self._singleton_intialized = False


class MyWindow(ExtensionWindow):

    def __init__(self, title, dock_position = None):
        super().__init__(title, dock_position)
        self._i = 0

    def construct(self) -> None:
        self._i += 1
        def _click():
            self.reconstruct()
        Button(
            "asdf", 
            f"{self._i}",
            on_click_fn=_click
            )

class AmtMenu(ExtensionMenu):

    def __init__(self, ext_id: str):

        #self._entity_editor = EntityEditorWindow()
        #self._debug_window = DebugWindow()
        #self._my_window = MyWindow("test", omni.ui.DockPosition.LEFT)

        self._windows: List[ExtensionWindow] = [
            EntityEditorWindow(),
            ProfilerWindow(),
            #EntityEditorWindow2(),
            DebugWindow(),
            ModelEditorExampleWindow(),
            VideoCaptureWindow(),
            #MyWindow("test", omni.ui.DockPosition.LEFT),
            # self._entity_editor,
            # self._debug_window,
            # self._my_window
        ]

        # submenu_items = [
        #     omni.kit.menu.utils.MenuItemDescription(
        #         name=self._entity_editor.title,
        #         onclick_fn=self._entity_editor.show
        #     ),
        #     omni.kit.menu.utils.MenuItemDescription(
        #         name=self._debug_window.title,
        #         onclick_fn=self._debug_window.show
        #     ),
        #     omni.kit.menu.utils.MenuItemDescription(
        #         name=self._my_window.title,
        #         onclick_fn=self._my_window.show
        #     ),
        # ]

        submenu_items: List[omni.kit.menu.utils.MenuItemDescription] = []
        for window in self._windows:
            submenu_items.append(
                omni.kit.menu.utils.MenuItemDescription(
                    name=window.title,
                    onclick_fn=window.show
                )
            )
        
        # extension_path = pathlib.Path(__file__).parent.parent.parent.parent.resolve()
        # glyph_path = os.path.join(extension_path, "data", "icon.svg")
        # items = [
        #     omni.kit.menu.utils.MenuItemDescription(
        #         name="AMT",
        #         sub_menu=submenu_items,
        #         glyph=glyph_path,
        #     )
        # ]

        super().__init__(ext_id, "AMT", submenu_items)

    def startup(self):
        super().startup()
        # Windows start hidden (ExtensionWindow creates them with visible=False); open them from the AMT menu.

    def shutdown(self):
        super().shutdown()
        #self._entity_editor.hide()
        for window in self._windows:
            window.hide()
