"""MindMemOS 技术解析文档 — Sphinx 配置"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path("../../MindMemOS/src/mindmemos").resolve()))

project = "MindMemOS 技术解析"
copyright = "2025, mindscale-noah"
author = "mindscale-noah"
release = "0.1.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.todo",
    "sphinx_autodoc_typehints",
    "myst_parser",
]

templates_path = ["_templates"]
exclude_patterns: list[str] = []

# -- 主题 ------------------------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_title = "MindMemOS 技术深度解析"
html_logo = None

# -- autodoc ---------------------------------------------------------------
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
}
autodoc_typehints = "description"
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_private_with_doc = True

# -- myst ----------------------------------------------------------------
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "html_image",
]
myst_heading_anchors = 3
