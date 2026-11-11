"""The "MCP Tools" window: every tool the bridge has registered, with a Run button.

The window is drawn from the bridge's own tool listing, so a tool package that registers later
appears on Refresh with nothing written for it here. A click is submitted to the bridge exactly
like a request from the socket: it waits its turn behind running calls, is refused in read-only
mode, and its answer is shown beside the button.
"""

import json

import carb
import omni.ext
import omni.ui as ui

from isaac_mcp_bridge import get_mcp_bridge

TITLE = "MCP Tools"
ANSWER_CHARS = 500
NAME_WIDTH = 150
GOOD, BAD, QUIET, NAME = 0xFF6FC172, 0xFF5B5BE5, 0xFF9E9E9E, 0xFFDBA656   # ABGR


class Argument:
    """One argument of a tool: a labelled widget chosen by the argument's kind, and its value."""

    def __init__(self, field):
        self.name, self.kind, self.choices, self.required = field["name"], field["kind"], field["enum"], field["required"]
        default = field["default"]
        with ui.HStack(height=22, spacing=8):
            ui.Label(self.name + (" *" if self.required else ""), width=NAME_WIDTH, tooltip=field["description"] or "",
                     style={"color": QUIET})
            if self.kind == "enum":
                self.widget = ui.ComboBox(self.choices.index(default) if default in self.choices else 0,
                                          *[str(choice) for choice in self.choices])
            elif self.kind == "boolean":
                self.widget = ui.CheckBox(width=20)
                self.widget.model.set_value(bool(default))
            elif self.kind in ("integer", "number"):
                self.widget = ui.IntField() if self.kind == "integer" else ui.FloatField()
                if isinstance(default, (int, float)) and not isinstance(default, bool):
                    self.widget.model.set_value(default)
            else:
                self.widget = ui.StringField(tooltip="" if self.kind == "string" else f"JSON {self.kind}")
                self.widget.model.set_value(default if isinstance(default, str) else "")

    def read(self):
        """(given, value). An empty text box is not given, so the tool's own default applies."""
        model = self.widget.model
        if self.kind == "enum":
            return True, self.choices[model.get_item_value_model().get_value_as_int()]
        if self.kind == "boolean":
            return True, model.get_value_as_bool()
        if self.kind == "integer":
            return True, model.get_value_as_int()
        if self.kind == "number":
            return True, model.get_value_as_float()
        text = model.get_value_as_string()
        if not text:
            return False, None
        return True, json.loads(text) if self.kind in ("array", "object") else text


def sections(bridge):
    """[(title, [(tool name, listing)], start collapsed)] in the order namespaces were registered,
    the bridge's own tools first and collapsed."""
    by_namespace = {}
    for name, listing in sorted(bridge.get_registered_tools().items()):
        by_namespace.setdefault(listing["namespace"], []).append((name, listing))
    order = [owner["namespace"] for owner in bridge.get_owners().values()]
    order += [namespace for namespace in by_namespace if namespace not in order]
    return [("bridge" if namespace is None else namespace, by_namespace[namespace], namespace is None)
            for namespace in dict.fromkeys(order) if namespace in by_namespace]


class McpPanelExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._window = ui.Window(TITLE, width=480, height=760)
        self._draw()

    def on_shutdown(self):
        if self._window:
            self._window.destroy()
        self._window = None

    def _draw(self):
        try:
            bridge = get_mcp_bridge()
            groups = sections(bridge)
        except Exception as error:
            carb.log_warn(f"[isaac.mcp.panel] no bridge: {error}")
            bridge, groups = None, []
        with self._window.frame:
            with ui.VStack(spacing=6):
                with ui.HStack(height=30, spacing=8):
                    ui.Button("Refresh", width=90, clicked_fn=self._draw, tooltip="Read the bridge's tools again")
                    count = sum(len(tools) for _, tools, _ in groups)
                    note = "the bridge extension (isaac.mcp.bridge) is not enabled" if bridge is None else \
                        f"{count} tools" + (", read-only: tools that change things are refused" if bridge.read_only else "")
                    ui.Label(note, style={"color": BAD if bridge is None else QUIET})
                with ui.ScrollingFrame(horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF):
                    with ui.VStack(spacing=6):
                        for title, tools, collapsed in groups:
                            with ui.CollapsableFrame(title=f"{title} ({len(tools)})", collapsed=collapsed, height=0):
                                with ui.VStack(spacing=4):
                                    for name, listing in tools:
                                        self._tool(name, listing)

    def _tool(self, name, listing):
        with ui.VStack(spacing=2, height=0):
            with ui.HStack(height=26, spacing=8):
                ui.Label(name.rsplit(".", 1)[-1], width=NAME_WIDTH, tooltip=listing["description"], style={"color": NAME})
                button = ui.Button("Run", width=64, tooltip=listing["description"])
                answer = ui.Label("", word_wrap=True, style={"color": QUIET})
            arguments = [Argument(field) for field in listing["fields"]]
            button.set_clicked_fn(lambda: self._run(name, arguments, answer))
            ui.Spacer(height=3)

    def _run(self, name, arguments, answer):
        def show(text, color):
            answer.text, answer.style = text[:ANSWER_CHARS] + ("..." if len(text) > ANSWER_CHARS else ""), {"color": color}

        def done(reply):
            good = reply.get("status") == "success"
            body = reply.get("result") if good else reply.get("message", "error")
            show(body if isinstance(body, str) else json.dumps(body, default=repr), GOOD if good else BAD)

        params, missing = {}, []
        try:
            for argument in arguments:
                given, value = argument.read()
                if given:
                    params[argument.name] = value
                elif argument.required:
                    missing.append(argument.name)
        except ValueError as error:
            return show(f"not valid JSON: {error}", BAD)
        if missing:
            return show("needs " + ", ".join(missing), BAD)
        show("running", QUIET)
        get_mcp_bridge().submit(name, params, done)   # answered on this, the main, thread
