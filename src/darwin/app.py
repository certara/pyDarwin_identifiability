import os
import time
import sys

import darwin.GlobalVars as GlobalVars
import darwin.utils as utils

from darwin.Log import log
from darwin.options import options
from darwin.execution_man import start_execution_manager

import darwin.NMEngineAdapter

from .Template import Template
from .ModelRun import ModelRun
from .ModelCache import set_model_cache
from .MemoryModelCache import MemoryModelCache

from .algorithms.exhaustive import run_exhaustive
from .algorithms.GA import run_ga
from .algorithms.OPT import run_skopt
from .algorithms.PSO import run_pso


def run_template(model_template: Template) -> ModelRun:

    algorithm = options.algorithm

    log.message(f"Search start time = {time.asctime()}")

    if algorithm in ["GBRT", "RF", "GP"]:
        final = run_skopt(model_template)
    elif algorithm == "GA":
        final = run_ga(model_template)
    elif algorithm in ["EX", "EXHAUSTIVE"]:
        final = run_exhaustive(model_template)
    elif algorithm == "PSO":
        final = run_pso(model_template)
    else:
        log.error(f"Algorithm {algorithm} is not available")
        sys.exit()

    log.message(f"Number of unique models to best model = {GlobalVars.UniqueModelsToBest}")
    log.message(f"Time to best model = {GlobalVars.TimeToBest / 60:0.1f} minutes")

    log.message(f"Search end time = {time.asctime()}")

    return final


def _go_to_folder(folder: str):
    if folder:
        if not os.path.isdir(folder):
            os.mkdir(folder)

        log.message("Changing directory to " + folder)
        os.chdir(folder)


def _init_model_results():
    results_file = GlobalVars.output

    utils.remove_file(results_file)

    log.message(f"Writing intermediate output to {results_file}")

    with open(results_file, "w") as resultsfile:
        resultsfile.write(f"Run Directory,Fitness,Model,ofv,success,covar,correlation #,"
                          f"ntheta,nomega,nsigm,condition,RPenalty,PythonPenalty,NMTran messages\n")


def init_app(options_file: str, folder: str = None):
    # if running in folder, options_file may be a relative path, so need to cd to the folder first
    _go_to_folder(folder)

    options.initialize(folder, options_file)

    # if folder is not provided, then it must be set in options
    if not folder:
        _go_to_folder(options.home_dir)

    log_file = os.path.join(options.home_dir, "messages.txt")

    utils.remove_file(log_file)

    log.initialize(log_file)

    log.message(f"Options file found at {options_file}")

    GlobalVars.init_global_vars(options.home_dir)

    darwin.NMEngineAdapter.register()

    set_model_cache(MemoryModelCache())

    _init_model_results()

    start_execution_manager()
