# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import secop_ophyd

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "SECoP-Ophyd"
copyright = "2024, Peter Braun"
author = "Peter Braun"
release = secop_ophyd.__version__

# Clean up version for display - extract just X.Y.Z from version strings like "0.13.9.dev0+gb43270d29.d20251029"
if "+" in secop_ophyd.__version__:
    # Development version - use short version or "dev"
    release = secop_ophyd.__version__.split("+")[0]
else:
    release = secop_ophyd.__version__

version = secop_ophyd.__version__

language = "en"

source_suffix = ".rst"
master_doc = "index"


# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    # for diagrams
    "sphinxcontrib.mermaid",
    # Use this for generating API docs
    "autodoc2",
    # For linking to external sphinx documentation
    "sphinx.ext.intersphinx",
    # Add links to source code in API docs
    "sphinx.ext.viewcode",
    # Add a copy button to each code block
    "sphinx_copybutton",
    # For the card element
    "sphinx_design",
    # To make .nojekyll
    "sphinx.ext.githubpages",
    # To make the {ipython} directive
    "IPython.sphinxext.ipython_directive",
    # To syntax highlight "ipython" language code blocks
    "IPython.sphinxext.ipython_console_highlighting",
    # To embed matplotlib plots generated from code
    "matplotlib.sphinxext.plot_directive",
    # To parse markdown
    "myst_parser",
]

templates_path = ["_templates"]
exclude_patterns = []  # type: ignore


# Which package to load and document
autodoc2_packages = [{"path": "../../src/secop_ophyd", "auto_mode": True}]

# Put them in docs/_api which is git ignored
autodoc2_output_dir = "_api"


# Don't document private things
autodoc2_hidden_objects = {"private", "dunder", "inherited"}

# MyST parser extensions
myst_enable_extensions = ["colon_fence", "fieldlist"]

# Intersphinx configuration for linking to external documentation
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "bluesky": ("https://blueskyproject.io/bluesky/main", None),
    "ophyd-async": ("https://blueskyproject.io/ophyd-async/main", None),
    "numpy": ("https://numpy.org/devdocs/", None),
}

# Set copy-button to ignore python and bash prompts
copybutton_prompt_text = (
    r">>> |\\.\\.\\. |\\$ |In \\[\\d*\\]: | {2,5}\\.\\.\\.: | {5,8}: "
)
copybutton_prompt_is_regexp = True


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

# Generate the API documentation when building
autosummary_generate = True
numpydoc_show_class_members = False


# -- Options for HTML output ----------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "pydata_sphinx_theme"

# Theme options for pydata_sphinx_theme
html_theme_options = {
    "use_edit_page_button": True,
    "github_url": "https://github.com/SampleEnvironment/secop-ophyd",
    "icon_links": [
        {
            "name": "PyPI",
            "url": "https://pypi.org/project/secop-ophyd",
            "icon": "fas fa-cube",
        },
    ],
    "external_links": [
        {
            "name": "SECoP Specification",
            "url": "https://sampleenvironment.github.io/secop-site/",
        },
    ],
    "navigation_with_keys": False,
    "show_toc_level": 3,
}

html_logo = "../images/logo.svg"
html_favicon = "../images/favicon.ico"

html_context = {
    "github_user": "SampleEnvironment",
    "github_repo": "secop-ophyd",
    "github_version": "main",
    "doc_path": "docs",
}

# If true, "Created using Sphinx" is shown in the HTML footer.
html_show_sphinx = False

# If true, "(C) Copyright ..." is shown in the HTML footer.
html_show_copyright = False

# Custom CSS
html_css_files = ["custom.css"]
