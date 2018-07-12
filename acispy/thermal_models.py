import xija
import os
from astropy.units import Quantity
from astropy.io import ascii
from acispy.dataset import Dataset
from acispy.plots import DatePlot
import numpy as np
from Chandra.Time import secs2date, DateTime, date2secs
from acispy.states import States
from acispy.model import Model
from acispy.msids import MSIDs
from acispy.time_series import EmptyTimeSeries
from acispy.utils import mylog, calc_off_nom_rolls, \
    get_time, ensure_list
import Ska.Numpy
import Ska.engarchive.fetch_sci as fetch

short_name = {"1deamzt": "dea",
              "1dpamzt": "dpa",
              "1pdeaat": "psmc",
              "fptemp_11": "acisfp",
              "tmp_fep1_mong": "fep1_mong",
              "tmp_fep1_actel": "fep1_actel",
              "tmp_bep_pcb": "bep_pcb"}

full_name = {"1deamzt": "DEA",
             "1dpamzt": "DPA",
             "1pdeaat": "PSMC",
             "fptemp_11": "Focal Plane",
             "tmp_fep1_mong": "FEP1 Mongoose",
             "tmp_fep1_actel": "FEP1 Actel",
             "tmp_bep_pcb": "BEP PCB"}

limits = {'1deamzt': 35.5,
          '1dpamzt': 35.5,
          '1pdeaat': 52.5,
          'tmp_fep1_mong': 43.0,
          'tmp_fep1_actel': 43.0,
          'tmp_bep_pcb': 43.0}

margins = {'1deamzt': 2.0,
           '1dpamzt': 2.0,
           '1pdeaat': 4.5,
           'tmp_fep1_mong': 2.0,
           'tmp_fep1_actel': 2.0,
           'tmp_bep_pcb': 2.0}

def find_json(name, model_spec):
    name = short_name[name]
    if model_spec is None:
        if name == "psmc":
            path = "psmc_check"
        else:
            path = name
        if "SKA" in os.environ and os.path.exists(os.environ["SKA"]):
            model_spec = os.path.join(os.environ["SKA"],
                                      "share/%s/%s_model_spec.json" % (path, name))
        else:
            model_spec = os.path.join(os.getcwd(), "%s_model_spec.json" % name)
    if not os.path.exists(model_spec):
        raise IOError("The JSON file %s does not exist!" % model_spec)
    return model_spec


class ModelDataset(Dataset):
    def write_model(self, filename, overwrite=False):
        """
        Write the model data vs. time to an ASCII text file.

        Parameters
        ----------
        filename : string
            The filename to write the data to.
        overwrite : boolean, optional
            If True, an existing file with the same name will be overwritten.
        """
        if os.path.exists(filename) and not overwrite:
            raise IOError("File %s already exists, but overwrite=False!" % filename)
        names = []
        arrays = []
        for i, msid in enumerate(self.model.keys()):
            if i == 0:
                times = self.times("model", msid).value
                dates = self.dates("model", msid)
                names += ['time', 'date']
                arrays += [times, dates]
            names.append(msid)
            arrays.append(self["model", msid].value)
        temp_array = np.rec.fromarrays(arrays, names=names)
        fmt = {(name, '%.2f') for name in names if name != "date"}
        out = open(filename, 'w')
        Ska.Numpy.pprint(temp_array, fmt, out)
        out.close()

    def write_model_and_data(self, filename, overwrite=False):
        """
        Write the model, telemetry, and states data vs. time to
        an ASCII text file. The state data is interpolated to the
        times of the model so that everything is at a common set
        of times.

        Parameters
        ----------
        filename : string
            The filename to write the data to.
        overwrite : boolean, optional
            If True, an existing file with the same name will be overwritten.
        """
        states_to_map = ["vid_board", "pcad_mode", "pitch", "clocking", "simpos",
                         "ccd_count", "fep_count", "off_nominal_roll", "power_cmd"]
        out = []
        for i, msid in enumerate(self.model.keys()):
            if i == 0:
                for state in states_to_map:
                    self.map_state_to_msid(state, msid)
                    out.append(("msids", state))
            out.append(("model", msid))
            if ("msids", msid) in self.field_list:
                self.add_diff_data_model_field(msid)
                out += [("msids", msid), ("model", "diff_%s" % msid)]
        self.write_msids(filename, out, overwrite=overwrite)


