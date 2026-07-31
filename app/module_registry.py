# app/module_registry.py
# Static module registration. To add a new module:
#   1. Create modules/<name>/ with __init__.py exporting a ModuleBase subclass
#   2. Import it here and add to MODULES list

from modules.ava_dancers import AvaDancersModule

MODULES = [
    AvaDancersModule,
    # Add new modules here — one line each
]
