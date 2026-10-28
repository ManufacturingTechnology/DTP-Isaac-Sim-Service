from abc import ABC, abstractmethod
import asyncio
import os
import pathlib
import sys
import traceback
from typing import Callable, List, Optional

import carb
import isaacsim.gui.components.element_wrappers
import isaacsim.gui.components.menu
import omni.kit
import omni.kit.actions
import omni.kit.actions.core
import omni.kit.app
import omni.kit.menu.utils
import omni.ui

from ..lib.logging import _logger


class Renderable(ABC):

    @property
    @abstractmethod
    def title(self) -> str:
        raise NotImplementedError()

    @abstractmethod
    def render(self) -> None:
        raise NotImplementedError()
    

class ExtensionMenu:

    @property
    def ext_id(self) -> str:
        return self._ext_id

    @property
    def items(self) -> List[omni.kit.menu.utils.MenuItemDescription]:
        return list(self._items)

    @property
    def title(self) -> str:
        return self._title

    def __init__(self, ext_id: str, title: str, items: List[omni.kit.menu.utils.MenuItemDescription]):
        if not isinstance(ext_id, str):
            raise TypeError("ext_id must be a str")
        self._ext_id = ext_id
        
        if not isinstance(title, str):
            raise TypeError("title must be a str")
        self._title = title

        if not isinstance(items, list) or not all(isinstance(i, omni.kit.menu.utils.MenuItemDescription) for i in items):
            raise TypeError("items must be a List[omni.kit.menu.utils.MenuItemDescription]")
        if not all(isinstance(i, omni.kit.menu.utils.MenuItemDescription) for i in items):
            raise TypeError("items must be a List[omni.kit.menu.utils.MenuItemDescription]")
        self._items = items
    
    def _register_recursive(self, prefix: str, item: omni.kit.menu.utils.MenuItemDescription) -> None:
        if not isinstance(prefix, str):
            raise TypeError("prefix must be a str")
        if not isinstance(item, omni.kit.menu.utils.MenuItemDescription):
            raise TypeError("item must be an instance of MenuItemDescription")
        
        if item.onclick_fn is not None:
            item_description = f"{prefix} {item.name}"
            item_id = item_description.lower().replace(" ", "_")
            action_registry = omni.kit.actions.core.get_action_registry()
            action_registry.register_action(
                self._ext_id,
                item_id,
                item.onclick_fn,
                description=f"{item_description}",
            )
        if item.sub_menu is not None:
            for sub_item in item.sub_menu:
                self._register_recursive(f"{prefix} {item.name}", sub_item)

    def _unregister_recursive(self, prefix: str, item: omni.kit.menu.utils.MenuItemDescription) -> None:
        if not isinstance(prefix, str):
            raise TypeError("prefix must be a str")
        if not isinstance(item, omni.kit.menu.utils.MenuItemDescription):
            raise TypeError("item must be an instance of MenuItemDescription")
        
        if item.onclick_fn is not None:
            item_description = f"{prefix} {item.name}"
            item_id = item_description.lower().replace(" ", "_")
            action_registry = omni.kit.actions.core.get_action_registry()
            action_registry.deregister_action(self._ext_id, item_id)

        if item.sub_menu is not None:
            for sub_item in item.sub_menu:
                self._unregister_recursive(f"{prefix} {item.name}", sub_item)

    def startup(self):
        omni.kit.menu.utils.add_menu_items(self._items, self._title)

        for item in self._items:
            self._register_recursive(self._title, item)

    def shutdown(self):
        omni.kit.menu.utils.remove_menu_items(self._items, self._title)

        for item in self._items:
            self._unregister_recursive(self._title, item)


