"""Fresh-process checks for the complete Navel package API and isolation."""

import subprocess
import sys
import unittest


class NavelLazyImportTests(unittest.TestCase):
    def check_process(self, code):
        result = subprocess.run(
            [sys.executable, "-B", "-c", code], capture_output=True,
            text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_transport_import_does_not_load_behavior(self):
        self.check_process('''
import sys
from robot.navel_client.transport import ObservationTransport
assert not any(name.startswith("robot.navel_client.behavior") for name in sys.modules)
assert "navel" not in sys.modules
''')

    def test_all_exports_and_direct_behavior_import_remain_identical(self):
        self.check_process('''
import robot.navel_client as package
assert package.__all__ == ["NavelAdapterConfig", "NavelObservationAdapter", "BehaviorController", "ObservationTransport", "TransportError"]
assert set(package.__all__) <= set(dir(package))
from robot.navel_client import BehaviorController
from robot.navel_client.behavior import BehaviorController as original
assert BehaviorController is original is package.BehaviorController
from robot.navel_client.adapter import NavelAdapterConfig, NavelObservationAdapter
from robot.navel_client.transport import ObservationTransport, TransportError
assert package.NavelAdapterConfig is NavelAdapterConfig
assert package.NavelObservationAdapter is NavelObservationAdapter
assert package.ObservationTransport is ObservationTransport
assert package.TransportError is TransportError
namespace = {}
exec("from robot.navel_client import *", namespace)
for name in package.__all__:
    assert namespace[name] is getattr(package, name)
try:
    package.nonexistent_export
except AttributeError:
    pass
else:
    raise AssertionError("unknown attributes must raise AttributeError")
''')
