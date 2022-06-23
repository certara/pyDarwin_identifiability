import numpy as np
import skopt
import time
import logging
import heapq
import warnings
from skopt import Optimizer

from darwin.Log import log
from darwin.options import options
from darwin.execution_man import keep_going

import darwin.GlobalVars as GlobalVars

from darwin.ModelCode import ModelCode
from darwin.run_downhill import run_downhill
from darwin.Template import Template
from darwin.Model import Model, write_best_model_files
from darwin.ModelRun import ModelRun
from darwin.Population import Population

logger = logging.getLogger(__name__)
Models = []  # will put models here to query them and not rerun models, will eventually be a MongoDB

warnings.filterwarnings("ignore", message="The objective has been evaluated ")


def _create_optimizer(model_template: Template, algorithm) -> Optimizer:
    # just get list of numbers, of len of each token group
    num_groups = []

    for token_group in model_template.tokens.values():
        numerical_group = list(range(len(token_group)))
        this_x = skopt.space.Categorical(categories=numerical_group, transform="onehot")
        num_groups.append(this_x)

    # for parallel, will need and array of N number of optimizer,  n_jobs doesn't seem to do anything
    opt = Optimizer(num_groups, n_jobs=1, base_estimator=algorithm)

    return opt


# run parallel? https://scikit-optimize.github.io/stable/auto_examples/parallel-optimization.html
def run_skopt(model_template: Template) -> ModelRun:
    """
    Run one of the scikit optimize (https://scikit-optimize.github.io/stable/) algorithms, specified in the options file 
    
    Called from Darwin.run_search, _run_template.
    
    Which algorithm is used is defined in the options files, with the code for the algorithms being:

    -"algorithm":"GP"

    -"algorithm":"RF"

    -"algorithm":"GBRT"

    :param model_template: Model template to be run

    :type model_template: Template

    :return: return best model from search

    :rtype: Model
    """     
    np.random.seed(options['random_seed'])
    downhill_q = options.downhill_q

    opt = _create_optimizer(model_template, options.algorithm)

    log.message(f"Algorithm is {options.algorithm}")

    # https://scikit-optimize.github.io/stable/auto_examples/ask-and-tell.html
    
    niter_no_change = 0

    population = Population(model_template, 0)

    for generation in range(1, options['num_generations'] + 1):
        log.message(f"Starting generation {generation}")

        suggested = opt.ask(n_points=options.population_size)

        population = Population.from_codes(model_template, generation, suggested, ModelCode.from_int)

        population.run_all()

        if not keep_going():
            break

        # run downhill?
        if generation % downhill_q == 0:
            # pop will have the fitnesses without the niche penalty here

            log.message(f"Starting downhill, iteration = {generation}")

            run_downhill(model_template, population)

            if not keep_going():
                break

            suggested = [r.model.model_code.IntCode for r in population.runs]

        fitnesses = [r.result.fitness for r in population.runs]

        # I think you can tell with all models, but not sure
        opt.tell(suggested, fitnesses) 

        best_fitness = heapq.nsmallest(1, fitnesses)[0]

        best_run = GlobalVars.BestRun

        if best_fitness < best_run.result.fitness:
            niter_no_change = 0
        else:
            niter_no_change += 1

        log.message(f"Best fitness this iteration = {best_fitness:4f}  at {time.asctime()}")
        log.message(f"Best overall fitness = {best_run.result.fitness:4f},"
                    f" iteration {best_run.generation}, model {best_run.model_num}")

    if options["final_fullExhaustiveSearch"] and keep_going():
        log.message(f"Starting final downhill")

        population.name = 'FN'

        run_downhill(model_template, population)

    if niter_no_change:
        log.message(f'No change in fitness in {niter_no_change} iteration')

    log.message(f"total time = {(time.time() - GlobalVars.StartTime)/60:.2f} minutes")

    write_best_model_files(GlobalVars.FinalControlFile, GlobalVars.FinalResultFile)

    best_run = GlobalVars.BestRun

    log.message(f"Final output from best model is in {GlobalVars.FinalResultFile}")
    log.message(f'Best overall solution = [{best_run.model.model_code.IntCode}],'
                f' Best overall fitness = {best_run.result.fitness:.6f} ')

    return best_run
