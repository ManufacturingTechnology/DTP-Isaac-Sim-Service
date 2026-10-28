from typing import List, Optional, Type
from pathlib import Path
from pydantic import BaseModel
import os

import isaacsim.gui.components.ui_utils
from isaacsim.gui.components.element_wrappers import (
    UIWidgetWrapper,
    Button,
    CheckBox,
    Frame,
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
import omni
import omni.timeline
import omni.ui
import pxr
import pxr.Usd
import pxr.Sdf
from pydantic import ValidationError

from .core import Renderable, ExtensionWindow
from .model_editor import (
    FieldEditor,
    ModelEditor,
    ModelEditorPane,
    ModelEditorWindow,
    _INVALID_LABEL_STYLE,
    _VALID_LABEL_STYLE,
)
from ..entity.behavior import BehaviorModel, Behavior
from ..entity.waypoint import WaypointModel
from ..lib.logging import _logger
from ..service.subscription import SubscriptionService
from ..service.entity import EntityService # BehaviorRegistryService


class BehaviorWrapper(ModelEditorPane):

    def __init__(self, prim: pxr.Usd.Prim):
        super().__init__(prim, BehaviorModel)
        self.editor._on_validation_fn = self._validate_model

    def render(self) -> None:
        super().render()
        self._edit_button = omni.ui.Button(
            "Edit Config",
            clicked_fn=self._on_edit_config_clicked,
        )
        # super().render() already validated once (before the button existed); validate
        # again to set the initial enabled state from the current model validity.
        self._edit_button.enabled = self.editor.validate()

        if self._edit_button.enabled:
            self._validate_config_for_edit_button()

    def _set_edit_button_invalid(self, invalid: bool) -> None:
        """Color the Edit Config button red when its behavior config is invalid, restore
        the default otherwise. Mirrors the red-label treatment FieldEditor.set_invalid()
        gives fields on validation errors. Safe to call before the button exists."""
        button: omni.ui.Button = getattr(self, "_edit_button", None)
        if button is None:
            return
        button.set_style({"Button.Label": _INVALID_LABEL_STYLE} if invalid else {"Button.Label": _VALID_LABEL_STYLE})

    def _on_edited(self, model_editor: ModelEditor, field_editor: FieldEditor, model: BehaviorModel, old_value: object) -> None:
        super()._on_edited(model_editor, field_editor, model, old_value)
        self._validate_config_for_edit_button()

    def _validate_config_for_edit_button(self) -> None:
        if getattr(self, "_edit_button", None) is None:
            return
        
        is_playing = omni.timeline.get_timeline_interface().is_playing()
        if is_playing:
            self._edit_button.enabled = False
            self._status_field.model.set_value("Can not edit config while playing")
            self._status_icon.source_url = self._bad_status_icon_path
            self._set_edit_button_invalid(True)
            return

        self._edit_button.enabled = True

        # Make sure the script can be loaded
        try:
            EntityService().behavior_cache.refresh()
        except Exception as ex:
            self._status_field.model.set_value(f"Could not refresh behaviors.\n\n{ex}")
            self._status_icon.source_url = self._bad_status_icon_path
            self._set_edit_button_invalid(True)
            return

        behavior: Behavior = EntityService().behavior_cache.get(self.prim)
        if behavior is None:
            self._status_field.model.set_value(f"Could not load behavior script.")
            self._status_icon.source_url = self._bad_status_icon_path
            self._set_edit_button_invalid(True)
            self._edit_button.enabled = False
            return
        if len(behavior.runners) == 0:
            self._status_field.model.set_value(
                f"No classes inheriting amt.dtp.ise.scripting.BehaviorScript found."
            )
            self._status_icon.source_url = self._bad_status_icon_path
            self._set_edit_button_invalid(True)
            self._edit_button.enabled = False
            return

        if len(behavior.runners) != 1:
            names = ", ".join([str(r.behavior_script_type.__name__) for r in behavior.runners])
            self._status_field.model.set_value(
                f"Only one class inheriting BehaviorScript can be implemented in a behavior script.\n" +
                f"Classes Found: {names}"
            )
            self._status_icon.source_url = self._bad_status_icon_path
            self._set_edit_button_invalid(True)
            self._edit_button.enabled = False
            return

        config_type = behavior.runners[0].behavior_script.config_type

        if config_type is None:
            self._status_field.model.set_value(f"Behavior Config is not specified in behavior script.\nCan not edit Behavior Config.")
            self._status_icon.source_url = self._bad_status_icon_path
            self._set_edit_button_invalid(True)
            self._edit_button.enabled = False
            return

        if not self.prim.HasCustomDataKey(BehaviorModel.config_custom_data_key):
            self._status_field.model.set_value(f"Behavior Config does not exist.\nClick 'Edit Config' to create it.")
            self._status_icon.source_url = self._bad_status_icon_path
            self._set_edit_button_invalid(True)
            return

        try:
            config_type.model_validate_json(self.prim.GetCustomDataByKey(BehaviorModel.config_custom_data_key))
        except Exception as ex:
            self._status_field.model.set_value(f"Invalid Behavior Config: {ex}")
            self._status_icon.source_url = self._bad_status_icon_path
            self._set_edit_button_invalid(True)
            return

        # Success, clear any error highlight on the edit button.
        self._status_field.model.set_value("")
        self._status_icon.source_url = ""
        self._set_edit_button_invalid(False)

    def _on_validation_error(self, model_editor: ModelEditor, field_editor: FieldEditor, validation_error: ValidationError) -> None:
        super()._on_validation_error(model_editor, field_editor, validation_error)
        if getattr(self, "_edit_button", None) is not None:
            self._edit_button.enabled = False
            # Button is disabled; clear the error highlight so the disabled state isn't
            # also painted red (the field labels already carry the validation error).
            self._set_edit_button_invalid(False)

    def _validate_model(self, model_editor: ModelEditor, field_editor: FieldEditor, model: BehaviorModel) -> Optional[str]:
        # Make sure that the behavior script actually exists
        directory_path = str(Path(EntityEditorWindow.get_prim_source_file(self.prim)).parent)
        resolved_path = Path(os.path.join(directory_path, model.script_path)).resolve()
        if not os.path.isfile(resolved_path):
            return f"Behavior script '{model.script_path}' does not exist at:\n'{resolved_path}'."
        
        return None
    
    def _on_edit_config_clicked(self) -> None:
        EntityService().behavior_cache.refresh()
        behavior = EntityService().behavior_cache.get(self.prim)
        if behavior is None:
            _logger.debug(f"Edit Config clicked when no behaviors available")
            return
        if len(behavior.runners) != 1:
            _logger.debug(f"Invalid number of runners in '{behavior.initialized_script_path}': {len(behavior.runners)}")
            return
        try:
            pane = ModelEditorPane(self.prim, behavior.runners[0].behavior_script.config_type, custom_data_key=BehaviorModel.config_custom_data_key)
        except ValidationError as ex:
            # If it won't validate, clear it and create a new one.
            self.prim.ClearCustomDataByKey(BehaviorModel.config_custom_data_key)
            try:
                pane = ModelEditorPane(self.prim, behavior.runners[0].behavior_script.config_type, custom_data_key=BehaviorModel.config_custom_data_key)
            except Exception as ex:
                self._status_field.model.set_value("Error: Could not delete and recreate config.\nConfig in external reference may be invalid.\nEdit the config in the referenced file.")
                self._status_icon.source_url = self._bad_status_icon_path
                return
        window = ModelEditorWindow(pane)
        window.on_hidden_fn = self._on_edit_config_finished
        window.show()

    def _on_edit_config_finished(self) -> None:
        self._validate_config_for_edit_button()


class EntityEditorWindow(ExtensionWindow):

    @staticmethod
    def get_prim_source_file(prim: pxr.Usd.Prim) -> str:
        # Not working for current root file
        p: pxr.Usd.Prim = prim
        stage = omni.usd.get_context().get_stage()
        root_prim = stage.GetPseudoRoot()
        while True:
            source_file = EntityEditorWindow.get_external_reference_prim_source_file(p)
            if source_file is not None:
                break
            p = p.GetParent()
            if p == root_prim or str(p.GetPath()) == "/":
                # Root of currently open file/stage
                root_layer = stage.GetRootLayer()
                source_file = root_layer.identifier if root_layer else None
                break
            if not p.IsValid():
                break

        return source_file

    @staticmethod
    def get_external_reference_prim_source_file(prim: pxr.Usd.Prim) -> Optional[str]:
        # Only gets source file if prim is an external reference
        #   If clicking on a child of another file, doesn't list external reference
        if not prim.IsValid():
            return None

        # Get the prim stack (all composing specs)
        stack = prim.GetPrimStack()
        payloads = []

        for prim_spec in stack:
            # Get applied payloads from the spec's payloadList
            payload_list = prim_spec.payloadList.GetAppliedItems()
            for payload in payload_list:
                if payload.assetPath:
                    # Resolve relative path if needed (using the spec's layer)
                    resolved_path = pxr.Sdf.ComputeAssetPathRelativeToLayer(prim_spec.layer, payload.assetPath)
                    payloads.append(resolved_path)
                # elif payload.primPath:
                #     # Fallback for internal paths
                #     payloads.append(str(payload.primPath))

        if len(payloads) == 0:
            return None

        return payloads[-1]
    
    def __init__(self):
        super().__init__("Entity Editor", dock_preference=omni.ui.DockPreference.LEFT)

        self._sub = SubscriptionService().create_subscription_set(self.__class__.__name__)
        self._sub.on_stage_selection_changed = self._on_selection_changed
        self._sub.on_timeline_play = self._on_play
        self._sub.on_timeline_stop = self._on_stop
        self._selected_prim: Optional[pxr.Usd.Prim] = None
        self._selected_file_path: Optional[str] = None

        self._is_playing = False
        self._root_vstack: Optional[omni.ui.VStack] = None
        self._renderables: Optional[List[Renderable]] = None

    def construct(self) -> None:
        self._root_vstack = omni.ui.VStack()
        with self._root_vstack:
            self._set_current_selection()
            self._sub.enable()

            self._construct_static()

            if self._selected_prim is None or not self._selected_prim.IsValid() or str(self._selected_prim.GetPath()) == "/":
                return

            self._renderables: List[Renderable] = [
                ModelEditorPane(self._selected_prim, WaypointModel),
                BehaviorWrapper(self._selected_prim),
            ]
        
            for renderable in self._renderables:
                collapsed = True
                if isinstance(renderable, ModelEditorPane):
                    if renderable.editor.model is not None:
                        collapsed = False # data exists, so show it
                with CollapsableFrame(renderable.title, collapsed=collapsed):
                    with omni.ui.VStack(style=isaacsim.gui.components.ui_utils.get_style(), spacing=5, height=0):
                        renderable.render()

        self._is_playing = omni.timeline.get_timeline_interface().is_playing()
        if self._root_vstack is not None:
            self._root_vstack.enabled = not self._is_playing

    def deconstruct(self) -> None:
        self._sub.disable()

    def _on_play(self) -> None:
        self._is_playing = True
        if self._root_vstack is not None:
            self._root_vstack.enabled = False

    def _on_stop(self) -> None:
        self._is_playing = False
        if self._root_vstack is not None:
            self._root_vstack.enabled = True
        if self._renderables is not None:
            for renderable in self._renderables:
                if isinstance(renderable, BehaviorWrapper):
                    # Validate and enable button
                    renderable._validate_config_for_edit_button()

    def _construct_static(self):
        # TODO: re-run/update if file opened, closed, changed, etc.

        stage_file_path: Optional[str] = omni.usd.get_context().get_stage_url()
        if stage_file_path is None:
            stage_file_path = ""
        if stage_file_path.startswith("file://"):
            stage_file_path = stage_file_path.removeprefix("file://")
        TextBlock(
            "Stage Path",
            stage_file_path,
            num_lines=1,
            tooltip="The file path of the currently open stage file.",
            #include_copy_button=True,
        )

        selected_prim = ""
        if self._selected_prim is not None:
            selected_prim = str(self._selected_prim.GetPath())
            TextBlock(
                "Prim Path",
                selected_prim,
                num_lines=1,
                tooltip="The stage path to the selected prim.",
                #include_copy_button=True,
            )

        selected_file_path = ""
        if self._selected_file_path is not None:
            selected_file_path = self._selected_file_path
            TextBlock(
                "File Path",
                selected_file_path,
                num_lines=1,
                tooltip="The path to the file where the selected prim is defined.",
                #include_copy_button=True,
            )

    def _set_current_selection(self) -> None:
        selection_paths = omni.usd.get_context().get_selection().get_selected_prim_paths()
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if stage is None:
            return

        if len(selection_paths) == 1:
            self._selected_prim = omni.usd.get_context().get_stage().GetPrimAtPath(selection_paths[0])
            self._selected_file_path = self.get_prim_source_file(self._selected_prim)
            pass
        else:
            self._selected_prim: Optional[pxr.Usd.Prim] = omni.usd.get_context().get_stage().GetPseudoRoot()
            self._selected_file_path = omni.usd.get_context().get_stage_url()
            pass

    def _on_selection_changed(self, paths: List[str]) -> None:
        self._set_current_selection()
        self.reconstruct()