class ThermalModelFromFiles(ModelDataset):
    """
    Fetch multiple temperature models and their associated commanded states
    from ASCII table files generated by xija or model check tools. If MSID
    data will be added, it will be interpolated to the times of the model
    data.

    Parameters
    ----------
    temp_files : string or list of strings
        Path(s) of file(s) to get the temperature model(s) from, generated from a tool
        like, i.e. dea_check. One or more files can be accepted. It is assumed
        that the files have the same timing information and have the same states.
    state_file : string
        Path of the states.dat file corresponding to the temperature file(s).
    get_msids : boolean, optional
        Whether or not to load the MSIDs corresponding to the
        temperature models for the same time period from the
        engineering archive. Default: False.

    Examples
    --------
    >>> from acispy import ThermalModelFromFiles
    >>> ds = ThermalModelFromFiles(["old_model/temperatures.dat", "new_model/temperatures.dat"],
    ...                            "old_model/states.dat", get_msids=True)

    >>> from acispy import ThermalModelFromFiles
    >>> ds = ThermalModelFromFiles(["temperatures_dea.dat", "temperatures_dpa.dat"],
    ...                            "old_model/states.dat", get_msids=True)
    """
    def __init__(self, temp_files, state_file, get_msids=False, tl_file=None):
        temp_files = ensure_list(temp_files)
        if len(temp_files) == 1:
            models = Model.from_load_file(temp_files[0])
            comps = list(models.keys())
            times = models[comps[0]].times.value
        else:
            model_list = []
            comps = []
            for i, temp_file in enumerate(temp_files):
                m = Model.from_load_file(temp_file)
                model_list.append(m)
                comps.append(list(m.keys())[0])
                if i == 0:
                    times = m[comps[0]].times.value
            comps = list(np.unique(comps))
            if len(comps) == 1:
                models = dict(("model%d" % i, m) for i, m in enumerate(model_list))
            elif len(comps) == len(temp_files):
                models = Model.join_models(model_list)
            else:
                raise RuntimeError("You can only import model files where all are "
                                   "the same MSID or they are all different!")
        states = States.from_load_file(state_file)
        if get_msids:
            if tl_file is not None:
                msids = MSIDs.from_tracelog(tl_file)
            else:
                tstart = secs2date(times[0]-700.0)
                tstop = secs2date(times[-1]+700.0)
                msids = MSIDs.from_database(comps, tstart, tstop=tstop, filter_bad=True,
                                            interpolate=True, interpolate_times=times)
                if msids[comps[0]].times.size != times.size:
                    raise RuntimeError("Lengths of time arrays for model data and MSIDs "
                                       "do not match. You probably ran a model past the "
                                       "end date in the engineering archive!")
        else:
            msids = EmptyTimeSeries()
        super(ThermalModelFromFiles, self).__init__(msids, states, models)


class ThermalModelFromLoad(ModelDataset):
    """
    Fetch a temperature model and its associated commanded states
    from a load review. Optionally get MSIDs for the same time period.
    If MSID data will be added, it will be interpolated to the times
    of the model data.

    Parameters
    ----------
    load : string
        The load review to get the model from, i.e. "JAN2516A".
    comps : list of strings, optional
        List of temperature components to get from the load models. If
        not specified all four components will be loaded.
    get_msids : boolean, optional
        Whether or not to load the MSIDs corresponding to the
        temperature models for the same time period from the
        engineering archive. Default: False.
    states_comp : string, optional
        The thermal model page to use to get the states. "DEA", "DPA",
        "PSMC", or "FP". Default: "DPA"

    Examples
    --------
    >>> from acispy import ThermalModelFromLoad
    >>> comps = ["1deamzt", "1pdeaat", "fptemp_11"]
    >>> ds = ThermalModelFromLoad("APR0416C", comps, get_msids=True)
    """
    def __init__(self, load, comps=None, get_msids=False, time_range=None,
                 tl_file=None, states_comp="DPA"):
        if comps is None:
            comps = ["1deamzt","1dpamzt","1pdeaat","fptemp_11",
                     "tmp_fep1_mong", "tmp_fep1_actel", "tmp_bep_pcb"]
        if time_range is not None:
            time_range = [date2secs(t) for t in time_range]
        model = Model.from_load_page(load, comps, time_range=time_range)
        states = States.from_load_page(load, comp=states_comp)
        if get_msids:
            if tl_file is not None:
                if time_range is None:
                    tbegin = None
                    tend = None
                else:
                    tbegin, tend = time_range
                msids = MSIDs.from_tracelog(tl_file, tbegin=tbegin, tend=tend)
            else:
                times = model[comps[0]].times.value
                tstart = secs2date(times[0]-700.0)
                tstop = secs2date(times[-1]+700.0)
                msids = MSIDs.from_database(comps, tstart, tstop=tstop,
                                            interpolate=True, interpolate_times=times)
                if msids[comps[0]].times.size != times.size:
                    raise RuntimeError("Lengths of time arrays for model data and MSIDs "
                                       "do not match. You probably ran a model past the "
                                       "end date in the engineering archive!")
        else:
            msids = EmptyTimeSeries()
        super(ThermalModelFromLoad, self).__init__(msids, states, model)


