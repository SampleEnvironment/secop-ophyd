#####################################################################
# Fast acquisition test node, used for trigger/fly/collect tests
#####################################################################
import os
import sys

project_root = os.environ.get("FRAPPY_PROJECT_ROOT")

if project_root and os.path.isdir(project_root):
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


Node(
    "acquisition.frappy.demo",
    "test node with fast, deterministic acquisition modules",
    "tcp://10773",
)

Mod(
    "counter",
    "frappy_modules.acquisition.FastAcquisition",
    "single-module acquisition counting up while it runs",
)

Mod(
    "spectrum",
    "frappy_modules.acquisition.SpectrumAcquisition",
    "single-module acquisition with a get_data command",
)
