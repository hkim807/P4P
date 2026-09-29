"""Mock robot adapted from the supplied standalone demo; never imports the SDK."""
import asyncio
import math
import time
import sys
import types
from types import SimpleNamespace as NS
sys.modules.setdefault('navel', types.ModuleType('navel'))
from robot.navel_client.navel_runtime import Runtime, Config
from robot.navel_client.adapter import NavelObservationAdapter
from robot.navel_client.behavior import BehaviorController
from robot.navel_client.main import _collect_locomotion, _collect_perception

class MemoryLog:
    def __init__(self): self.events = []
    def emit(self, event, **data): self.events.append((event, data))


class SimRobot:
    """Differential-drive simulation, task-returning SDK methods, changing UIDs.
    Uses accelerated motion ONLY to keep tests short; not a physics accuracy test.
    """
    def __init__(self, visible=True, appear_after=.0, disappear_on_arc=False):
        self.x = self.y = self.yaw = self.v = self.w = 0.
        self.person = (2.8, -.6)
        self.visible, self.appear_after = visible, appear_after
        self.disappear_on_arc = disappear_on_arc
        self.calls, self.active, self.arcs, self.seq = [], 0, 0, 0
        self.odometry_on = True
        self.speech_failure = False

    async def next_locomotion(self, timeout):
        await asyncio.sleep(.015)
        if not self.odometry_on:
            raise TimeoutError
        return NS(odometry=NS(position=NS(x=self.x, y=self.y),
                  orientation=NS(x=math.sin(self.yaw/2), y=math.cos(self.yaw/2), z=0., w=0.),
                  velocity=NS(linear_x=self.v, linear_y=self.w, angular_z=0.),
                  time=int(time.monotonic()*1e6)))

    async def next_frame(self, timeout):
        await asyncio.sleep(.05)
        self.seq += 1
        people=[]
        if self.visible and self.x >= self.appear_after and not (self.disappear_on_arc and self.arcs):
            dx,dy=self.person[0]-self.x,self.person[1]-self.y
            c,s=math.cos(self.yaw),math.sin(self.yaw)
            x,y=c*dx+s*dy,-s*dx+c*dy
            if x>0 and abs(math.atan2(y,x))<math.radians(80):
                people=[NS(uid=100+self.seq%3,face=NS(),g_head_position=None,
                           g_nose=[NS(sys=3,x=x,y=y,z=.1)])]
        return NS(time=int(time.monotonic()*1e6),persons=people)

    def _send(self, name, distance, angle, speed):
        self.calls.append(name)
        async def go():
            self.active += 1
            assert self.active == 1, 'overlapping movement senders'
            try:
                duration = abs(distance)/speed if distance else abs(angle)/70
                elapsed=0.
                while elapsed<duration:
                    dt=min(.02,duration-elapsed)
                    # Introduce rotation undertravel to exercise final live heading correction.
                    da=math.radians(angle)*dt/duration*.8
                    ds=distance*dt/duration
                    self.x+=ds*math.cos(self.yaw+da/2)
                    self.y+=ds*math.sin(self.yaw+da/2)
                    self.yaw+=da
                    self.v=ds/dt; self.w=da/dt
                    elapsed+=dt
                    await asyncio.sleep(.005)
            finally:
                self.active-=1
                self.v=self.w=0.
                self.calls.append(name+'_finished')
        return asyncio.create_task(go())

    def move_base(self,distance,speed=None,acceleration=None):
        return self._send('cruise',distance,0,speed or 1.6)
    def move_and_rotate_base(self,distance,angle,speed=None,acceleration=None):
        self.arcs+=1
        return self._send('arc',distance,angle,speed or .25)
    def rotate_base(self,angle,speed=None,acceleration=None):
        return self._send('rotate',0,angle,0)
    def base_vel(self,x,r):
        assert x==r==0
        assert self.active==0, 'stop sent before cancelling movement sender'
        self.calls.append('zero')
        self.v=self.w=0.
    def say(self,text):
        self.calls.append('say')
        async def speech():
            assert self.active==0
            if self.speech_failure: raise RuntimeError('simulated speech failure')
            await asyncio.sleep(.02)
        return asyncio.create_task(speech())



async def setup_runtime(test, robot=None, execute=False, **config):
    robot = robot or SimRobot()
    rt = Runtime(robot, Config(execute=execute, **config), MemoryLog())
    controller = BehaviorController(runtime=rt)
    adapter = NavelObservationAdapter(runtime=rt)
    queue = asyncio.Queue(maxsize=1)
    readers = [asyncio.create_task(_collect_locomotion(robot, rt)),
               asyncio.create_task(_collect_perception(robot, adapter, rt, queue, controller))]
    async def cleanup():
        await controller.shutdown()
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
    test.addAsyncCleanup(cleanup)
    await asyncio.wait_for(queue.get(), 2)
    return robot, rt, controller, queue, readers


def response(rt, decision='decision-1', target=None, action='APPROACH', distance=.7, speed=None, validity=60000):
    observation_id, context = next(reversed(rt.contexts.items()))
    target = target if target is not None else next(iter(context['targets']), '999')
    return {'behavior_intent': {'schema_version': '1.0', 'decision_id': decision,
        'observation_id': observation_id, 'social_state_id': 'state-1',
        'created_at_us': context['timestamp_us'], 'action': action,
        'target_human_id': target, 'preferences': {'preferred_social_distance_m': distance,
            'target_speed_mps': speed, 'hold_duration_s': 1.},
        'valid_for_ms': validity, 'reason_codes': ['HUMAN_DETECTED'], 'decision_confidence': .9}}


async def wait_for(predicate, timeout=5):
    async def wait():
        while not predicate():
            await asyncio.sleep(.01)
    await asyncio.wait_for(wait(), timeout)
