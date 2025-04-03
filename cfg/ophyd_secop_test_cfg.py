#####################################################################
# Python version of frappy config
#####################################################################
import os
import sys


# this is for adding module classes to the path
base_path = os.path.abspath(os.path.dirname(__name__))

# Detects if we are running tests via xprocess
# and adjusts the base path accordingly
if '.xprocess' in base_path:
    # Go up three more directories to reach the project root from the xprocess directory
    # Typically: .pytest_cache/d/.xprocess/cryo_sim -> project_root
    for _ in range(4):
        base_path = os.path.dirname(base_path)
    
sys.path.append(base_path)
print("path: " + base_path)
print("Name:" + __name__)


Node(
    "ophyd_secop.frappy.demo",
    "short description\n\n"
    "This is a very long description providing all the gory details "
    "about the stuff we are describing.",
    "tcp://10771",
    more="blub",
)

Mod(
    "str_test",
    "frappy_modules.ophyd_secop_test_modules.Test_Mod_str",
    "test module for reading String signals in Bluesky RE",
    group="test",
    value="blah",
)


Mod(
    "struct_of_arrays",
    "frappy_modules.ophyd_secop_test_modules.Test_Struct_of_arrays",
    "module for testing struct of arrays",
    value={
        "ints": [1, 2, 3, 4, 5],
        "strings": ["aaaa", "aaaaa", "aaaa", "aaa", "aa"],
        "floats": [1.1, 2.2, 4.3, 6.4, 7.5],
    },
    writable_strct_of_arr={
        "ints": [1, 2, 3, 4, 5],
        "strings": ["aaaa", "aaaaa", "aaaa", "aaa", "aa"],
        "floats": [1.1, 2.2, 4.3, 6.4, 7.5],
    },
)


Mod(
    "nd_arr",
    "frappy_modules.ophyd_secop_test_modules.Test_ND_arrays",
    "module for testing multidimensional arrays",
    value=[
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
    ],
    arr3d=[[[0]]],
)

Mod(
    "primitive_arrays",
    "frappy_modules.ophyd_secop_test_modules.OPHYD_test_primitive_arrays",
    "simulated hardware for testing handling of arays of primitives",
    value=10.3,
)


Mod(
    "ophy_struct",
    "frappy_modules.ophyd_secop_test_modules.OPYD_test_struct",
    "simulated hardware for testing ophyd struct behavior" "also tuples",
    group="very important/stuff",
    # jitter=0.1,
    p_start=5,
    looptime=1,
    value={"x": 10, "y": 10, "z": 10, "color": "blue"},
    target={"x": 10, "y": 10, "z": 10, "color": "blue"},
    nested_struct={
        "number": 5,
        "string": "blablalbla",
        "tupl": [1, 1, 1],
        "pos_struct": {"x": 5, "y": 10, "z": 15, "col": "green", "enum": 1},
    },
    tuple_param=(5, 5, 5, "green"),
    # ramp=6,
    # maxpower=20.0,
    # heater=4.1,
    # mode='pid',
    # tolerance=0.1,
    # window=30,
    # timeout=900,
    # p = Param(40, unit='%/K'),  # in case 'default' is the first arg, we can omit 'default='
    # i = 10,
    # d = 2,
    # pid = Group('p', 'i', 'd'),
    # pollinterval = Param(export=False),
    # value = Param(unit = 'K', test = 'customized value'),
)
