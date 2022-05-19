import re
import os
import shutil
import shlex
import xmltodict
import concurrent.futures
from pharmpy.modeling import read_model   # v 0.66.0 works
from typing import OrderedDict
import psutil
from os.path import exists
from subprocess import DEVNULL, STDOUT, Popen, PIPE
import time
import glob
from copy import copy
import gc
import sys

import darwin.utils as utils
import darwin.GlobalVars as GlobalVars

from .Template import Template
from .ModelCode import ModelCode
from .Omega_utils import set_omega_bands, insert_omega_block


class Model:
    """The full model, used for GA, GP, RF, GBRF and exhaustive
    inheirates the Template object"""

    def __init__(self, template: Template, code: ModelCode, model_num: int, is_ga: bool,
                 generation=None):  # for ga, code is full GA/DEAP individual, with fitness
        """code is a model_code object, type defines whether it is full binary (for GA), minimal binary (for downhill)
        or integer.
        makecontrol always used intcode"""
        self.ofv = self.crash = None
        self.dataset_path = None
        self.template = template
        self.source = "new"  # new if new run, "saved" if from saved model, will be no results and no output file - consider saving output file?
        self.generation = generation
        # get model number and phenotype
        self.modelNum = model_num
        self.errMsgs = []
        self.model_code = copy(code)
        self.RSTDOUT = self.RSTDERR = self.NMSTDOUT = self.NMSTDERR = None  # standard output and standard error from NONMEM run
        # all required representations of model are done here
        # GA -> integer,
        # integer is just copied
        # minimal binary is generated, just in case this is a downhill step
        self.success = self.covariance = self.correlation = False
        self.OMEGA = self.SIGMA = None
        self.post_run_Rtext = self.post_run_Pythontext = self.NMtranMSG = self.PRDERR = ""
        # self.Rfuture = None # hold future for running R code
        self.fitness = self.template.options['crash_value']
        self.post_run_Pythonpenalty = self.post_run_Rpenalty = self.Condition_num_test = self.condition_num = 0
        self.num_THETAs = self.num_non_fixed_THETAs = self.num_OMEGAs = self.num_non_fixed_OMEGAs = self.num_SIGMAs = self.num_non_fixed_SIGMAs = self.ofv = 0
        self.jsonListRecord = None  # this is a list of key values to be saved to json file, for subsequent runs and to avoid running the same mdoel
        self.Num_noninfluential_tokens = 0  # home many tokens, due to nesting have a parameter that doesn't end up in the control file?
        self.token_Non_influential = [True] * len(
            self.template.tokens)  # does each token result in a change? does it containt a parameter, if token has a parameter, but doesn't
        # default is true, will change to false if: 1. doesn't contain parameters (in check_contains_parms) is put into control file (in utils.replaceTokens)
        self.startTime = time.time()
        self.elapseTime = None
        self.filestem = None
        self.outputFileName = None
        self.runDir = None
        self.phenotype = None
        self.xml_file = None
        self.control = None
        self.controlFileName = None
        self.cltFileName = None
        self.datafile_name = None
        self.executableFileName = None
        self.status = "Not Started"

    def make_copy(self):
        newmodel = Model(self.template, self.model_code, self.modelNum, self.template.isGA, self.generation)
        newmodel.fitness = self.fitness
        newmodel.ofv = self.ofv
        newmodel.condition_num = self.condition_num
        newmodel.control = copy(self.control)
        newmodel.controlFileName = copy(self.controlFileName)
        newmodel.Condition_num_test = copy(self.Condition_num_test)
        newmodel.correlation = copy(self.correlation)
        newmodel.covariance = copy(self.covariance)
        newmodel.datafile_name = copy(self.datafile_name)
        newmodel.elapseTime = copy(self.elapseTime)
        newmodel.errMsgs = copy(self.errMsgs)
        newmodel.executableFileName = copy(self.executableFileName)
        newmodel.generation = self.generation
        newmodel.modelNum = self.modelNum
        newmodel.jsonListRecord = copy(self.jsonListRecord)
        newmodel.NMtranMSG = copy(self.NMtranMSG)
        newmodel.errMsgs = copy(self.errMsgs)
        newmodel.filestem = copy(self.filestem)
        newmodel.outputFileName = self.outputFileName
        newmodel.num_non_fixed_THETAs = self.num_non_fixed_THETAs
        newmodel.num_THETAs = self.num_THETAs
        newmodel.num_OMEGAs = self.num_OMEGAs
        newmodel.num_SIGMAs = self.num_SIGMAs
        newmodel.phenotype = copy(self.phenotype)
        newmodel.post_run_Rpenalty = copy(self.post_run_Rpenalty)
        newmodel.post_run_Rtext = copy(self.post_run_Rtext)
        newmodel.post_run_Pythonpenalty = copy(self.post_run_Pythonpenalty)
        newmodel.post_run_Pythonpenalty = copy(self.post_run_Pythonpenalty)
        newmodel.token_Non_influential = copy(self.token_Non_influential)
        newmodel.runDir = copy(self.runDir)
        newmodel.status = "Done"
        newmodel.success = copy(self.success)
        newmodel.xml_file = copy(self.xml_file)
        return newmodel

    def __del__(self):
        gc.collect()

    def files_present(self):
        """is the data file specified in the control file present? """
        # make sure control file is there:
        count = 0
        file_exists = exists(self.controlFileName)
        while not file_exists and count < 20:
            time.sleep(0.1)
            count += 1
            file_exists = exists(self.controlFileName)
        if not file_exists:
            self.template.printMessage("Cannot find " + self.controlFileName + " to check for data file")
        else:
            result = read_model(self.controlFileName)
            try:
                if hasattr(result, "dataset_path"):
                    self.dataset_path = result.dataset_path
                else:
                    self.dataset_path = result._read_dataset_path()
                if not exists(self.dataset_path):
                    self.template.printMessage(
                        f"!!!!!Data set for FIRST MODEL {self.dataset_path} seems to be missing, exiting at {time.asctime()}")
                    sys.exit()
                else:
                    self.template.printMessage(f"Data set for FIRST MODEL ONLY {self.dataset_path} was found")
            except:
                self.template.printMessage(f"Unable to check if data set is present with current version of NONMEM")

        # check nmfe?
        if not exists(self.template.options['nmfePath']):
            self.template.printMessage(
                f"NMFE path {self.template.options['nmfePath']} seems to be missing, exiting at {time.asctime()}")
            sys.exit()
        else:
            self.template.printMessage(f"NMFE found at {self.template.options['nmfePath']}")

        if self.template.options['useR']:
            if not exists(self.template.options['RScriptPath']):
                self.template.printMessage(
                    f"RScript.exe path {self.template.options['RScriptPath']} seems to be missing, exiting at {time.asctime()}")
                sys.exit()
            else:
                print(f"RScript.exe found at {self.template.options['RScriptPath']}")

            if not exists(self.template.postRunRCode):
                self.template.printMessage(
                    f"Post Run R code path {self.template.postRunRCode} seems to be missing, exiting at {time.asctime()}")
                sys.exit()
            else:
                self.template.printMessage(f"postRunRCode file found at {self.template.postRunRCode}")
        else:
            self.template.printMessage(
                "Not using PostRun R code")
        return

    def copy_results(self, prevResults):
        try:
            self.fitness = prevResults['fitness']
            self.ofv = prevResults['ofv']
            self.control = prevResults['control']
            self.success = prevResults['success']
            self.covariance = prevResults['covariance']
            self.correlation = prevResults['correlation']
            self.num_THETAs = prevResults['num_THETAs']
            self.num_OMEGAs = prevResults['num_OMEGAs']
            self.num_SIGMAs = prevResults['num_SIGMAs']
            self.condition_num = prevResults['condition_num']
            self.post_run_Rtext = prevResults['post_run_Rtext']
            self.post_run_Rpenalty = prevResults['post_run_Rpenalty']
            self.post_run_Pythontext = prevResults['post_run_Pythontext']
            self.post_run_Pythonpenalty = prevResults['post_run_Pythontext']
            self.NMtranMSG = prevResults['NMtranMSG']
            self.NMtranMSG = "From saved model " + self.NMtranMSG  # ["","","","output from previous model"]
            self.status = "Done"
            return True
        except:
            return False

    def start_model(self):
        self.filestem = 'NMModel_' + str(self.generation) + "_" + str(self.modelNum)
        self.runDir = os.path.join(self.template.homeDir, str(self.generation), str(self.modelNum))
        self.controlFileName = self.filestem + ".mod"
        self.outputFileName = self.filestem + ".lst"
        self.cltFileName = os.path.join(self.runDir, self.filestem + ".clt")
        self.xml_file = os.path.join(self.runDir, self.filestem + ".xml")
        self.executableFileName = self.filestem + ".exe"  # os.path.join(self.runDir,self.filestem +".exe")
        self.make_control()

        # in case the new folder name is a file
        try:
            if os.path.isfile(os.path.join(self.template.homeDir, str(self.generation))) or os.path.islink(
                    os.path.join(self.template.homeDir, str(self.generation))):
                os.unlink(os.path.join(self.template.homeDir, str(self.generation)))
            if os.path.isfile(self.runDir) or os.path.islink(self.runDir):
                os.unlink(self.runDir)
            if not os.path.isdir(self.runDir):
                os.makedirs(self.runDir)
            os.chdir(self.runDir)
        except:
            self.template.printMessage(f"Error removing run files/folders for {self.runDir}, is that file/folder open?")

        for filename in os.listdir(self.runDir):
            file_path = os.path.join(self.runDir, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                self.template.printMessage('Failed to delete %s. Reason: %s' % (self.runDir, e))
        ## check key file, just to make sure

        if os.path.exists(self.controlFileName):
            os.remove(self.controlFileName)
        if os.path.exists(self.outputFileName):
            os.remove(self.outputFileName)
        with open(self.controlFileName, 'w+') as f:
            f.write(self.control)
            f.flush()

        if self.template.isFirstModel:
            self.files_present()
            self.template.isFirstModel = False
            self.template.printMessage("Run Directory for first model is " + self.runDir)
        command = [self.template.options['nmfePath'], self.controlFileName, self.outputFileName,
                   " -nmexec=" + self.executableFileName]
        GlobalVars.UniqueModels += 1

        self.NMProcess = Popen(command, stdout=DEVNULL, stderr=STDOUT)
        self.start = time.time()
        self.status = "Running_NM"

        return

    def decode_r_stdout(self):
        newval = self.RSTDOUT.decode("utf-8").replace("[1]", "").strip()
        # comes back a single string, need to parse by ""
        val = shlex.split(newval)
        self.post_run_Rpenalty = float(val[0])
        # penalty is always first, but may be addition /r/n in array? get the last?
        Num_vals = len(val)
        self.post_run_Rtext = val[Num_vals - 1]

    def start_post_r(self):
        """Run R code specified in the file options['postRunCode'], return penalty from R code
        R is called by subprocess call to Rscript.exe. User must supply path to Rscript.exe
        Presence of Rscript.exe is check in the files_present"""

        command = [self.template.options['RScriptPath'], self.template.postRunRCode]
        os.chdir(self.runDir)  # just to make sure it is reading the right data
        try:
            self.RProcess = Popen(command, stdout=PIPE, stderr=PIPE)
        except:
            self.post_run_Rpenalty = self.template.options['crash_value']
        return

    def check_done_post_run_python(self):
        if self.future.done():
            try:
                self.post_run_Pythonpenalty, self.post_run_Pythontext = self.template.python_postprocess()
                self.status = "Done"
                with open(os.path.join(self.runDir, self.outputFileName), "a") as f:
                    f.write(f"Post run Python code Penalty = {str(self.post_run_Pythonpenalty)}\n")
                    f.write(f"Post run Python code text = {str(self.post_run_Pythontext)}\n")
                return True
            except:
                self.post_run_Pythonpenalty = self.template.options['crash_value']
                self.status = "Done"
                with open(os.path.join(self.runDir, self.outputFileName), "a") as f:
                    print("!!!Post run Python code crashed in " + self.runDir)
                    f.write("Post run Python code crashed\n")
                return True
            finally:
                self.status = "Done"
        else:
            return False

    def check_done_post_run_r(self):
        """Is the post run call (R only for now) done?"""
        try:
            count = 0
            if self.RProcess.poll() is None:
                return False
            else:
                while self.RSTDOUT is None and count < 100:
                    self.RSTDOUT, self.RSTDERR = self.RProcess.communicate()
                    time.sleep(0.1)
                    count += 1
                self.decode_r_stdout()
                self.RProcess = None
                gc.collect()

                with open(os.path.join(self.runDir, self.outputFileName), "a") as f:
                    f.write(f"Post run R code Penalty = {str(self.post_run_Rpenalty)}\n")
                    f.write(f"Post run R code text = {str(self.post_run_Rtext)}\n")
                if count >= 99:
                    self.post_run_Rpenalty = self.template.options['crash_value']
                    self.template.printMessage("!!!Post run R code failed return a value in " + self.runDir)
                return True
        except:
            self.post_run_Rpenalty = self.template.options['crash_value']
            self.template.printMessage("!!!Post run R code crashed in " + self.runDir)
            with open(os.path.join(self.runDir, self.outputFileName), "a") as f:
                f.write("Post run R code crashed\n")
            return True

    def start_post_python(self):
        with concurrent.futures.ThreadPoolExecutor() as executor:  # each model object has it's own future for running user defined python code
            self.future = executor.submit(self.template.python_postprocess)
        self.status = "Running_post_Pythoncode"

    def check_all_done(self):
        """Check is the model is done running, uses the Process of the object. Process.poll() return of 107 or 110
        seems to mean failed to start. Process.poll() of 0 is finished
        if done, reads xml file to collect results, then either calls run_post_Code (if applicable)
        or calls calcFitness."""
        if self.status == "Done":  # if done here, then already has post run code results
            return True
        if self.status == "Running_NM":
            if not self.NMProcess.poll() is None:  ## == 107 or self.NMProcess.poll() == 110 or self.NMProcess.poll() == 0:
                # done with NM, create the model here, before any post code run
                # GET NMTRAN MESSAGES HERE, THEY WON'T SHOW UP IN XML FILE IF NONMEM CRASHES
                self.NMProcess = None
                ## see url https://github.com/python/cpython/issues/72352
                ## https://stackoverflow.com/questions/16341047/how-to-clean-up-subprocess-popen-instances-upon-process-termination
                gc.collect()
                self.status = "Done_running_NM"
            else:  # has timeout expired?
                end = time.time()
                if (end - self.start) > int(self.template.options['timeout_sec']):
                    self.template.printMessage(f"Allowed NM run time exceeded, model in {self.runDir}")
                    try:
                        p = psutil.Process(self.NMProcess.pid)
                        p.terminate()
                        self.NMProcess = None
                        gc.collect()
                    except:
                        pass
                    finally:
                        self.status = "Done_running_NM"
                else:
                    return False

        if self.status == "Done_running_NM":
            if self.template.options['useR']:
                self.start_post_r()
                self.status = "Running_post_Rcode"
                return False
            else:
                self.status = "Done_running_PostR"
        if self.status == "Running_post_Rcode":
            if self.check_done_post_run_r():
                self.status = "Done_running_PostR"
            else:
                return False
        if self.status == "Done_running_PostR":
            if self.template.options['usePython']:
                self.start_post_python()
                self.status = "Running_post_Pythoncode"
                return False
            else:
                self.status = "Done"
        if self.status == "Running_post_Pythoncode":
            if self.check_done_post_run_python():
                self.status = "Done"
            else:
                return False
        if self.status == "Done":
            self.calc_fitness()
            return True

    def read_xml(self):
        if not exists(self.xml_file):
            self.ofv = self.template.options['crash_value']
            self.success = False
            self.covariance = False
            self.correlation = False
            self.condition_num = self.template.options['crash_value']
            self.Condition_num_test = False
            self.num_THETAs = self.num_non_fixed_THETAs = self.num_OMEGAs = self.num_non_fixed_OMEGAs = self.num_SIGMAs = self.num_non_fixed_SIGMAs = 99
            return ()
        else:
            try:
                with open(self.xml_file) as xml_file:
                    # ofv, success, covariance
                    data_dict = xmltodict.parse(xml_file.read())
                    if self.template.version is None:
                        self.template.version = data_dict['nm:output']['nm:nonmem']['@nm:version']  # string
                        print("NONMEM version = " + self.template.version)
                        # keep first two digits
                        dots = [_.start() for _ in re.finditer("\.", self.template.version)]
                        # and get the first two
                        majorversion = float(self.template.version[:dots[1]])  # float
                        if majorversion < 7.4 or majorversion > 7.5:
                            print("NONMEM is version "
                                  + self.template.version + ", NONMEM 7.4 and 7.5 are supported, exiting")
                            sys.exit()

                            # if 0 in problem_dict: # more than one problem, e.g. with simulation
                # it seems that if there is only one problem, this is orderedDict
                # is multiple problems, is just a plain list, if > 0, assume the FIRST IS THE $EST

                problem_dict = data_dict['nm:output']['nm:nonmem']['nm:problem']
                problem_options = dict()
                if "nm:problem_options" in problem_dict[0]:
                    problem_options = problem_dict[0]['nm:problem_options']
                else:
                    if "nm:problem_options" in problem_dict:
                        problem_options = problem_dict['nm:problem_options']
                    else:  # unable to read
                        self.ofv = self.template.options['crash_value']
                        self.success = False
                        self.covariance = False
                        self.correlation = False
                        self.Condition_num_test = False
                        self.condition_num = self.template.options['crash_value']
                        self.PRDERR += " .xml file not present, perhaps crash in estimation step"
                        self.num_non_fixed_THETAs = self.num_non_fixed_OMEGAs = self.num_SIGMAs = 99
                        self.num_THETAs = self.num_OMEGAs = self.num_SIGMAs = 99
                ## read omegas from clt file, any non zero diagonals are estimated
                parms_file = open(self.cltFileName, "r")
                lines = parms_file.readlines()
                parms_file.close()
                # fixed format, parse on space
                parm_names = lines[1].split()
                num_parms = len(parm_names)
                for this_row in range(2, num_parms + 2):
                    currow = lines[this_row].split()
                    if "THETA" in parm_names[this_row - 2]:
                        self.num_THETAs += 1
                        if float(currow[-1]) != 0.000000:
                            self.num_non_fixed_THETAs += 1
                        continue
                    if "OMEGA" in parm_names[this_row - 2]:
                        self.num_OMEGAs += 1  # total size of OMEGA
                        if float(currow[-1]) != 0.000000:
                            self.num_non_fixed_OMEGAs += 1
                        continue
                    if "SIGMA" in parm_names[this_row - 2]:
                        self.num_SIGMAs += 1
                        if float(currow[-1]) != 0.000000:
                            self.num_non_fixed_SIGMAs += 1
                        continue
                self.num_THETAs = int(problem_options['@nm:nthetat'])
                self.num_SIGMAs = int(problem_options['@nm:sigma_diagdim'])
                if isinstance(problem_dict, list):
                    problem_dict = problem_dict[0]
                estimations = problem_dict['nm:estimation']

                # similar, may be more than one estimation, if > 1, we want the final one
                if isinstance(estimations, list):  # > 1 one $EST
                    n_estimation = len(estimations)
                    last_estimation = estimations[n_estimation - 1]
                else:
                    last_estimation = estimations
                # ofv, success, covariance
                self.ofv = float(last_estimation['nm:final_objective_function'])
                if last_estimation['nm:termination_status'] == '0':
                    self.success = True
                else:
                    self.success = False
                if last_estimation['nm:covariance_status']['@nm:error'] == '0':
                    self.covariance = True
                else:
                    self.covariance = False
                corr_data = last_estimation['nm:correlation']["nm:row"]
                num_rows = len(corr_data)
                self.correlation = True
                for this_row in range(1, num_rows):
                    thisrow = corr_data[this_row]['nm:col'][:-1]
                    # get abs
                    absfunction = lambda t: abs(t) > self.template.options['correlationLimit']
                    thisrow = [absfunction(float(x['#text'])) for x in thisrow]
                    if any(thisrow):
                        self.correlation = False
                        break
                if 'nm:eigenvalues' in last_estimation:
                    # if last_estimation['nm:eigenvalues'] is None:
                    Eigens = last_estimation['nm:eigenvalues']['nm:val']
                    max = -9999999
                    min = 9999999
                    for i in Eigens:
                        val = float(i['#text'])
                        if val < min: min = val
                        if val > max: max = val
                    self.condition_num = max / min
                    if self.condition_num > 1000:  # should 1000 be an option??
                        self.Condition_num_test = False
                    else:
                        self.Condition_num_test = True
                else:
                    self.condition_num = self.template.options['crash_value']
                    self.Condition_num_test = False
            except:
                self.ofv = self.template.options['crash_value']
                self.success = False
                self.covariance = False
                self.correlation = False
                self.Condition_num_test = False
                self.condition_num = self.template.options['crash_value']
                self.PRDERR += " .xml file not present, likely crash in estimation step"

        gc.collect()
        return ()

    def get_nmtran_msgs(self):
        self.NMtranMSG = ""
        try:
            if (os.path.exists(os.path.join(self.runDir, "FMSG"))):
                with open(os.path.join(self.runDir, "FMSG"), 'r') as file:
                    # to do remove all empty (\n) lines
                    msg = file.readlines()
                warnings = [' (WARNING  31) $OMEGA INCLUDES A NON-FIXED INITIAL ESTIMATE CORRESPONDING TO\n',
                            ' (WARNING  41) NON-FIXED PARAMETER ESTIMATES CORRESPONDING TO UNUSED\n',
                            ' (WARNING  40) $THETA INCLUDES A NON-FIXED INITIAL ESTIMATE CORRESPONDING TO\n']
                shortwarnings = ['NON-FIXED OMEGA ', 'NON-FIXED PARAMETER ', 'NON-FIXED THETA']
                for thiswarning, thisshortwarning in zip(warnings, shortwarnings):
                    if thiswarning in msg:
                        self.NMtranMSG += thisshortwarning
            if (os.path.exists(os.path.join(self.runDir, "PRDERR"))):
                with open(os.path.join(self.runDir, "PRDERR"), 'r') as file:
                    msg = file.readlines()
                warnings = ['PK PARAMETER FOR',
                            'IS TOO CLOSE TO AN EIGENVALUE',
                            'F OR DERIVATIVE RETURNED BY PRED IS INFINITE (INF) OR NOT A NUMBER (NAN)']
                for thiswarning in warnings:
                    for thisline in msg:
                        if thiswarning in thisline and not (thisline.strip() + " ") in self.PRDERR:
                            self.PRDERR += thisline.strip() + " "
            errors = [' AN ERROR WAS FOUND IN THE CONTROL STATEMENTS.']
            # if an error is found, print out the rest of the text immediately, and add to errors
            for thiserror in errors:
                if thiserror in msg:
                    startline = 0
                    for thisline in msg:
                        if thiserror in thisline:  # printout rest of text
                            error_text = ""
                            full_error_text = msg[startline:]
                            for error_line in full_error_text:
                                error_text = error_text + ", " + error_line
                            self.template.printMessage(
                                "!!!ERROR in Model " + str(self.modelNum) + ", " + error_text + "!!!")
                            self.NMtranMSG += error_text
                            break
                        else:
                            startline += 1
            if self.NMtranMSG == "":
                self.NMtranMSG = "No important warnings"
        except:
            self.NMtranMSG = "FMSG file not found"

            return
            ## try to sort relevant message?
            # key are (WARNING  31) - non fixed OMEGA and (WARNING  41) non fixed parameter and (WARNING  40) non fixed theta

    def get_prderr(self):
        try:
            if (os.path.exists(os.path.join(self.runDir, "PRDERR"))):
                with open(os.path.join(self.runDir, "PRDERR"), 'r') as file:
                    msg = file.readlines()
                warnings = ['PK PARAMETER FOR',
                            'IS TOO CLOSE TO AN EIGENVALUE',
                            'F OR DERIVATIVE RETURNED BY PRED IS INFINITE (INF) OR NOT A NUMBER (NAN)']
                for thiswarning in warnings:
                    for thisline in msg:
                        if thiswarning in thisline and not (thisline.strip() + " ") in self.PRDERR:
                            self.PRDERR += thisline.strip() + " "
        except:
            pass
        return

    def calc_fitness(self):
        """calculates the fitness, based on the model output, and the penalties (from the options file)
        need to look in output file for parameter at boundary and parameter non positive """

        try:
            self.get_nmtran_msgs()  # read from FMSG, in case run fails, will still have NMTRAN messages
            # self.get_PRDERR()
            self.read_xml()
            # self.get_results_pharmpy() # only for num fixed theta, omega etc, get the rest directly from the xml file
            if (self.ofv == None):
                self.fitness = self.template.options['crash_value']
                return
            else:
                self.fitness = self.ofv
                # non influential tokens penalties
                self.fitness += self.Num_noninfluential_tokens * self.template.options['non_influential_tokens_penalty']
                self.ofv = min(self.ofv, self.template.options['crash_value'])
        except:
            self.fitness = self.template.options['crash_value']
            return

        try:
            if not self.success:
                self.fitness += self.template.options['covergencePenalty']

            if not self.covariance:  # covariance_step['completed'] != True:
                self.fitness += self.template.options['covariancePenalty']
                self.fitness += self.template.options['correlationPenalty']
                self.fitness += self.template.options['conditionNumberPenalty']
            else:
                if not self.correlation:
                    self.fitness += self.template.options['correlationPenalty']
                if not self.Condition_num_test:  #
                    self.fitness += self.template.options['conditionNumberPenalty']
                    ## parsimony penalties

            self.fitness += self.num_non_fixed_THETAs * self.template.options['THETAPenalty']
            self.fitness += self.num_OMEGAs * self.template.options['OMEGAPenalty']
            self.fitness += self.num_SIGMAs * self.template.options['SIGMAPenalty']
        except:
            self.fitness = self.template.options['crash_value']

        if self.template.options['useR']:
            try:
                self.fitness += self.post_run_Rpenalty
            except:
                self.fitness = self.template.options['crash_value']

        if self.template.options['usePython']:
            try:
                self.fitness += self.post_run_Pythonpenalty
            except:
                self.fitness = self.template.options['crash_value']

        if self.fitness > self.template.options['crash_value']:
            self.fitness = self.template.options['crash_value']
            # save results
            # write to output
        output = open(self.outputFileName, "a")
        output.write(f"OFV = {self.ofv}\n")
        output.write(f"success = {self.success}\n")
        output.write(f"covariance = {self.covariance}\n")
        output.write(f"correlation = {self.correlation}\n")
        output.write(f"Condition # = {self.condition_num}\n")
        output.write(f"Num Non fixed THETAs = {self.num_non_fixed_THETAs}\n")
        output.write(f"Num Non fixed OMEGAs = {self.num_non_fixed_OMEGAs}\n")
        output.write(f"Num Non fixed SIGMAs = {self.num_non_fixed_SIGMAs}\n")
        output.flush()
        output.close()

        self.make_json_list()

        return

    def make_json_list(self):
        """assembles what goes into the JSON file of saved models"""
        self.jsonListRecord = {"control": self.control, "fitness": self.fitness, "ofv": self.ofv,
                               "success": self.success, "covariance": self.covariance,
                               "post_run_Rtext": self.post_run_Rtext, "post_run_Rpenalty": self.post_run_Rpenalty,
                               "post_run_Pythontext": self.post_run_Pythontext,
                               "post_run_Pythonpenalty": self.post_run_Pythonpenalty,
                               "correlation": self.correlation, "num_THETAs": self.num_THETAs,
                               "num_non_fixed_THETAs": self.num_non_fixed_THETAs,
                               "num_non_fixed_OMEGAs": self.num_non_fixed_OMEGAs,
                               "num_non_fixed_SIGMAs": self.num_non_fixed_SIGMAs,
                               "num_OMEGAs": self.num_OMEGAs, "num_SIGMAs": self.num_SIGMAs,
                               "condition_num": self.condition_num,
                               "NMtranMSG": self.NMtranMSG}
        return

    def cleanup(self):
        """deletes all unneeded files after run
        no argument, no return value """
        try:
            del self.NMProcess
        except:
            self.template.printMessage(f"failed to delete process for {self.modelNum}")

        if self.source == "saved":
            self.template.printMessage(f"called clean up for saveed model, # {self.modelNum}")
            return  # ideally shouldn't be called for saved models, but just in case

        try:
            os.chdir(self.template.homeDir)
        except OSError as e:
            self.template.printMessage(f"OS Error {e} in call to cleanup")

        try:
            if self.template.options['remove_run_dir'] == "True":
                try:
                    if os.path.isdir(self.runDir):
                        shutil.rmtree(self.runDir)
                except OSError:
                    self.template.printMessage("Cannot remove folder {self.runDir} in call to cleanup")
            else:
                file_to_delete = [self.filestem + ".ext",
                                  self.filestem + ".clt",
                                  self.filestem + ".coi",
                                  self.filestem + ".cor",
                                  self.filestem + ".cov",
                                  self.filestem + ".cpu",
                                  self.filestem + ".grd",
                                  self.filestem + ".phi",
                                  self.filestem + ".shm",
                                  self.filestem + ".smt",
                                  self.filestem + ".shk",
                                  self.filestem + ".rmt",
                                  self.executableFileName,
                                  "PRSIZES.F90",
                                  "ifort.txt",
                                  "nmpathlist.txt",
                                  "nmprd4p.mod",
                                  "INTER"]
                file_to_delete = file_to_delete + glob.glob('F*') + glob.glob('W*.*') + glob.glob('*.lnk')
                for f in file_to_delete:
                    try:
                        os.remove(os.path.join(self.runDir, f))
                    except OSError:
                        pass
            if os.path.isdir(os.path.join(self.runDir, "temp_dir")):
                shutil.rmtree(os.path.join(self.runDir, "temp_dir"))
        except OSError as e:
            self.template.printMessage(f"OS Error {e}")

        return

    def check_contains_parms(self):
        """ looks at a token set to see if it contains and OMEGA/SIGMA/THETA/ETA/EPS or ERR, if so it is influential. If not (
            e.g., the token is empty) it is non-influential"""
        tokensetNum = 0
        for thisKey in self.template.tokens.keys():
            tokenSet = self.template.tokens.get(thisKey)[self.phenotype[thisKey]]
            isinfluential = False
            for thistoken in tokenSet:

                trimmedtoken = utils.removeComments(thistoken)
                if "THETA" in trimmedtoken or "OMEGA" in trimmedtoken or "SIGMA" in trimmedtoken or "ETA(" in trimmedtoken or "EPS(" in trimmedtoken or "ERR(" in trimmedtoken:
                    isinfluential = True
                    break
            self.token_Non_influential[
                tokensetNum] = isinfluential  # doesn't containt parm, so can't contribute to non-influential count

            tokensetNum += 1
        return

    def make_control(self):
        """constructs control file from intcode
        ignore last value if self_search_omega_bands """
        # this appears to be OK with search_omega_bands
        self.phenotype = OrderedDict(zip(self.template.tokens.keys(), self.model_code.IntCode))
        self.check_contains_parms()  # fill in whether any token in each token set contains THETA,OMEGA SIGMA

        anyFound = True  # keep looping, looking for nested tokens
        self.control = self.template.TemplateText
        token_found = False  # error check to see if any tokens are present
        for _ in range(3):  # always need 2, and won't do more than 2, only support 1 level of nested loops
            anyFound, self.control = utils.replaceTokens(self.template.tokens, self.control, self.phenotype,
                                                         self.token_Non_influential)
            self.Num_noninfluential_tokens = sum(self.token_Non_influential)
            token_found = token_found or anyFound

        if anyFound:
            self.template.printMessage(
                "It appears that there is more than one level of nested tokens, only one level is supported, exiting")
            raise RuntimeError("Is there more than 1 level of nested tokens??")

        self.control = utils.matchTHETAs(self.control, self.template.tokens, self.template.varTHETABlock,
                                         self.phenotype, self.template.lastFixedTHETA)
        self.control = utils.matchRands(self.control, self.template.tokens, self.template.varOMEGABlock, self.phenotype,
                                        self.template.lastFixedETA, "ETA")
        self.control = utils.matchRands(self.control, self.template.tokens, self.template.varSIGMABlock, self.phenotype,
                                        self.template.lastFixedEPS, "EPS")
        if self.template.isGA or self.template.isPSO:
            self.control += "\n ;; Phenotype \n ;; " + str(self.phenotype) + "\n;; Genotype \n ;; " + str(
                self.model_code.FullBinCode) + \
                            "\n;; Num influential tokens = " + str(self.token_Non_influential)
        else:
            self.control += "\n ;; Phenotype \n ;; " + str(self.phenotype) + "\n;; code \n ;; " + str(
                self.model_code.IntCode) + \
                            "\n;; Num Non influential tokens = " + str(self.token_Non_influential)
        # add band OMEGA
        if self.template.search_omega_band:
            ## bandwidth must be last gene
            bandwidth = self.model_code.IntCode[-1]
            omega_block, self.template.search_omega_band = set_omega_bands(self.control, bandwidth)
            if self.template.search_omega_band:
                self.control = insert_omega_block(self.control, omega_block)
        if not (token_found):
            self.template.printMessage("No tokens found, exiting")
            self.errMsgs.append("No tokens found")

        return
