#####################################################################
# Fast-moving test node, used for move/stop tests
#####################################################################
import os
import sys

project_root = os.environ.get("FRAPPY_PROJECT_ROOT")

if project_root and os.path.isdir(project_root):
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


Node(
    "motion.frappy.demo",
    "test node with fast, deterministic modules for each interface class",
    "tcp://10772",
)

Mod(
    "mover",
    "frappy_modules.motion.FastMover",
    "linear axis moving at constant speed",
    value=0,
    target=0,
    speed=10,
)

Mod(
    "struct_mover",
    "frappy_modules.motion.FastStructMover",
    "two axes with struct value and target",
)

Mod(
    "tuple_mover",
    "frappy_modules.motion.FastTupleMover",
    "two axes with tuple value and target",
)

Mod(
    "detector",
    "frappy_modules.motion.FastTrigger",
    "measurement finishing after a short, fixed time",
    duration=0.1,
)

Mod(
    "writer",
    "frappy_modules.motion.InstantWritable",
    "writable whose value follows the target immediately",
)

Mod(
    "switch",
    "frappy_modules.motion.SlowSwitch",
    "writable with a struct target that takes time to reach it",
    duration=0.1,
)

Mod(
    "comm",
    "frappy_modules.motion.EchoCommunicator",
    "communicator echoing every command",
)
