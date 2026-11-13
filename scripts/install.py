#!/usr/bin/env python3
"""Make a Kit app load the bridge on every launch, with no launch flags.

A Kit app keeps what its Extensions window is told to autoload in ``user.config.json``. This
script edits that file: it adds this repository's ``exts`` folder to the extension search path,
enables the bridge, and stores the bridge's port. A tool package of your own is
registered the same way, so the bridge imports it when it starts.

    python3 scripts/install.py                 # register the bridge
    python3 scripts/install.py --remove        # undo that
    python3 scripts/install.py add-project --path <dir holding the package> --module my_tools
    python3 scripts/install.py remove-project --module my_tools [--path <dir>]
    python3 scripts/install.py status

Add ``--dry-run`` to see the changes without writing. The file is copied to
``user.config.json.bak.<time>`` before it is written. Close the Kit app first: an app that is
running writes its own settings back over the file when it exits.

Stdlib only.
"""

import argparse
import glob
import json
import os
import re
import shutil
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "exts", "isaac.mcp.bridge"))
from isaac_mcp_bridge.knobs import BIND, PORT, SETTINGS_SUBTREE, TOOL_MODULES, TOOL_PATHS  # noqa: E402

EXTENSIONS = ["isaac.mcp.bridge"]
DEFAULT_PORT = 8766
BRIDGE_KEY = SETTINGS_SUBTREE.rsplit("/", 1)[1]


def as_list(stored):
    """Kit stores a list as {"0": a, "1": b}."""
    if isinstance(stored, dict):
        return [stored[key] for key in sorted(stored, key=lambda key: (not key.isdigit(), int(key) if key.isdigit() else 0, key))]
    return list(stored) if isinstance(stored, list) else []


def as_stored(values):
    return {str(index): value for index, value in enumerate(values)}


def find_config(given):
    if given:
        return given
    found = sorted(glob.glob(os.path.expanduser("~/.local/share/ov/data/Kit/*/*/user.config.json")))
    if len(found) == 1:
        return found[0]
    if found:
        sys.exit("Several Kit apps have a user.config.json; pass the one you mean with --config:\n  " + "\n  ".join(found))
    sys.exit("No Kit user.config.json found. Start the app once so it writes one, or pass --config.")


def extension_id(name, folders):
    """``name-version`` as Kit names an enabled extension, from the extension's own extension.toml."""
    for folder in folders:
        try:
            with open(os.path.join(folder, name, "config", "extension.toml")) as stream:
                version = re.search(r'^\s*version\s*=\s*"([^"]+)"', stream.read(), re.MULTILINE)
        except OSError:
            continue
        if version:
            return f"{name}-{version.group(1)}"
    return name


class Settings:
    """The parts of user.config.json this script edits, and a log of what it changed."""

    def __init__(self, path):
        self.path = path
        with open(path) as stream:
            self.document = json.load(stream)
        persistent = self.document.setdefault("persistent", {})
        self.app = persistent.setdefault("app", {}).setdefault("exts", {})
        self.extensions = persistent.setdefault("exts", {})
        self.changes = []

    def bridge(self):
        return self.extensions.setdefault(BRIDGE_KEY, {})

    def edit(self, where, key, wanted):
        """Make the list at ``where[key]`` equal ``wanted``, logging what was added and removed."""
        before = as_list(where.get(key))
        self.changes += [f"- {key}: {value}" for value in before if value not in wanted]
        self.changes += [f"+ {key}: {value}" for value in wanted if value not in before]
        if wanted:
            where[key] = as_stored(wanted)
        else:
            where.pop(key, None)

    def put(self, key, value):
        """Set or (with None) remove one bridge setting."""
        bridge = self.bridge()
        if value is None and key in bridge:
            del bridge[key]
            self.changes.append(f"- {SETTINGS_SUBTREE}/{key}")
        elif value is not None and bridge.get(key) != value:
            bridge[key] = value
            self.changes.append(f"+ {SETTINGS_SUBTREE}/{key} = {value}")

    def save(self, dry_run):
        if not self.extensions.get(BRIDGE_KEY):
            self.extensions.pop(BRIDGE_KEY, None)
        if not self.changes:
            print("Nothing to change.")
            return
        print("\n".join(self.changes))
        if dry_run:
            print("Dry run: nothing was written.")
            return
        backup = f"{self.path}.bak.{int(time.time())}"
        shutil.copy2(self.path, backup)
        with open(self.path, "w") as stream:
            json.dump(self.document, stream, indent=4)
        print(f"Written (the previous file is {backup}). It takes effect the next time the app starts. "
              "If the app is running now, close it and run this again: on exit it writes its own settings over the file.")


