---
title: 'Model editor (as built)'
audience: agent
---

# Model editor (as built)

> **Abstract.** How the pydantic form editor (`extension/exts/amt.dtp.ise/amt/dtp/ise/ui/model_editor.py`)
> works: editor matching, whole-model validation and error routing, list and Optional editing, the example
> window, and how to test it without Isaac Sim. User-facing description: Asset Development › Behavior
> Scripts › "Field types the form supports". Verify against the code; may lag.

## Structure

- `ModelEditor` owns a model type (and optionally an instance) and one `FieldEditor` per field, built by
  `_editor_for()` / `_build_field_editors()`: the first `FieldEditor` subclass (definition order) whose
  `matches(annotation, field_info)` accepts the field. List items use the same factory with a `FieldInfo`
  made from the item annotation (`FieldInfo.from_annotation`, which keeps `Annotated` constraints; probe
  with pydantic 2.11.10).
- Every edit collects all values (`get_value()` per editor; nested models and lists return dicts and lists),
  validates the **whole** model with pydantic, merges field-level errors (`collect_errors()`), and routes
  error locations (stringified, e.g. `("people", "1", "name")`) back through `apply_validation_errors()`.
  When valid, `on_edited_fn` fires; `ModelEditorPane` saves the model to the prim's custom data.
- `FieldEditor(name, field_info, on_edited_fn, *, label=None, label_width=150)`: `label_text` and
  `label_width` let list items show their index instead of the field title.
- `_BaseModelFieldEditor` accepts a model instance or a dict (list items are stored as dicts), keeps an
  item's collapsed state through `initial_collapsed`, and can be retitled (`set_title`).

## Lists (`_ListFieldEditor`)

- Matches `List[T]`, `list[T]`, `Tuple[T, ...]`, and `Optional` of those (`is_list_annotation`). Items of any
  type that has an editor; anything else raises a `TypeError` naming the field.
- Items are `_ListItem`s: a stable `uid`, the stored `value` (source of truth between rebuilds), the current
  `editor`. **Structural changes** (add, delete, move, duplicate, drop) store every item's widget value,
  change the list, and `rebuild()` the rows frame (omni.ui can't re-parent widgets); edits inside an item
  don't rebuild. After a rebuild the last validation result is re-applied to the new editors.
- Reordering: per-row Up / Down, list-level Move Up / Move Down for the selected item (click its index), and
  drag handles (`set_drag_fn` → `"<list id>:<uid>"`). **Each whole row is the drop target**
  (`set_accept_drop_fn(str)` and `set_drop_fn(WidgetMouseDropEvent)` with `.mime_data`, per the omni.ui 3.2.0
  stubs): the dropped item takes that row's position, and a line above (moving up) or below (moving down)
  the row shows where. Drops from another list are ignored. Rows, not thin zones between them, are the
  targets, because a release over a row must hit a drop target. Every drop logs a debug line
  (`List '<field>': drop on row …`), so a drop that doesn't arrive is visible with debug logging.
  **Verified by the user in Isaac Sim.**
- Options in the list field's `json_schema_extra`: `duplicate` (default True), `item_title_field`,
  `reorder` (`"row"` / `"list"` / `"both"`, default both; the user will pick one), `drag` (default True). Other
  keys (e.g. `extensions`) pass through to the items.
- `min_length` / `max_length` disable Add / Copy / X at the limits.
- Colors (the user's choice): **+ Add item** and **X** are the default gray with only a slight green /
  red tint (`_LIST_ADD_STYLE`, `_LIST_DELETE_STYLE`), so they don't distract.
- Pure helpers (no omni imports, unit-tested): `lib/src/amt/dtp/ise/lib/model_editing.py`
  (`lib/tests/test_model_editing.py`): list annotations and limits, default values for new items
  (`model_defaults`: required `Optional` fields become None; required primitives are left out so validation
  flags them), `to_plain`, `item_title`, `move`, `drop_target`.
- `List[str]` fields (e.g. `WaypointModel.tags`) use rows like any other list.

## Optional fields (`_OptionalFieldEditor`)

- `Optional[T]` for any `T` with an editor (str, int, float, bool, Literal, Enum, FuncEnum, Path): a checkbox
  before `T`'s editor; unchecked → `None`, the inner editor is disabled and keeps its value. Models and
  lists keep their own enable checkbox (`_BaseModelFieldEditor`, `_ListFieldEditor`).
- `_inner_field_info(inner, field_info)` builds the inner `FieldInfo`: the inner annotation's `Annotated`
  constraints plus the field's title, description, `json_schema_extra`, and field-level constraints (e.g.
  `Optional[int] = Field(None, ge=0, le=10)` puts `Ge`/`Le` on the field, and they apply to `T`).
- Works for list items: `List[Optional[int]]` gives each item its own checkbox.

## Example window

`ModelEditorExampleWindow` (AMT menu, "Model Editor Example"): edits an in-memory `_ExampleAllModel`
(never saved), with Reset to Defaults, Load Example Values, a status box, and the current model as JSON.
`_ExampleAllModel` has one field of every supported type, including the lists `numbers` (limits),
`weights`, `choices`, `files`, `people` (models, titled by `name`), `matrix` (nested lists, no duplicate),
and `optional_list`; and `Optional[...]` of every kind (`optional_name`, `optional_count` with limits,
`optional_value`, `optional_flag`, `optional_selection`, `optional_enum`, `optional_func_enum`,
`optional_file`, `optional_numbers: List[Optional[int]]`).

## Testing

- Offline: `lib/tests/test_model_editing.py` (pytest) and
  `docs/src/Agents/Scratch/harnesses/model_editor_smoke.py <repo>` (stubs omni/Isaac with MagicMock; 36 checks of
  matching, construction, every list action including row drops, limits, error routing, row building, and
  the Optional editor; 36/36).
  Widgets are mocks, so rendering and interaction are **not** verified offline.
- In Isaac Sim (the user): row drag and drop is verified. Still to confirm ❓: the example window, every other
  list action, the Optional editor, and the existing forms (behavior config, waypoints) unchanged.
