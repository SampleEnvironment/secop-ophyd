

# Import bluesky and ophyd
import matplotlib.pyplot as plt
from bluesky import RunEngine
from bluesky.callbacks.best_effort import BestEffortCallback
from bluesky.plan_stubs import *
from bluesky.plans import *


from bluesky.utils import ProgressBarManager
from bssecop.SECoPDevices import SECoP_Node_Device


## bluesky runengine setup
 
# Create a run engine, with plotting, progressbar and transform
RE = RunEngine({}, call_returns_result=True)
bec = BestEffortCallback()
RE.subscribe(bec)
RE.waiting_hook = ProgressBarManager()
RE.ignore_callback_exceptions = False
plt.ion()

#######################################################################

# Node device creation
cryoNode = SECoP_Node_Device(host='localhost',port ='10769',loop= RE.loop)
    

# get five readings of cryo.value (1/s)
p = RE(count([cryoNode.cryo],num=5,delay=1))

# drive from 10K to 11K in five steps 
#p = RE(scan([cryoNode.cryo],cryoNode.cryo,10,11,5))

#cryoNode._secclient.tx_task.cancel()
#cryoNode._secclient.rx_task.cancel()