class ThermalModelRunner(ModelDataset):
    """
    Class for running Xija thermal models.

    Parameters
    ----------
    name : string
        The name of the model to simulate. Can be "dea", "dpa", "psmc", or "fep1_mong".
    tstart : string
        The start time in YYYY:DOY:HH:MM:SS format.
    tstop : string
        The stop time in YYYY:DOY:HH:MM:SS format.
    states : dict, optional
        A dictionary of modeled commanded states required for the model. The
        states can either be a constant value or NumPy arrays. If not supplied,
        the thermal model will be run with states from the commanded states
        database.
    T_init : float, optional
        The initial temperature for the thermal model run. If None,
        an initial temperature will be determined from telemetry.
        Default: None
    model_spec : string, optional
        Path to the model spec JSON file for the model. Default: None, the 
        standard model path will be used.
    include_bad_times : boolean, optional
        If set, bad times from the data are included in the array masks
        and plots. Default: False
    server : string
         DBI server or HDF5 file. Only used if the commanded states database
         is used. Default: None

    Examples
    --------
    >>> states = {"ccd_count": np.array([5,6,1]),
    ...           "pitch": np.array([150.0]*3),
    ...           "fep_count": np.array([5,6,1]),
    ...           "clocking": np.array([1]*3),
    ...           "vid_board": np.array([1]*3),
    ...           "off_nominal_roll": np.array([0.0]*3),
    ...           "simpos": np.array([-99616.0]*3)}
    >>> dpa_model = ThermalModelRunner("dpa", "2015:002:00:00:00",
    ...                                "2016:005:00:00:00", states=states,
    ...                                T_init=10.1)
    """
    def __init__(self, name, tstart, tstop, states=None, T_init=None,
                 use_msids=True, model_spec=None, include_bad_times=False,
                 server=None, ephemeris=None):

        self.name = name

        tstart = get_time(tstart)
        tstop = get_time(tstop)

        tstart_secs = DateTime(tstart).secs
        tstop_secs = DateTime(tstop).secs
        start = secs2date(tstart_secs - 700.0)

        if states is None:
            states_obj = States.from_database(start, tstop, server=server)
            states = dict((k, np.array(v)) for k, v in states_obj.items())
            states["off_nominal_roll"] = calc_off_nom_rolls(states)
        else:
            if "tstart" not in states:
                states["tstart"] = DateTime(states["datestart"]).secs
                states["tstop"] = DateTime(states["datestop"]).secs
            states_obj = States(states)

        if T_init is None:
            if not use_msids:
                raise RuntimeError("Set 'use_msids=True' if you want to use telemetry "
                                   "for setting the initial state!")
            T_init = fetch.MSID(name, tstart_secs-700., tstart_secs+700.).vals.mean()

        state_times = np.array([states["tstart"], states["tstop"]])

        self.model_spec = find_json(name, model_spec)

        ephem_times, ephem_data = self._get_ephemeris(ephemeris, tstart_secs, tstop_secs)

        self.xija_model = self._compute_model(name, tstart, tstop, states, 
                                              state_times, T_init, 
                                              ephem_times=ephem_times,
                                              ephem_data=ephem_data)

        self.bad_times = self.xija_model.bad_times
        self.bad_times_indices = self.xija_model.bad_times_indices

        if isinstance(states, dict):
            states.pop("dh_heater", None)

        components = [name]
        if 'dpa_power' in self.xija_model.comp:
            components.append('dpa_power')

        masks = {}
        if include_bad_times:
            masks[name] = np.ones(self.xija_model.times.shape, dtype='bool')
            for (left, right) in self.bad_times_indices:
                masks[name][left:right] = False

        model_obj = Model.from_xija(self.xija_model, components, masks=masks)
        if use_msids:
            msids_obj = MSIDs.from_database([name], tstart, tstop=tstop,
                                            interpolate=True,
                                            interpolate_times=self.xija_model.times)
        else:
            msids_obj = EmptyTimeSeries()
        super(ThermalModelRunner, self).__init__(msids_obj, states_obj, model_obj)

    def _get_ephemeris(self, ephemeris, tstart, tstop):
        if ephemeris is None:
            return None, None
        ephem_data = ascii.read(ephemeris, format='ascii')
        msids = ['orbitephem0_{}'.format(axis) for axis in "xyz"]
        idxs = np.logical_and(ephem_data["times"] >= tstart - 2000.0,
                              ephem_data["times"] <= tstop + 2000.0)
        ephemeris = dict((k, ephem_data[k].data[idxs]) for k in msids)
        ephemeris_times = ephemeris["times"].data[idxs]
        return ephemeris_times, ephemeris

    def _compute_model(self, name, tstart, tstop, states, state_times, T_init,
                       ephem_times=None, ephem_data=None):
        if name == "fptemp_11":
            name = "fptemp"
        if isinstance(states, np.ndarray):
            state_names = states.dtype.names
        else:
            state_names = list(states.keys())
        if "off_nominal_roll" in state_names:
            roll = np.array(states["off_nominal_roll"])
        else:
            roll = calc_off_nom_rolls(states)
        model = xija.XijaModel(name, start=tstart, stop=tstop, model_spec=self.model_spec)
        if 'eclipse' in model.comp:
            model.comp['eclipse'].set_data(False)
        model.comp[name].set_data(T_init)
        model.comp['sim_z'].set_data(np.array(states['simpos']), state_times)
        if 'roll' in model.comp:
            model.comp['roll'].set_data(roll, state_times)
        if 'dpa_power' in model.comp:
            # This is just a hack, we're not
            # really setting the power to zero.
            model.comp['dpa_power'].set_data(0.0) 
        # This is for the PSMC model
        if 'pin1at' in model.comp:
            model.comp['pin1at'].set_data(T_init-10.)
        if 'dpa0' in model.comp:
            model.comp['dpa0'].set_data(T_init)
        if 'dh_heater' in model.comp:
            model.comp['dh_heater'].set_data(states.get("dh_heater", 0), state_times)
        for st in ('ccd_count', 'fep_count', 'vid_board', 'clocking', 'pitch'):
            model.comp[st].set_data(np.array(states[st]), state_times)
        if name == "fptemp":
            for axis in "xyz":
                ephem = 'orbitephem0_{}'.format(axis)
                if ephem_times is None:
                    msid = fetch.Msid(ephem, model.tstart - 2000, model.tstop + 2000)
                    e_times = msid.times
                    e_data = msid.vals
                else:
                    e_times = ephem_times
                    e_data = ephem_data[ephem]
                model.comp[ephem].set_data(e_data, e_times)
            for i in range(1, 5):
                quat = 'aoattqt{}'.format(i)
                quat_name = 'q{}'.format(i)
                model.comp[quat].set_data(states[quat_name], state_times)
            model.comp['1cbat'].set_data(-53.0)
            model.comp['sim_px'].set_data(-120.0)

        model.make()
        model.calc()
        return model

    @classmethod
    def from_states_table(cls, name, tstart, tstop, states_file, T_init,
                          model_spec=None, include_bad_times=False, 
                          ephemeris=None):
        """
        Class for running Xija thermal models.

        Parameters
        ----------
        name : string
            The name of the model to simulate. Can be "dea", "dpa", "psmc", or "fep1mong".
        tstart : string
            The start time in YYYY:DOY:HH:MM:SS format.
        tstop : string
            The stop time in YYYY:DOY:HH:MM:SS format.
        states_file : string
            A file containing commanded states, in the same format as "states.dat" which is
            outputted by ACIS thermal model runs for loads.
        T_init : float
            The starting temperature for the model in degrees C.
        model_spec : string, optional
            Path to the model spec JSON file for the model. Default: None, the
            standard model path will be used.
        include_bad_times : boolean, optional
            If set, bad times from the data are included in the array masks
            and plots. Default: False
        """
        tstart = get_time(tstart)
        tstop = get_time(tstop)
        states = ascii.read(states_file)
        states_dict = dict((k, states[k]) for k in states.colnames)
        if "off_nominal_roll" not in states.colnames:
            states_dict["off_nominal_roll"] = calc_off_nom_rolls(states)
        return cls(name, tstart, tstop, states=states_dict, T_init=T_init,
                   model_spec=model_spec, include_bad_times=include_bad_times,
                   ephemeris=ephemeris)

    @classmethod
    def from_commands(cls, name, tstart, tstop, cmds, T_init,
                      model_spec=None, include_bad_times=False, ephemeris=None):
        tstart = get_time(tstart)
        tstop = get_time(tstop)
        t = States.from_commands(tstart, tstop, cmds)
        states = {k: t[k].value for k in t.keys()}
        return cls(name, tstart, tstop, states=states, T_init=T_init,
                   model_spec=model_spec, include_bad_times=include_bad_times,
                   ephemeris=ephemeris)

    @classmethod
    def from_backstop(cls, name, backstop_file, T_init, model_spec=None,
                      include_bad_times=False,  ephemeris=None):
        import Ska.ParseCM
        bs_cmds = Ska.ParseCM.read_backstop(backstop_file)
        tstart = bs_cmds[0]['time']
        tstop = bs_cmds[-1]['time']
        return cls.from_commands(name, tstart, tstop, bs_cmds, T_init,
                                 model_spec=model_spec, include_bad_times=include_bad_times,
                                 ephemeris=ephemeris)

    def make_dashboard_plots(self, yplotlimits=None, errorplotlimits=None, fig=None,
                             figfile=None, bad_times=None):
        """
        Make dashboard plots for the particular thermal model.

        Parameters
        ----------
        yplotlimits : two-element array_like, optional
            The (min, max) bounds on the temperature to use for the
            temperature vs. time plot. Default: Determine the min/max
            bounds from the telemetry and model prediction and
            decrease/increase by degrees to determine the plot limits.
        errorplotlimits : two-element array_like, optional
            The (min, max) error bounds to use for the error plot.
            Default: [-15, 15]
        fig : :class:`~matplotlib.figure.Figure`, optional
            A Figure instance to plot in. Default: None, one will be
            created if not provided.
        figfile : string, optional
            The file to write the dashboard plot to. One will be created
            if not provided.
        bad_times : list of tuples, optional
            Provide a set of times to exclude from the creation of the
            dashboard plot.
        """
        from xijafit import dashboard as dash
        import matplotlib.pyplot as plt
        if fig is None:
            fig = plt.figure(figsize=(20,10))
        msid = self.name
        if ("msids", msid) not in self.field_list:
            raise RuntimeError("You must include the real data if you want to make a "
                               "dashboard plot! Set use_msids=True when creating the"
                               "thermal model!")
        telem = self["msids", msid]
        pred = self["model", msid]
        mask = np.logical_and(telem.mask, pred.mask)
        if bad_times is not None:
            for (left, right) in bad_times:
                idxs = np.logical_and(telem.times.value >= date2secs(left),
                                      telem.times.value <= date2secs(right))
                mask[idxs] = False
        times = telem.times.value[mask]
        if yplotlimits is None:
            ymin = min(telem.value[mask].min(), pred.value[mask].min())-2
            ymax = min(telem.value[mask].max(), pred.value[mask].max())+2
            yplotlimits = [ymin, ymax]
        if errorplotlimits is None:
            errorplotlimits = [-15, 15]
        if msid == "fptemp_11":
            caution = None
            planning = None
        else:
            caution = limits[self.name]+margins[self.name]
            planning = limits[self.name]
        mylimits = {"units": "C", "caution_high": caution, "planning_limit": planning}
        if msid == "fptemp_11":
            mylimits["acisi_limit"] = -114.0
            mylimits["aciss_limit"] = -112.0
            mylimits["fp_sens_limit"] = -118.7
        dash.dashboard(pred.value[mask], telem.value[mask], times, mylimits,
                       msid=self.name, modelname=full_name[self.name],
                       errorplotlimits=errorplotlimits, yplotlimits=yplotlimits,
                       fig=fig, figfile=figfile)