class ExtensionWindow:

    @property
    def title(self) -> str:
        return self._title
    
    @property
    def visible(self) -> bool:
        return self._window.visible

    @property
    def window(self) -> isaacsim.gui.components.element_wrappers.ScrollingWindow:
        return self._window

    def __init__(
        self,
        title: str,
        *,
        dock_preference: omni.ui.DockPosition = omni.ui.DockPreference.DISABLED, # omni.ui.DockPreference.LEFT_BOTTOM
        width: int = 600,
        height: int = 500,
        flags: int = omni.ui.WINDOW_FLAGS_NONE,
    ):
        if not isinstance(title, str):
            raise TypeError("title must be a str")
        self._title = title

        if not isinstance(width, int):
            raise TypeError("width must be an int")
        
        if not isinstance(height, int):
            raise TypeError("height must be an int")

        if not isinstance(dock_preference, omni.ui.DockPreference):
            raise Exception("dock_position must be a omni.ui.DockPreference")
        self._dock_preference = dock_preference

        self._window = isaacsim.gui.components.element_wrappers.ScrollingWindow(
                title=self._title, width=width, height=height, visible=False, 
                dockPreference=dock_preference, flags=flags, 
            )
        self._window.set_visibility_changed_fn(self._on_visibility_changed)
        self._window.frame.set_build_fn(self._build_ui)
        #flags = self._window.flags
        self.on_hidden_fn: Optional[Callable[[], None]] = None
        self._constructed_at_least_once = False

    def show(self) -> "ExtensionWindow":
        if not self._window.visible:
            self._window.visible = True
        return self

    def hide(self):
        if self._window.visible:
            self._window.visible = False

    def _build_ui(self):
        try:
            with omni.ui.VStack(spacing=5, height=0):
                self.construct()
        except Exception as ex:
            _logger.error("Error building window", exception=ex)
            raise ex
        
    async def _dock_beside_viewport(self):
        """Dock this window relative to the viewport, a frame after Kit has created it.

        Kit registers a new window with the workspace on the next update, so docking has to wait one
        frame; a second wait lets the layout settle before anything else touches the window.
        """
        app = omni.kit.app.get_app()
        await app.next_update_async()
        viewport = omni.ui.Workspace.get_window("Viewport")
        window = omni.ui.Workspace.get_window(self._title)
        if viewport is not None and window is not None:
            window.dock_in(viewport, self._dock_preference, 0.33)
        await app.next_update_async()

    def _on_visibility_changed(self, visible: bool):
        if self._window.visible:
            try:
                if self._constructed_at_least_once:
                    self._window.frame.rebuild()
                    return
                    #self.reconstruct()
                else:
                    self._constructed_at_least_once = True
                #with self._window.frame:
                #    self._build_ui()

                if self._dock_preference is not None:
                    self._task = asyncio.ensure_future(self._dock_beside_viewport())
            except Exception as ex:
                _logger.error(f"Error on window visible", exception=ex)
        else:
            try:
                # self._usd_context = None
                # self._stage_event_sub = None
                # self._timeline_event_sub = None
                self.deconstruct()
                self._window.frame.clear()

                if self.on_hidden_fn is not None:
                    self.on_hidden_fn()

            except Exception as ex:
                _logger.error(f"Error on window invisible", exception=ex)

    def reconstruct(self) -> None:
        self.deconstruct()
        self._window.frame.clear()
        self._window.frame.rebuild()
            

    @abstractmethod
    def construct(self) -> None:
        pass

    def deconstruct(self) -> None:
        pass

class MessageWindow(ExtensionWindow):

    def __init__(self, title: str, content: str, exception: Optional[Exception] = None):
        if not isinstance(title, str):
            raise TypeError("title must be a str")
        self._title = title

        if not isinstance(content, str):
            raise TypeError("content must be a str")
        self._content = content

        if exception is not None and not isinstance(exception, Exception):
            raise TypeError("exception must be an Optional[Exception]")
        self._exception = exception

        super().__init__(title, width=1_000, flags=omni.ui.WINDOW_FLAGS_MODAL | omni.ui.WINDOW_FLAGS_NO_COLLAPSE)
        
    def construct(self) -> None:
        with omni.ui.VStack():
            # read_only (rather than enabled=False) keeps the text selectable and
            # copyable (Ctrl+C / right-click) while preventing edits.
            self._field = omni.ui.StringField(
                multiline=True,
                height=420,
                read_only=True,
            )
            content = self._content
            if self._exception is not None:
                content += "\n\n"
                content += "".join(traceback.format_exception(
                    type(self._exception), self._exception, self._exception.__traceback__
                ))
            self._field.model.set_value(content)
            with omni.ui.HStack():
                omni.ui.Spacer()

                def _copy_clicked():
                    try:
                        import omni.kit.clipboard
                        omni.kit.clipboard.copy(content)
                    except Exception as ex:
                        _logger.error("Failed to copy message to clipboard", exception=ex)

                omni.ui.Button(
                    "Copy",
                    width=100,
                    clicked_fn=_copy_clicked,
                )
                omni.ui.Spacer(width=10)

                def _btn_clicked():
                    # Defer to the next update: toggling visibility synchronously
                    # from inside a modal window's own callback doesn't reliably
                    # dismiss the window (the modal re-asserts itself this frame).
                    async def _close():
                        await omni.kit.app.get_app().next_update_async()
                        self.hide()
                    asyncio.ensure_future(_close())

                omni.ui.Button(
                    "OK",
                    width=100,
                    clicked_fn=_btn_clicked,
                )
