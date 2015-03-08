from collections import OrderedDict
import importlib

from artiq.protocols.sync_struct import Notifier
from artiq.protocols.pc_rpc import Client, BestEffortClient
from artiq.master.results import result_dict_to_hdf5


class ResultDB:
    def __init__(self, init_rt_results, update_rt_results):
        self.init_rt_results = init_rt_results
        self.update_rt_results = update_rt_results

    def init(self, rtr_description):
        assert not hasattr(self, "realtime_data")
        assert not hasattr(self, "data")

        realtime_results_set = set()
        for rtr in rtr_description.keys():
            if isinstance(rtr, tuple):
                for e in rtr:
                    realtime_results_set.add(e)
            else:
                realtime_results_set.add(rtr)

        self.realtime_data = Notifier({x: [] for x in realtime_results_set})
        self.data = Notifier(dict())

        self.init_rt_results(rtr_description)
        self.realtime_data.publish = lambda notifier, data: \
            self.update_rt_results(data)

    def _request(self, name):
        try:
            return self.realtime_data[name]
        except KeyError:
            try:
                return self.data[name]
            except KeyError:
                self.data[name] = []
                return self.data[name]

    def request(self, name):
        r = self._request(name)
        r.kernel_attr_init = False
        return r

    def set(self, name, value):
        if name in self.realtime_data.read:
            self.realtime_data[name] = value
        else:
            self.data[name] = value

    def write_hdf5(self, f):
        result_dict_to_hdf5(f, self.realtime_data.read)
        result_dict_to_hdf5(f, self.data.read)


def _create_device(desc, dbh):
    ty = desc["type"]
    if ty == "local":
        module = importlib.import_module(desc["module"])
        device_class = getattr(module, desc["class"])
        return device_class(dbh, **desc["arguments"])
    elif ty == "controller":
        if desc["best_effort"]:
            cl = BestEffortClient
        else:
            cl = Client
        return cl(desc["host"], desc["port"], desc["target_name"])
    else:
        raise ValueError("Unsupported type in device DB: " + ty)


class DBHub:
    """Connects device, parameter and result databases to experiment.
    Handle device driver creation and destruction.

    """
    def __init__(self, ddb, pdb, rdb):
        self.ddb = ddb
        self.active_devices = OrderedDict()

        self.get_parameter = pdb.request
        self.set_parameter = pdb.set
        self.init_results = rdb.init
        self.get_result = rdb.request
        self.set_result = rdb.set

    def get_device(self, name):
        if name in self.active_devices:
            return self.active_devices[name]
        else:
            desc = self.ddb.request(name)
            while isinstance(desc, str):
                # alias
                desc = self.ddb.request(desc)
            dev = _create_device(desc, self)
            self.active_devices[name] = dev
            return dev

    def close(self):
        """Closes all active devices, in the opposite order as they were
        requested.

        Do not use the same ``DBHub`` again after calling
        this function.

        """
        for dev in reversed(list(self.active_devices.values())):
            if isinstance(dev, (Client, BestEffortClient)):
                dev.close_rpc()
            elif hasattr(dev, "close"):
                dev.close()
