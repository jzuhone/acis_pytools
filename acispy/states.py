from astropy.io import ascii
import requests
from acispy.units import get_units
from acispy.utils import get_time, ensure_list
from Chandra.cmd_states import fetch_states
from acispy.units import APQuantity, APStringArray, Quantity
from acispy.time_series import TimeSeriesData

cmd_state_codes = {("states", "hetg"): {"RETR": 0, "INSR": 1},
                   ("states", "letg"): {"RETR": 0, "INSR": 1},
                   ("states", "dither"): {"DISA": 0, "ENAB": 1},
                   ("states", "pcad_mode"): {"STBY": 0, "NPNT": 1, 
                                             "NMAN": 2, "NSUN": 3, 
                                             "PWRF": 4, "RMAN": 5, 
                                             "NULL": 6}}

state_dtypes = {"ccd_count": "int", 
                "fep_count": "int",
                "vid_board": "int",
                "clocking": "int"}

class States(TimeSeriesData):

    def __init__(self, table):
        new_table = {}
        times = Quantity([table["tstart"], table["tstop"]], "s")
        for k, v in table.items():
            if v.dtype.char != 'S':
                new_table[k] = APQuantity(v, times, get_units("states", k), 
                                          dtype=v.dtype)
            else:
                new_table[k] = APStringArray(v, times)
        super(States, self).__init__(new_table)

    @classmethod
    def from_database(cls, tstart, tstop, states=None):
        states = ensure_list(states)
        t = fetch_states(tstart, tstop, vals=states)
        table = dict((k, t[k]) for k in t.dtype.names)
        return cls(table)

    @classmethod
    def from_load_page(cls, load):
        url = "http://cxc.cfa.harvard.edu/acis/DPA_thermPredic/"
        url += "%s/ofls%s/states.dat" % (load[:-1].upper(), load[-1].lower())
        u = requests.get(url)
        t = ascii.read(u.text)
        table = dict((k, t[k].data) for k in t.keys())
        # hack
        if 'T_pin1at' in table:
            table.pop("T_pin1at")
        return cls(table)

    @classmethod
    def from_load_file(cls, states_file):
        t = ascii.read(states_file)
        table = dict((k, t[k].data) for k in t.keys())
        # hack
        if 'T_pin1at' in table:
            table.pop("T_pin1at")
        return cls(table)

    def get_states(self, time):
        time = get_time(time).secs
        state = {}
        for key in self.keys():
            state[key] = self[key][time]
        return state

    @property
    def current_states(self):
        return self.get_states("now")
