"""JupyterLab Server handlers"""

# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.

from __future__ import annotations

import os
import pathlib
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from jupyter_server.base.handlers import FileFindHandler, JupyterHandler
from jupyter_server.extension.handler import ExtensionHandlerJinjaMixin, ExtensionHandlerMixin
from jupyter_server.utils import url_path_join as ujoin
from tornado import template, web

from jupyterlab_server.utils import _camelCase

from .config import LabConfig, get_page_config, recursive_update
from .settings_handler import SettingsHandler
from .settings_utils import _get_overrides
from .translations_handler import TranslationsHandler

if TYPE_CHECKING:
    from .app import LabServerApp

# -----------------------------------------------------------------------------
# Module globals
# -----------------------------------------------------------------------------

MASTER_URL_PATTERN = (
    r"/(?P<mode>{}|doc)(?P<workspace>/workspaces/[a-zA-Z0-9\-\_]+)?(?P<tree>/tree/.*)?"
)

DEFAULT_TEMPLATE = template.Template(
    """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Error</title>
</head>
<body>
<h2>Cannot find template: "{{name}}"</h2>
<p>In "{{path}}"</p>
</body>
</html>
"""
)


def is_url(url: str) -> bool:
    """Test whether a string is a full URL (e.g. https://nasa.gov)"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False
    from urllib.parse import urlparse

    def _camelCase(snake_str: str) -> str:
        """Convert snake_case string to camelCase."""

    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])

    def is_url(url: str) -> bool:
        """Test whether a string is a full URL (e.g., https://nasa.gov)."""

    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False


class LabHandler(ExtensionHandlerJinjaMixin, ExtensionHandlerMixin, JupyterHandler):
    """Render the JupyterLab View."""

    @lru_cache
    def get_page_config(self) -> dict[str, Any]:
        """Construct the page config object"""
        self.application.store_id = getattr(self.application, "store_id", 0)
        config = LabConfig()
        app: LabServerApp = self.extensionapp
        settings_dir = app.app_settings_dir

        page_config = self.settings.setdefault("page_config_data", {})
        terminals = self.settings.get("terminals_available", False)
        server_root = self.settings.get("server_root_dir", "").replace(os.sep, "/")
        base_url = self.settings.get("base_url")

        # Remove trailing slash for compatibility with html-webpack-plugin
        page_config.setdefault("fullStaticUrl", self.static_url_prefix.rstrip("/"))
        page_config.setdefault("terminalsAvailable", terminals)
        page_config.setdefault("ignorePlugins", [])
        page_config.setdefault("serverRoot", server_root)
        page_config["store_id"] = self.application.store_id

        # Preferred path handling
        preferred_path = ""
        try:
            preferred_path = self.serverapp.contents_manager.preferred_dir
        except Exception:
            try:
                if self.serverapp.preferred_dir and self.serverapp.preferred_dir != server_root:
                    preferred_path = (
                        pathlib.Path(self.serverapp.preferred_dir)
                        .relative_to(server_root)
                        .as_posix()
                    )
            except Exception:
                pass
        page_config["preferredPath"] = preferred_path or "/"

        self.application.store_id += 1

        # MathJax settings
        mathjax_config = self.settings.get("mathjax_config", "TeX-AMS_HTML-full,Safe")
        mathjax_url = (
            self.mathjax_url or "https://cdnjs.cloudflare.com/ajax/libs/mathjax/2.7.7/MathJax.js"
        )
        page_config.setdefault("mathjaxConfig", mathjax_config)
        page_config.setdefault("fullMathjaxUrl", mathjax_url)

        # Add app-specific settings
        for name in config.trait_names():
            page_config[_camelCase(name)] = getattr(app, name)

        # Full URLs for extensions
        for name in config.trait_names():
            if name.endswith("_url"):
                full_name = _camelCase("full_" + name)
                full_url = getattr(app, name)
                if base_url and not is_url(full_url):
                    full_url = ujoin(base_url, full_url)
                page_config[full_name] = full_url

        recursive_update(
            page_config,
            get_page_config(
                app.extra_labextensions_path + app.labextensions_path, settings_dir, logger=self.log
            ),
        )

        # Apply custom page config hook
        page_config_hook = self.settings.get("page_config_hook")
        if page_config_hook:
            page_config = page_config_hook(self, page_config)

        return page_config

    @web.authenticated
    @web.removeslash
    def get(
        self, mode: str | None = None, workspace: str | None = None, tree: str | None = None
    ) -> None:
        """Get the JupyterLab HTML page."""
        workspace = "default" if workspace is None else workspace.replace("/workspaces/", "")
        tree_path = "" if tree is None else tree.replace("/tree/", "")

        page_config = self.get_page_config()
        page_config["mode"] = "single-document" if mode == "doc" else "multiple-document"
        page_config["workspace"] = workspace
        page_config["treePath"] = tree_path

        tpl = self.render_template("index.html", page_config=page_config)
        self.write(tpl)


class NotFoundHandler(LabHandler):
    """Handler for 404 - Page Not Found."""

    @lru_cache
    def get_page_config(self) -> dict[str, Any]:
        page_config = super().get_page_config().copy()
        page_config["notFoundUrl"] = self.request.path
        return page_config


def add_handlers(extension_app, handlers):
    """Add the appropriate handlers to the web app."""
    # Normalize directories
    for name in LabConfig.class_trait_names():
        if name.endswith("_dir"):
            setattr(extension_app, name, getattr(extension_app, name).replace(os.sep, "/"))

    # Normalize URLs
    for name in LabConfig.class_trait_names():
        if name.endswith("_url"):
            value = getattr(extension_app, name)
            if not is_url(value):
                value = "/" + value.strip("/")
                setattr(extension_app, name, value)

    url_pattern = MASTER_URL_PATTERN.format(extension_app.app_url.replace("/", ""))
    handlers.append((url_pattern, LabHandler))

    # Handle federated lab extensions
    labextensions_path = extension_app.extra_labextensions_path + extension_app.labextensions_path
    labextensions_url = ujoin(extension_app.labextensions_url, "(.*)")
    handlers.append(
        (
            labextensions_url,
            FileFindHandler,
            {
                "path": labextensions_path,
                "no_cache_paths": [] if extension_app.cache_files else ["/"],
            },
        )
    )

    # Handle local settings
    if extension_app.schemas_dir:
        overrides, error = _get_overrides(extension_app.app_settings_dir)
        if error:
            extension_app.log.warning("Failed loading overrides: %s", error)

        settings_config = {
            "app_settings_dir": extension_app.app_settings_dir,
            "schemas_dir": extension_app.schemas_dir,
            "settings_dir": extension_app.user_settings_dir,
            "labextensions_path": labextensions_path,
            "overrides": overrides,
        }

        settings_path = ujoin(extension_app.settings_url, "?")
        handlers.append((settings_path, SettingsHandler, settings_config))

        setting_path = ujoin(extension_app.settings_url, "(?P<schema_name>.+)")
        handlers.append((setting_path, SettingsHandler, settings_config))

        if extension_app.translations_api_url:
            translations_path = ujoin(extension_app.translations_api_url, "?")
            handlers.append((translations_path, TranslationsHandler, settings_config))