def find_text_time(time, hours=1.0):
    return secs2date(date2secs(time)+hours*3600.0)

class SimulateCTIRun(ThermalModelRunner):
    """
    Class for simulating thermal models during CTI runs under constant conditions.

    Parameters
    ----------
    name : string
        The name of the model to simulate. Can be "dea", "dpa", "psmc", or "fep1mong".
    tstart : string
        The start time of the CTI run in YYYY:DOY:HH:MM:SS format.
    tstop : string
        The stop time of the CTI run in YYYY:DOY:HH:MM:SS format.
    T_init : float
        The starting temperature for the model in degrees C.
    pitch : float
        The pitch at which to run the model in degrees. 
    ccd_count : integer
        The number of CCDs to clock.
    vehicle_load : string, optional
        If a vehicle load is running, specify it here, e.g. "SEP0917C".
        Default: None, meaning no vehicle load. If this parameter is set,
        the input values of pitch and off-nominal roll will be ignored
        and the values from the vehicle load will be used.
    simpos : float, optional
        The SIM position at which to run the model. Default: -99616.0
    off_nominal_roll : float, optional
        The off-nominal roll in degrees for the model. Default: 0.0
    dh_heater: integer, optional
        Flag to set whether (1) or not (0) the detector housing heater is on. 
        Default: 0
    clocking : integer, optional
        Set to 0 if you want to simulate a CTI run which doesn't clock, which
        you probably don't want to do if you're going to simulate an actual
        CTI run. Default: 1
    model_spec : string, optional
        Path to the model spec JSON file for the model. Default: None, the 
        standard model path will be used. 

    Examples
    --------
    >>> dea_run = SimulateCTIRun("dea", "2016:201:05:12:03", "2016:201:05:12:03",
    ...                          14.0, 150., ccd_count=5, off_nominal_roll=-6.0, 
    ...                          dh_heater=1)
    """
    def __init__(self, name, tstart, tstop, T_init, pitch, ccd_count,
                 vehicle_load=None, simpos=-99616.0, off_nominal_roll=0.0, 
                 dh_heater=0, clocking=1, model_spec=None):
        tstart = get_time(tstart)
        tstop = get_time(tstop)
        self.vehicle_load = vehicle_load
        datestart = tstart
        tstart = DateTime(tstart).secs
        tstop = DateTime(tstop).secs
        datestop = secs2date(tstop)
        tend = tstop+0.5*(tstop-tstart)
        dateend = secs2date(tend)
        self.datestart = datestart
        self.datestop = datestop
        self.tstart = Quantity(tstart, "s")
        self.tstop = Quantity(tstop, "s")
        self.dateend = dateend
        self.T_init = Quantity(T_init, "deg_C")
        if vehicle_load is None:
            states = {"ccd_count": np.array([ccd_count], dtype='int'),
                      "fep_count": np.array([ccd_count], dtype='int'),
                      "clocking": np.array([clocking], dtype='int'),
                      'vid_board': np.array([clocking], dtype='int'),
                      "pitch": np.array([pitch]),
                      "simpos": np.array([simpos]),
                      "datestart": np.array([self.datestart]),
                      "datestop": np.array([self.dateend]),
                      "tstart": np.array([self.tstart.value]),
                      "tstop": np.array([tend]),
                      "off_nominal_roll": np.array([off_nominal_roll]),
                      "dh_heater": np.array([dh_heater], dtype='int')}
        else:
            mylog.info("Modeling a %d-chip CTI run concurrent with " % ccd_count +
                       "the %s vehicle loads." % vehicle_load)
            states = dict((k, state.value) for (k, state) in
                          States.from_load_page(vehicle_load).table.items())
            states["off_nominal_roll"] = calc_off_nom_rolls(states)
            cti_run_idxs = states["tstart"] < tstop
            states["ccd_count"][cti_run_idxs] = ccd_count
            states["fep_count"][cti_run_idxs] = ccd_count
            states["clocking"][cti_run_idxs] = 1
            states["vid_board"][cti_run_idxs] = 1
        super(SimulateCTIRun, self).__init__(name, datestart, dateend, states,
                                             T_init, model_spec=model_spec)

        mylog.info("Run Parameters")
        mylog.info("--------------")
        mylog.info("Start Datestring: %s" % datestart)
        mylog.info("Stop Datestring: %s" % datestop)
        mylog.info("Initial Temperature: %g degrees C" % T_init)
        mylog.info("CCD Count: %d" % ccd_count)
        if vehicle_load is None:
            disp_pitch = pitch
            disp_roll = off_nominal_roll
        else:
            pitches = states["pitch"][cti_run_idxs]
            rolls = states["off_nominal_roll"][cti_run_idxs]
            disp_pitch = "Min: %g, Max: %g" % (pitches.min(), pitches.max())
            disp_roll = "Min: %g, Max: %g" % (rolls.min(), rolls.max())
        mylog.info("Pitch: %s" % disp_pitch)
        mylog.info("SIM Position: %g" % simpos)
        mylog.info("Off-nominal Roll: %s" % disp_roll)
        mylog.info("Detector Housing Heater: %s" % {0: "OFF", 1: "ON"}[dh_heater])

        mylog.info("Model Result")
        mylog.info("------------")

        self.limit = Quantity(limits[self.name], "deg_C")
        viols = self.mvals.value > self.limit.value
        if np.any(viols):
            idx = np.where(viols)[0][0]
            self.limit_time = self.times('model', self.name)[idx]
            self.limit_date = secs2date(self.limit_time)
            self.duration = Quantity((self.limit_time.value-tstart)*0.001, "ks")
            msg = "The limit of %g degrees C will be reached at %s, " % (self.limit.value, self.limit_date)
            msg += "after %g ksec." % self.duration.value
            mylog.info(msg)
            if self.limit_time < self.tstop:
                self.violate = True
                viol_time = "before"
            else:
                self.violate = False
                viol_time = "after"
            mylog.info("The limit is reached %s the end of the CTI run." % viol_time)
        else:
            self.limit_time = None
            self.limit_date = None
            self.duration = None
            self.violate = False
            mylog.info("The limit of %g degrees C is never reached." % self.limit.value)

        if self.violate:
            mylog.warning("This CTI run is NOT safe from a thermal perspective.")
        else:
            mylog.info("This CTI run is safe from a thermal perspective.")

    def plot_model(self, no_annotations=False):
        """
        Plot the simulated model run.

        Parameters
        ----------
        no_annotations : boolean, optional
            If True, don't put lines or text on the plot. Shouldn't be
            used if you're actually trying to determine if a CTI run is
            safe. Default: False
        """
        if self.vehicle_load is None:
            field2 = None
        else:
            field2 = "pitch"
        viol_text = "NOT SAFE" if self.violate else "SAFE"
        dp = DatePlot(self, [("model", self.name)], field2=field2)
        if not no_annotations:
            dp.add_hline(self.limit.value, ls='--', lw=2, color='g')
            dp.add_vline(self.datestart, ls='--', lw=2, color='b')
            dp.add_text(find_text_time(self.datestart), self.limit.value - 2.0,
                        "START CTI RUN", color='blue', rotation="vertical")
            dp.add_vline(self.datestop, ls='--', lw=2, color='b')
            dp.add_text(find_text_time(self.datestop), self.limit.value - 12.0,
                        "END CTI RUN", color='blue', rotation="vertical")
            dp.add_text(find_text_time(self.datestop, hours=4.0), self.T_init.value+2.0,
                        viol_text, fontsize=22, color='black')
            if self.limit_date is not None:
                dp.add_vline(self.limit_date, ls='--', lw=2, color='r')
                dp.add_text(find_text_time(self.limit_date), self.limit.value-2.0,
                            "VIOLATION", color='red', rotation="vertical")
        dp.set_xlim(find_text_time(self.datestart, hours=-1.0), self.dateend)
        dp.set_ylim(self.T_init.value-2.0, 
                    max(self.limit.value, self.mvals.value.max())+3.0)
        return dp

    def get_temp_at_time(self, t):
        """
        Get the model temperature at a time *t* seconds
        past the beginning of the CTI run.
        """
        t += self.tstart.value
        return Quantity(np.interp(t, self['model', self.name].times.value,
                                  self['model', self.name].value), "deg_C")

    @property
    def mvals(self):
        return self['model', self.name]

    def write_msids(self, filename, fields, mask_field=None, overwrite=False):
        raise NotImplementedError

    def write_states(self, states_file, overwrite=False):
        raise NotImplementedError

    def write_model(self, filename, overwrite=False):
        raise NotImplementedError

    def make_dashboard_plots(self, yplotlimits=None, errorplotlimits=None, fig=None):
        raise NotImplementedError

    def write_model_and_data(self, filename, overwrite=False):
        raise NotImplementedError