def install(settings, args):
    folders = args.folder or [os.path.join(REPO, "exts")]
    names = args.enable or EXTENSIONS
    search = as_list(settings.app.get("userFolders"))
    enabled = as_list(settings.app.get("enabled"))
    others = [item for item in enabled if not any(item == name or item.startswith(name + "-") for name in names)]
    if args.remove:
        settings.edit(settings.app, "userFolders", [folder for folder in search if folder not in folders])
        settings.edit(settings.app, "enabled", others)
        settings.put(PORT.leaf, None)
        settings.put(BIND.leaf, None)
        return
    for folder in folders:
        if not os.path.isdir(folder):
            print(f"warning: {folder} is not a folder")
    settings.edit(settings.app, "userFolders", search + [folder for folder in folders if folder not in search])
    settings.edit(settings.app, "enabled", others + [extension_id(name, folders) for name in names])
    if args.autostart_port:
        settings.put(PORT.leaf, args.autostart_port)
    if args.bind_host:
        settings.put(BIND.leaf, args.bind_host)


def project(settings, args, add):
    if not args.module or (add and not args.path):
        sys.exit("add-project needs --path and --module; remove-project needs --module")
    path = os.path.abspath(os.path.expanduser(args.path)) if args.path else None
    paths = as_list(settings.bridge().get(TOOL_PATHS.leaf))
    modules = as_list(settings.bridge().get(TOOL_MODULES.leaf))
    if add:
        if not (os.path.isdir(os.path.join(path, args.module)) or os.path.isfile(os.path.join(path, args.module + ".py"))):
            print(f"warning: {path} has no module named {args.module}")
        paths, modules = paths + [path] * (path not in paths), modules + [args.module] * (args.module not in modules)
    else:
        paths, modules = [item for item in paths if item != path], [item for item in modules if item != args.module]
    settings.edit(settings.bridge(), TOOL_PATHS.leaf, paths)
    settings.edit(settings.bridge(), TOOL_MODULES.leaf, modules)


def status(settings):
    bridge = settings.extensions.get(BRIDGE_KEY, {})
    for label, value in (("extension folders", as_list(settings.app.get("userFolders"))),
                         ("enabled", as_list(settings.app.get("enabled"))),
                         ("bridge port", bridge.get(PORT.leaf)), ("bridge address", bridge.get(BIND.leaf)),
                         ("tool modules", as_list(bridge.get(TOOL_MODULES.leaf))),
                         ("tool paths", as_list(bridge.get(TOOL_PATHS.leaf)))):
        print(f"{label}: {value}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="install", choices=["install", "add-project", "remove-project", "status"])
    parser.add_argument("--config", help="the app's user.config.json (default: the only one found)")
    parser.add_argument("--folder", action="append", help="install: extension folder to register (default: this repository's exts)")
    parser.add_argument("--enable", action="append", help=f"install: extension to enable (default: {', '.join(EXTENSIONS)})")
    parser.add_argument("--autostart-port", type=int, default=DEFAULT_PORT, help=f"install: the bridge's port (default {DEFAULT_PORT})")
    parser.add_argument("--bind-host", help="install: address the bridge listens on (default: localhost only)")
    parser.add_argument("--path", help="add-project, remove-project: the folder that holds the tool package")
    parser.add_argument("--module", help="add-project, remove-project: the tool package's module name")
    parser.add_argument("--remove", action="store_true", help="install: undo the registration")
    parser.add_argument("--dry-run", action="store_true", help="show the changes and write nothing")
    args = parser.parse_args(argv)
    settings = Settings(find_config(args.config))
    print(f"config: {settings.path}")
    if args.command == "status":
        return status(settings)
    if args.command == "install":
        install(settings, args)
    else:
        project(settings, args, add=args.command == "add-project")
    settings.save(args.dry_run)


if __name__ == "__main__":
    main()
