import numpy as np
import Templater
import time
import GlobalVars
import model_code
import runAllModels
import heapq
import os
import gc


def exhaustive(model_template):
    GlobalVars.StartTime = time.time()
    Num_Groups = []
    for thisKey in model_template.tokens.keys():
        tokenGroup = model_template.tokens.get(thisKey)
        Num_Groups.append(list(range(len(tokenGroup))))
    # need to add another group if searching on omega bands
    if model_template.search_omega_band:
        Num_Groups.append(list(range(model_template.omega_bandwidth)))
    codes = np.array(np.meshgrid(*Num_Groups)).T.reshape(-1, len(Num_Groups))
    # convert to regular list
    codes = codes.tolist()
    NumModels = len(codes)
    model_template.printMessage(f"Total of {NumModels} to be run in exhaustive search")
    maxes = model_template.gene_max
    lengths = model_template.gene_length
    # break into smaller list, for memory management
    MaxModels = model_template.options['max_model_list_size']
    # Models = [None]*MaxModels
    current_start = 0
    current_last = current_start + MaxModels
    if current_last > NumModels:
        MaxModels = NumModels
        current_last = NumModels
    runAllModels.InitModellist(model_template)
    fitnesses = []
    best_fitness = model_template.options['crash_value']
    while current_last <= NumModels:
        if current_last > len(codes):
            current_last = len(codes)
        # for thisInts,model_num in zip(codes,range(len(codes))):
        thisModel = 0
        Models = [None] * MaxModels
        for thisInts, model_num in zip(codes[current_start:current_last], range(current_start, current_last)):
            code = model_code.model_code(thisInts, "Int", maxes, lengths)
            Models[thisModel] = Templater.model(model_template, code, model_num, True, 0)
            thisModel += 1
        runAllModels.run_all(Models)
        for i in range(len(Models)):
            if Models[i].fitness < best_fitness:
                best_fitness = Models[i].fitness
                best_model = Models[i].makeCopy()
            fitnesses.append(Models[i].fitness)
        model_template.printMessage(f"Current Best fitness = {best_fitness}")
        current_start = current_last
        current_last = current_start + MaxModels
    #best = heapq.nsmallest(1, range(len(fitnesses)), fitnesses.__getitem__)
    #best_fitness = fitnesses[best[0]] 
    elapsed = time.time() - GlobalVars.StartTime
    Models[0].template.printMessage(f"Elapse time = {elapsed / 60:.1f} minutes \n")
    Models[0].template.printMessage(f"Best overall fitness = {best_fitness:4f}, model {best_model.modelNum}")
    with open(os.path.join(model_template.homeDir, "finalControlFile.mod"), 'w') as control:
        control.write(best_model.control)
    resultFilePath = os.path.join(model_template.homeDir, "finalresultFile.lst")
    with open(resultFilePath, 'w') as result:
        result.write(GlobalVars.BestModelOutput)
    Models[0].template.printMessage(f"Final outout from best model is in {resultFilePath}")
    model_template.printMessage(f"Unique model list in  {GlobalVars.SavedModelsFile}") 
    Models = None  # free up memory??   stil not working
    gc.collect()
    return best_model
