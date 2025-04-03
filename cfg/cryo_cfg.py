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



Node(
    "cryo_7.frappy.demo",
    "short description\n\n"
    "This is a very long description providing all the gory details "
    "about the stuff we are describing.",
    "tcp://10769",
    more="blub",
)

Mod(
    "cryo",
    "frappy_modules.cryo.Cryostat",
    "A simulated cc cryostat with heat-load, specific heat for the sample and a "
    "temperature dependent heat-link between sample and regulation.",
    group="very important/stuff",
    jitter=0.1,
    T_start=10.0,
    target=10.0,
    looptime=1,
    ramp=6,
    maxpower=20.0,
    heater=4.1,
    mode="pid",
    tolerance=0.1,
    window=30,
    timeout=900,
    p=Param(
        40, unit="%/K"
    ),  # in case 'default' is the first arg, we can omit 'default='
    i=10,
    d=2,
    pid=Group("p", "i", "d"),
    pollinterval=Param(export=False),
    value=Param(unit="K", test="customized value"),
)
